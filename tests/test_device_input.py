"""Recording on a USB phone: touch events from `getevent` become replayable steps."""

from __future__ import annotations

from pathlib import Path

import frontmatter

from qalprit.config_loader import DeviceProfile, EnvironmentConfig, ScreensCatalog
from qalprit.device_input import (
    GeteventParser,
    TouchDevice,
    classify_gesture,
    parse_keyboard_top,
    pick_touch_device,
)
from qalprit.env_loader import EnvContext
from qalprit.evidence import EvidenceCollector
from qalprit.recorder import RecorderManager, _step_to_yaml_dict
from qalprit.spec_loader import load_flows, normalize_step
from qalprit.steps import execute_step
from qalprit.steps.base import StepContext
from qalprit.validator import validate_project
from tests.fake_driver import FakeDriver

GETEVENT_LP = """add device 1: /dev/input/event4
  name:     "uinput-fpc"
  events:
    ABS (0003): ABS_X                 : value 0, min 0, max 255, fuzz 0, flat 0, resolution 0
                ABS_Y                 : value 0, min 0, max 255, fuzz 0, flat 0, resolution 0
  input props:
    <none>
add device 2: /dev/input/event2
  name:     "fts_ts"
  events:
    ABS (0003): ABS_MT_SLOT           : value 0, min 0, max 9, fuzz 0, flat 0, resolution 0
                ABS_MT_POSITION_X     : value 0, min 0, max 4095, fuzz 0, flat 0, resolution 0
                ABS_MT_POSITION_Y     : value 0, min 0, max 8191, fuzz 0, flat 0, resolution 0
                ABS_MT_TRACKING_ID    : value 0, min 0, max 65535, fuzz 0, flat 0, resolution 0
  input props:
    INPUT_PROP_DIRECT
add device 3: /dev/input/event0
  name:     "gpio-keys"
  events:
    KEY (0001): KEY_VOLUMEDOWN KEY_VOLUMEUP KEY_POWER
"""

DEV = TouchDevice("/dev/input/event2", 0, 4095, 0, 8191)


def _touch(ts: float, x: int, y: int, dev: str = "/dev/input/event2") -> list[str]:
    return [
        f"[{ts:14.6f}] {dev}: EV_ABS       ABS_MT_TRACKING_ID   0000002a",
        f"[{ts:14.6f}] {dev}: EV_ABS       ABS_MT_POSITION_X    {x:08x}",
        f"[{ts:14.6f}] {dev}: EV_ABS       ABS_MT_POSITION_Y    {y:08x}",
        f"[{ts:14.6f}] {dev}: EV_KEY       BTN_TOUCH            DOWN",
        f"[{ts:14.6f}] {dev}: EV_SYN       SYN_REPORT           00000000",
    ]


def _move(ts: float, x: int, y: int, dev: str = "/dev/input/event2") -> list[str]:
    return [
        f"[{ts:14.6f}] {dev}: EV_ABS       ABS_MT_POSITION_X    {x:08x}",
        f"[{ts:14.6f}] {dev}: EV_ABS       ABS_MT_POSITION_Y    {y:08x}",
        f"[{ts:14.6f}] {dev}: EV_SYN       SYN_REPORT           00000000",
    ]


def _lift(ts: float, dev: str = "/dev/input/event2") -> list[str]:
    return [
        f"[{ts:14.6f}] {dev}: EV_ABS       ABS_MT_TRACKING_ID   ffffffff",
        f"[{ts:14.6f}] {dev}: EV_KEY       BTN_TOUCH            UP",
        f"[{ts:14.6f}] {dev}: EV_SYN       SYN_REPORT           00000000",
    ]


def _feed(parser: GeteventParser, lines: list[str]) -> list[tuple[str, object]]:
    out: list[tuple[str, object]] = []
    for line in lines:
        out.extend(parser.feed(line))
    return out


def _gestures(events: list[tuple[str, object]]) -> list:
    return [g for kind, g in events if kind == "gesture"]


def test_touchscreen_is_picked_over_the_fingerprint_sensor() -> None:
    dev = pick_touch_device(GETEVENT_LP)
    assert dev is not None
    assert dev.path == "/dev/input/event2"
    assert (dev.max_x, dev.max_y) == (4095, 8191)
    assert pick_touch_device("") is None


