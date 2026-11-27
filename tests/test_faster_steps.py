"""Faster steps: redaction from one page source read, and the run screenshot setting."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from qalprit.config_loader import DeviceProfile, EnvironmentConfig, ScreensCatalog
from qalprit.env_loader import EnvContext, SecretMasker
from qalprit.evidence import EvidenceCollector
from qalprit.runner import TestRunner, normalize_screenshot_mode, wants_step_screenshot
from qalprit.spec_loader import TestSpec, normalize_step
from tests.fake_driver import FakeDriver


class PageSourceDriver(FakeDriver):
    """A fake that asks for page source lookups, like the Appium adapter on Android."""

    prefer_page_source_lookup = True


ANDROID_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <android.widget.FrameLayout class="android.widget.FrameLayout" bounds="[0,0][390,844]" displayed="true">
    <android.widget.EditText class="android.widget.EditText" resource-id="com.bank:id/pw" bounds="[30,200][150,240]" displayed="true" />
    <android.widget.EditText class="android.widget.EditText" resource-id="card_number" bounds="[30,300][230,340]" displayed="true" />
    <android.view.View class="android.view.View" content-desc="otp_box" bounds="[10,400][110,430]" displayed="true" />
    <android.view.View class="android.view.View" content-desc="hidden_box" bounds="[10,500][110,530]" displayed="false" />
  </android.widget.FrameLayout>
</hierarchy>"""


def _screens() -> ScreensCatalog:
    return ScreensCatalog(
        {
            "login": {
                "password": {"android": {"id": "com.bank:id/pw", "sensitive": True}},
                "card": {"android": {"id": "card_number", "sensitive": True}},
                "otp": {"android": {"accessibility_id": "otp_box", "sensitive": True}},
                "hidden": {"android": {"accessibility_id": "hidden_box", "sensitive": True}},
                "answer": {"android": {"xpath": "//android.widget.EditText[@text='a']", "sensitive": True}},
            }
        }
    )


def _collector(tmp_path: Path, screens: ScreensCatalog) -> EvidenceCollector:
    return EvidenceCollector(
        run_dir=tmp_path,
        screens=screens,
        platform="android",
        env_ctx=EnvContext(),
        extra_env_vars={},
        masker=SecretMasker(),
    )


def test_redaction_reads_page_source_once_and_searches_only_what_it_cannot_match(tmp_path: Path) -> None:
    driver = PageSourceDriver(window_size={"width": 390, "height": 844}, screenshot_scale=2)
    driver.set_page_source(ANDROID_XML)
    xpath_box = {"x": 200, "y": 600, "width": 100, "height": 40}
    driver.add_element("xpath", "//android.widget.EditText[@text='a']", rect=xpath_box)
    for box in (
        {"x": 30, "y": 200, "width": 120, "height": 40},
        {"x": 10, "y": 500, "width": 100, "height": 30},
    ):
        driver.secret_paint_boxes.append(box)

    boxes, invalid, warnings = _collector(tmp_path, _screens())._collect_sensitive_boxes_with_diagnostics(driver)

    assert invalid == 0, warnings
    assert {"x": 30, "y": 200, "width": 120, "height": 40} in boxes  # full resource id
    assert {"x": 30, "y": 300, "width": 200, "height": 40} in boxes  # bare id matches the suffix
    assert {"x": 10, "y": 400, "width": 100, "height": 30} in boxes  # content-desc
    assert xpath_box in boxes  # xpath still found through a device search
    assert {"x": 10, "y": 500, "width": 100, "height": 30} not in boxes  # displayed="false"
    searched = set(driver._lookup_counts)
    assert searched == {("xpath", "//android.widget.EditText[@text='a']")}

    res = _collector(tmp_path, _screens()).capture_screenshot_detailed(driver, "shot")
    assert res.rel_path
    with Image.open(tmp_path / res.rel_path) as img:
        assert img.getpixel((180, 440)) == (0, 0, 0)
        assert img.getpixel((120, 1030)) == (255, 0, 0)  # hidden node keeps its pixels


def test_redaction_falls_back_to_device_searches_when_page_source_is_unreadable(tmp_path: Path) -> None:
    driver = PageSourceDriver()
    driver.set_page_source("<hierarchy><not closed")
    driver.add_element("accessibility_id", "otp_box", rect={"x": 10, "y": 400, "width": 100, "height": 30})
    screens = ScreensCatalog({"login": {"otp": {"android": {"accessibility_id": "otp_box", "sensitive": True}}}})

    boxes, invalid, _ = _collector(tmp_path, screens)._collect_sensitive_boxes_with_diagnostics(driver)

    assert invalid == 0
    assert boxes == [{"x": 10, "y": 400, "width": 100, "height": 30}]
    assert ("accessibility_id", "otp_box") in driver._lookup_counts


