"""Tests for recorder reliability: system prompts, friendly errors, variables on save, iOS mirror."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import qalprit.driver_factory as df
from qalprit.datasets import load_datasets
from qalprit.driver_factory import AppiumDriverAdapter
from qalprit.error_text import clean_error, friendly_error
from qalprit.inspector import InspectedElement
from qalprit.recorder import RecorderManager, find_system_prompt_dismiss, parameterize_typed_steps
from qalprit.spec_loader import load_flows


def _el(text: str, rect: list[int] | None = None, **extra: object) -> dict[str, object]:
    return {"text": text, "rect": rect or [0, 0, 100, 40], "visible": True, **extra}


def test_save_password_sheet_is_detected_with_its_dismiss_button() -> None:
    elements = [
        _el("Login"),
        _el("Save Password?"),
        _el("Save Password"),
        _el("Not Now", [10, 700, 190, 750]),
    ]
    found = find_system_prompt_dismiss(elements)
    assert found is not None
    title, button = found
    assert title == "Save Password?"
    assert button["text"] == "Not Now"


def test_app_screens_are_not_mistaken_for_a_system_prompt() -> None:
    # A login form mentions passwords and has a Cancel button, but asks no save question.
    assert find_system_prompt_dismiss([_el("Password"), _el("Forgot password?"), _el("Cancel")]) is None
    # The question alone is not enough: without a known dismiss button nothing is tapped.
    assert find_system_prompt_dismiss([_el("Save Password?"), _el("Save")]) is None
    # An editable field whose text looks like the question is user data, not a prompt.
    assert find_system_prompt_dismiss([_el("Save password?", editable=True), _el("Not Now")]) is None


def _inspected(**kw: object) -> InspectedElement:
    base: dict[str, object] = {
        "tag": "XCUIElementTypeButton", "accessibility_id": "", "resource_id": "", "text": "",
        "bounds": "", "clickable": True, "enabled": True, "visible": True,
    }
    base.update(kw)
    return InspectedElement(**base)  # type: ignore[arg-type]


def test_only_inputs_are_treated_as_secret() -> None:
    assert _inspected(tag="XCUIElementTypeSecureTextField").suggested_sensitive is True
    assert _inspected(text="Forgot password?").suggested_sensitive is False
    assert _inspected(text="Go Shopping").suggested_sensitive is False
    assert _inspected(accessibility_id="Spinner").suggested_sensitive is False


def test_friendly_error_gives_a_next_step_and_drops_the_stack_trace() -> None:
    raw = "Message: An element could not be located on the page using the given search parameters.\nStacktrace:\n  at foo (bar.js:1)"
    msg = friendly_error(RuntimeError(raw))
    assert "no longer on screen" in msg
    assert "Stacktrace" not in msg and "bar.js" not in msg
    assert "session ended" in friendly_error("invalid session id")
    assert clean_error("Message: boom\nStacktrace:\n at x") == "boom"
    assert len(clean_error("x" * 900)) <= 320


def test_typed_values_become_variables() -> None:
    steps = [
        {"action": "tap", "target": "demo.more"},
        {"action": "type", "target": "demo.text_input", "value": "bob@example.com"},
        {"action": "type", "target": "demo.text_input_2", "value": "10203040", "secret": True},
        {"action": "type", "target": "login.username_field", "value": "alice"},
        {"action": "type", "target": "login.username_field", "value": "alice"},
        {"action": "type", "target": "pay.card_field", "value": "4111", "secret": True},
        {"action": "type", "target": "a.b", "value": "${QA_USER}"},
    ]
    out, variables, secrets, values = parameterize_typed_steps(steps)

    assert variables == ["username", "password", "username_2", "card_password"]
    assert secrets == ["password", "card_password"]
    assert values["password"] == "10203040"
    assert [s.get("value") for s in out[1:]] == [
        "${username}", "${password}", "${username_2}", "${username_2}", "${card_password}", "${QA_USER}",
    ]
    assert steps[2]["value"] == "10203040", "input steps must not be mutated"


def _write_min_project(root: Path) -> None:
    (root / "config").mkdir()
    (root / "config" / "environments.yaml").write_text(
        "environments:\n  qa:\n    android: {app_package: com.x, app_activity: .Main}\n", encoding="utf-8"
    )
    (root / "config" / "devices.yaml").write_text("devices: {}\n", encoding="utf-8")
    for d in ("screens", "flows", "specs"):
        (root / d).mkdir()
    (root / "screens" / "screens.yaml").write_text("screens: {}\n", encoding="utf-8")


def test_save_with_variables_keeps_the_secret_out_of_the_flow_file(tmp_path: Path) -> None:
    _write_min_project(tmp_path)
    rm = RecorderManager()
    rm._project_root = tmp_path
    rm._platform = "ios"
    rm._locators = {
        "demo.user": {"screen": "demo", "key": "user", "strategy": "accessibility_id", "value": "user", "sensitive": False},
        "demo.pass": {"screen": "demo", "key": "pass", "strategy": "accessibility_id", "value": "pass", "sensitive": True},
    }
    rm._steps = [
        {"action": "type", "target": "demo.user", "value": "bob@example.com", "secret": False, "label": "Type"},
        {"action": "type", "target": "demo.pass", "value": "10203040", "secret": True, "label": "Type"},
    ]

    res = rm.save_recorded(flow_name="demo/login", create_test=False, stop_after=False, parameterize=True)

    assert res["variables"] == ["user", "pass"]
    flow_text = (tmp_path / "flows" / "demo" / "login.md").read_text(encoding="utf-8")
    assert "10203040" not in flow_text and "${pass}" in flow_text
    flows = load_flows(tmp_path / "flows")
    flows = flows[0] if isinstance(flows, tuple) else flows
    assert flows["demo/login"].params == ["user", "pass"]
    # Recorded on iOS only, so validation must not ask for Android locators.
    assert flows["demo/login"].platforms == ["ios"]
    from qalprit.validator import validate_project

    report = validate_project(tmp_path, flow_name="demo/login")
    messages = " ".join(str(getattr(i, "message", i)) for i in report.issues)
    assert "platform 'android'" not in messages

    bundle = load_datasets(tmp_path, "flow", "demo/login")
    assert bundle.variables == ["user", "pass"]
    assert "pass" in bundle.secret_columns
    assert [r.name for r in bundle.rows] == ["recorded"]
    assert bundle.rows[0].values == {"user": "bob@example.com", "pass": "10203040"}

    # Recording the flow again must not wipe dataset rows the tester added.
    rm._steps[0]["value"] = "carol@example.com"
    rm.save_recorded(flow_name="demo/login", create_test=False, stop_after=False, parameterize=True)
    assert load_datasets(tmp_path, "flow", "demo/login").rows[0].values["user"] == "bob@example.com"


class _KbElement:
    def __init__(self, displayed: bool) -> None:
        self._displayed = displayed

    def is_displayed(self) -> bool:
        return self._displayed


class _StaleKeyboardDriver:
    """WDA keeps answering "shown" after the keyboard has slid off screen."""

    def __init__(self, displayed: bool) -> None:
        self._displayed = displayed
        self.wda_shots = 0

    def is_keyboard_shown(self) -> bool:
        return True

    def find_elements(self, _by: str, value: str) -> list[_KbElement]:
        return [_KbElement(self._displayed)] if value == "XCUIElementTypeKeyboard" else []

    def get_screenshot_as_png(self) -> bytes:
        self.wda_shots += 1
        return b"\x89PNG-wda"


def test_ios_keyboard_state_follows_the_keyboard_element_not_the_stale_flag() -> None:
    assert AppiumDriverAdapter(_StaleKeyboardDriver(displayed=False), platform="ios").is_keyboard_shown() is False
    assert AppiumDriverAdapter(_StaleKeyboardDriver(displayed=True), platform="ios").is_keyboard_shown() is True
    assert AppiumDriverAdapter(_StaleKeyboardDriver(displayed=False), platform="android").is_keyboard_shown() is True


def test_simulator_screenshot_comes_from_simctl_and_falls_back_to_wda(monkeypatch) -> None:
    calls: list[list[str]] = []
    ok = {"value": True}

    def fake_run(cmd: list[str], **_kw: object) -> SimpleNamespace:
        calls.append(cmd)
        if ok["value"]:
            Path(cmd[-1]).write_bytes(b"\x89PNG-simctl")
            return SimpleNamespace(returncode=0)
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(df, "_macos_xcode_env", lambda: {})
    monkeypatch.setattr(df.subprocess, "run", fake_run)
    raw = _StaleKeyboardDriver(displayed=False)
    adapter = AppiumDriverAdapter(raw, platform="ios", simulator_udid="SIM-1")

    assert adapter.get_screenshot_as_png() == b"\x89PNG-simctl"
    assert calls[0][:5] == ["xcrun", "simctl", "io", "SIM-1", "screenshot"]
    assert raw.wda_shots == 0

    ok["value"] = False
    assert adapter.get_screenshot_as_png() == b"\x89PNG-wda"
    assert adapter.get_screenshot_as_png() == b"\x89PNG-wda"
    n = len(calls)
    assert adapter.get_screenshot_as_png() == b"\x89PNG-wda"
    assert len(calls) == n, "simctl is skipped after two failures in a row"

    # No UDID (real device or Android): never shell out.
    calls.clear()
    assert AppiumDriverAdapter(raw, platform="ios").get_screenshot_as_png() == b"\x89PNG-wda"
    assert calls == []