def test_tap_is_mapped_from_raw_axes_to_screen_pixels() -> None:
    parser = GeteventParser(device=DEV, display_width=1080, display_height=2400)
    events = _feed(parser, _touch(100.0, 2048, 4096) + _lift(100.08))
    assert events[0] == ("down", None), "the finger landing is reported first"
    (tap,) = _gestures(events)
    assert tap.kind == "tap"
    assert abs(tap.x - 540) <= 1 and abs(tap.y - 1200) <= 1
    # A second tap on exactly the same spot sends no new X/Y values; it must still count.
    again = _feed(parser, [
        "[ 101.000000] /dev/input/event2: EV_ABS       ABS_MT_TRACKING_ID   0000002b",
        "[ 101.000000] /dev/input/event2: EV_KEY       BTN_TOUCH            DOWN",
        "[ 101.000000] /dev/input/event2: EV_SYN       SYN_REPORT           00000000",
    ] + _lift(101.05))
    (tap2,) = _gestures(again)
    assert tap2.kind == "tap" and (tap2.x, tap2.y) == (tap.x, tap.y)


def test_hold_swipe_edge_back_and_system_gestures_are_told_apart() -> None:
    parser = GeteventParser(device=DEV, display_width=1080, display_height=2400)
    (hold,) = _gestures(_feed(parser, _touch(1.0, 1000, 3000) + _move(1.5, 1003, 3002) + _lift(1.9)))
    assert hold.kind == "long_press" and hold.duration_ms == 900

    (swipe,) = _gestures(_feed(parser, _touch(2.0, 2048, 6000) + _move(2.1, 2048, 4000) + _move(2.2, 2048, 2000) + _lift(2.25)))
    assert swipe.kind == "swipe" and swipe.direction == "up"
    assert 0.45 <= swipe.distance <= 0.55

    (back,) = _gestures(_feed(parser, _touch(3.0, 20, 4000) + _move(3.1, 1500, 4010) + _lift(3.15)))
    assert back.kind == "back"

    (home,) = _gestures(_feed(parser, _touch(4.0, 2048, 8150) + _move(4.1, 2048, 5000) + _lift(4.15)))
    assert home.kind == "system"


def test_two_finger_gestures_and_other_devices_are_ignored_but_back_key_is_kept() -> None:
    parser = GeteventParser(device=DEV, display_width=1080, display_height=2400)
    pinch = _touch(5.0, 1000, 3000) + [
        "[   5.050000] /dev/input/event2: EV_ABS       ABS_MT_SLOT          00000001",
        "[   5.050000] /dev/input/event2: EV_ABS       ABS_MT_TRACKING_ID   0000002c",
        "[   5.050000] /dev/input/event2: EV_ABS       ABS_MT_POSITION_X    00000a00",
        "[   5.100000] /dev/input/event2: EV_ABS       ABS_MT_TRACKING_ID   ffffffff",
        "[   5.100000] /dev/input/event2: EV_ABS       ABS_MT_SLOT          00000000",
    ] + _lift(5.2)
    assert _gestures(_feed(parser, pinch)) == []

    stylus = _touch(6.0, 10, 10, dev="/dev/input/event9") + _lift(6.1, dev="/dev/input/event9")
    assert _feed(parser, stylus) == []

    keys = _feed(parser, [
        "[   7.000000] /dev/input/event0: EV_KEY       KEY_BACK             DOWN",
        "[   7.050000] /dev/input/event0: EV_KEY       KEY_BACK             UP",
    ])
    assert keys == [("key", "back")]


def test_small_moves_are_taps_and_distance_is_clamped() -> None:
    assert classify_gesture(500, 1000, 510, 1008, 0.1, 1080, 2400).kind == "tap"
    far = classify_gesture(540, 2200, 540, 20, 0.3, 1080, 2400)
    assert far.kind == "swipe" and far.distance == 0.9