def test_zero_size_sensitive_node_still_blocks_the_screenshot_in_secure_mode(tmp_path: Path) -> None:
    driver = PageSourceDriver()
    driver.set_page_source(
        '<hierarchy><android.view.View content-desc="otp_box" bounds="[10,400][10,400]" displayed="true" /></hierarchy>'
    )
    screens = ScreensCatalog({"login": {"otp": {"android": {"accessibility_id": "otp_box", "sensitive": True}}}})

    res = _collector(tmp_path, screens).capture_screenshot_detailed(driver, "shot")

    assert res.rel_path is None
    assert res.redaction_status == "skipped_secure_mode"


def test_appium_adapter_uses_page_source_lookup_on_android_only() -> None:
    from qalprit.driver_factory import AppiumDriverAdapter

    assert AppiumDriverAdapter(object(), "android").prefer_page_source_lookup is True
    assert AppiumDriverAdapter(object(), "ios").prefer_page_source_lookup is False
    assert getattr(FakeDriver(), "prefer_page_source_lookup", False) is False


@pytest.mark.parametrize(
    ("mode", "step_type", "expected"),
    [
        ("all", "assert_visible", True),
        ("all", "tap", True),
        ("actions", "tap", True),
        ("actions", "type", True),
        ("actions", "back", True),
        ("actions", "assert_visible", False),
        ("actions", "wait_for_visible", False),
        ("actions", "screenshot", True),
        ("failures", "tap", False),
        ("failures", "screenshot", True),
        ("failures", "visual_compare", True),
        ("all", "if_visible", False),
    ],
)
def test_wants_step_screenshot(mode: str, step_type: str, expected: bool) -> None:
    assert wants_step_screenshot(mode, step_type) is expected


def test_normalize_screenshot_mode() -> None:
    assert normalize_screenshot_mode(None) == "all"
    assert normalize_screenshot_mode("") == "all"
    assert normalize_screenshot_mode(" Actions ") == "actions"
    with pytest.raises(ValueError, match="Unknown screenshot mode"):
        normalize_screenshot_mode("some")


def _run(tmp_path: Path, mode: str, *, fail: bool = False):
    screens = ScreensCatalog(
        {
            "home": {
                "button": {"accessibility_id": "button"},
                "title": {"accessibility_id": "title"},
                "missing": {"accessibility_id": "missing"},
            }
        }
    )
    driver = FakeDriver()
    driver.add_element("accessibility_id", "button")
    driver.add_element("accessibility_id", "title")
    steps = [
        normalize_step({"tap": "home.button"}),
        normalize_step({"assert_visible": "home.title"}),
        normalize_step({"screenshot": "named"}),
    ]
    if fail:
        steps.append(normalize_step({"assert_visible": {"target": "home.missing", "timeout": 0.05}}))
    spec = TestSpec(id="SHOT-001", title="Screenshot modes", reset="none", steps=steps, source_path=tmp_path / "s.md")
    run_dir = tmp_path / "results" / mode
    runner = TestRunner(
        driver_factory=lambda _aa: driver,
        profile=DeviceProfile(platform="android", target_type="simulator", device_name="t"),
        env_name="qa",
        env_cfg=EnvironmentConfig(default_timeout=0.2),
        env_ctx=EnvContext(),
        screens=screens,
        flows={},
        run_dir=run_dir,
        run_id=mode,
        poll_interval=0.01,
        screenshots=mode,
    )
    res = runner.run([spec])
    return {s.action: s.screenshot_path for s in res.tests[0].steps if s.phase == "steps"}, res


def test_runner_actions_mode_skips_check_step_screenshots(tmp_path: Path) -> None:
    shots, res = _run(tmp_path, "actions")
    assert res.summary.passed == 1
    assert shots["tap"]
    assert shots["assert_visible"] is None
    assert shots["screenshot"]


def test_runner_failures_mode_keeps_failure_and_named_screenshots(tmp_path: Path) -> None:
    _, res = _run(tmp_path, "failures", fail=True)
    steps = [s for s in res.tests[0].steps if s.phase == "steps"]
    by_status = {(s.action, s.status): s.screenshot_path for s in steps}
    assert by_status[("tap", "passed")] is None
    assert by_status[("screenshot", "passed")]
    assert by_status[("assert_visible", "failed")]


def test_runner_default_mode_captures_every_step(tmp_path: Path) -> None:
    shots, _ = _run(tmp_path, "all")
    assert all(shots.values())