def test_keyboard_top_comes_from_the_input_method_window() -> None:
    old_style = "  Window #3 Window{a1 u0 InputMethod}:\n    mFrame=[0,1584][1080,2400] last=[0,1584][1080,2400]\n"
    assert parse_keyboard_top(old_style, 2400) == 1584
    insets = "    frame=[0,74][1080,2400]\n    mGivenContentInsets=[0,1420][0,0] mGivenVisibleInsets=[0,1420][0,0]\n"
    assert parse_keyboard_top(insets, 2400) == 1494
    # Only a full-screen window and no insets: the keys cannot be placed, so say unknown.
    assert parse_keyboard_top("    frame=[0,0][1080,2400]\n", 2400) is None
    assert parse_keyboard_top("", 2400) is None


def _phone_recorder(tmp_path: Path) -> tuple[RecorderManager, FakeDriver]:
    rm = RecorderManager()
    drv = FakeDriver(platform="android", window_size={"width": 1080, "height": 2400}, screenshot_scale=1)
    rm._project_root = tmp_path
    rm._driver = drv
    rm._active = True
    rm._real_device = True
    rm._platform = "android"
    rm._screen_name = "home"
    # Skip the screen refresh; these tests only check which step a gesture becomes.
    rm._capture_snapshot_locked = lambda: None  # type: ignore[method-assign]
    return rm, drv


_ELEMENTS = [
    {"key": "cart_button", "recommended_strategy": "id", "recommended_value": "cart", "display_label": "Cart",
     "rect": [800, 100, 1000, 220], "visible": True},
    {"key": "search_input", "recommended_strategy": "id", "recommended_value": "search", "display_label": "Search",
     "rect": [40, 300, 1040, 420], "visible": True, "editable": True},
    {"key": "item_row", "recommended_strategy": "text", "recommended_value": "Blue shirt", "display_label": "Blue shirt",
     "rect": [40, 1800, 1040, 1950], "visible": True},
]


def test_gestures_on_the_phone_become_steps(tmp_path: Path, monkeypatch) -> None:
    from qalprit import recorder as rec_mod

    monkeypatch.setattr(rec_mod.time, "sleep", lambda *_a: None)
    rm, _drv = _phone_recorder(tmp_path)
    tok = rm._session_token

    def _do(kind: str, x: int = 0, y: int = 0, *, ex: int | None = None, ey: int | None = None,
            dur: float = 0.08, kb: int | None = None) -> None:
        g = classify_gesture(x, y, x if ex is None else ex, y if ey is None else ey, dur, 1080, 2400) if kind == "gesture" else None
        rm._handle_device_gesture(kind, g, (list(_ELEMENTS), "home", kb), token=tok)

    _do("gesture", 900, 160)  # tap Cart
    _do("gesture", 500, 360)  # tap on the search field: the type step taps it, so no tap step
    _do("gesture", 500, 1880, kb=1500)  # a key on the on-screen keyboard, not the row behind it
    _do("gesture", 500, 1880, dur=1.2)  # hold the row
    _do("gesture", 540, 2000, ey=800, dur=0.25)  # scroll the list
    _do("gesture", 500, 2380)  # nothing of the app there (navigation bar)
    _do("back_key")

    yaml_steps = [_step_to_yaml_dict(s) for s in rm._steps]
    assert yaml_steps == [
        {"tap": "home.cart_button"},
        {"long_press": {"target": "home.item_row", "duration": 1200}},
        {"swipe": {"direction": "up", "distance": 0.5}},
        "back",
    ]
    for raw in yaml_steps:  # every recorded step parses back into the step vocabulary
        normalize_step(raw)
    assert "did not land on anything in the app" in rm._notice


def _write_project(root: Path, *, package: str) -> None:
    (root / "config").mkdir()
    android = f"{{app_package: {package}}}" if package else "{}"
    (root / "config" / "environments.yaml").write_text(
        f"environments:\n  qa:\n    android: {android}\n", encoding="utf-8"
    )
    (root / "config" / "devices.yaml").write_text("devices: {}\n", encoding="utf-8")
    for d in ("screens", "flows", "specs"):
        (root / d).mkdir()
    (root / "screens" / "screens.yaml").write_text("screens: {}\n", encoding="utf-8")


def test_a_test_recorded_on_the_phone_replays_without_wiping_it(tmp_path: Path) -> None:
    _write_project(tmp_path, package="")
    rm = RecorderManager()
    rm._project_root = tmp_path
    rm._platform = "android"
    rm._real_device = True
    rm._start_mode = "restart"
    rm._launch_app_id = "com.shop.app"
    rm._locators = {
        "home.item_row": {"screen": "home", "key": "item_row", "strategy": "text", "value": "Blue shirt", "sensitive": False},
    }
    rm._steps = [
        {"action": "swipe", "target": "", "value": "up", "distance": 0.5, "secret": False, "label": "Swipe up"},
        {"action": "long_press", "target": "home.item_row", "value": "900", "secret": False, "label": "Long press"},
    ]
    res = rm.save_recorded(flow_name="shop/browse", suite="smoke", stop_after=False)

    flow = load_flows(tmp_path / "flows")[0]["shop/browse"]
    assert [s.type for s in flow.steps] == ["launch", "swipe", "long_press"]
    assert flow.steps[0].params == {"app": "com.shop.app", "restart": True}
    spec_meta = frontmatter.load(str(tmp_path / res["spec"]["source_path"])).metadata
    assert spec_meta["reset"] == "none", "a replay must keep the login and data on the phone"
    assert "USB device" in (tmp_path / "flows" / "shop" / "browse.md").read_text(encoding="utf-8")
    report = validate_project(tmp_path, env_name="qa")
    assert not [i for i in report.issues if i.level == "error"], [i.format() for i in report.issues]


def test_start_mode_defaults(monkeypatch, tmp_path: Path) -> None:
    """A phone is reopened with its data kept; an emulator keeps the old Fresh app behaviour."""
    from qalprit import recorder as rec_mod

    _write_project(tmp_path, package="com.shop.app")
    monkeypatch.setattr(rec_mod.threading, "Thread", lambda *a, **k: type("T", (), {"start": lambda self: None})())
    rm = RecorderManager()
    rm.start(tmp_path, platform="android", device_profile="live:android:R58M123ABC", env_name="qa")
    assert rm._real_device and rm._start_mode == "restart"
    rm.start(tmp_path, platform="android", device_profile="live:android:R58M123ABC", env_name="qa", start_mode="clear_data")
    assert rm._start_mode == "clear_data"
    rm.start(tmp_path, platform="android", device_profile="live:android:emulator-5554", env_name="qa")
    assert not rm._real_device and rm._start_mode == "clear_data"
    rm.start(tmp_path, platform="android", device_profile="live:android:emulator-5554", env_name="qa", fresh_start=False)
    assert rm._start_mode == "as_is"


def _ctx(tmp_path: Path, driver: object) -> StepContext:
    screens = ScreensCatalog({"home": {"item_row": {"android": {"text": "Blue shirt"}}}})
    env_cfg = EnvironmentConfig(default_timeout=1.0, poll_interval=0.01)
    env_ctx = EnvContext()
    return StepContext(
        driver=driver,
        platform="android",
        target_type="device",
        profile=DeviceProfile(platform="android", target_type="device", device_name="phone"),
        env_cfg=env_cfg,
        env_ctx=env_ctx,
        screens=screens,
        evidence=EvidenceCollector(
            run_dir=tmp_path, screens=screens, platform="android", env_ctx=env_ctx, extra_env_vars={}, masker=env_ctx.masker,
        ),
        masker=env_ctx.masker,
        app_id="",
        app_path=None,
        reset_mode="none",
        reset_fn=lambda _m: (True, ""),
    )


def test_long_press_and_launch_replay(tmp_path: Path) -> None:
    drv = FakeDriver(platform="android")
    drv.add_element("text", "Blue shirt", rect={"x": 40, "y": 1800, "width": 1000, "height": 150})
    ctx = _ctx(tmp_path, drv)

    execute_step(ctx, normalize_step({"launch": {"app": "com.shop.app", "restart": True}}))
    execute_step(ctx, normalize_step({"long_press": {"target": "home.item_row", "duration": 900}}))

    assert drv.calls[:2] == [("terminate_app", "com.shop.app"), ("activate_app", "com.shop.app")]
    script, args = next(c[1] for c in drv.calls if c[0] == "execute_script")
    assert script == "mobile: longClickGesture"
    assert args[0] == {"x": 540, "y": 1875, "duration": 900}
