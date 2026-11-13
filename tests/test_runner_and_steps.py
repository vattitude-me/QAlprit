"""End-to-end unit tests for all step types, flow expansion, teardown, retries, and reports."""

from __future__ import annotations

import json
from pathlib import Path

from qalprit.config_loader import DeviceProfile, EnvironmentConfig, ScreensCatalog
from qalprit.env_loader import EnvContext
from qalprit.report.generator import generate_reports
from qalprit.results import write_results_json
from qalprit.runner import TestRunner
from qalprit.spec_loader import FlowSpec, TestSpec, normalize_step
from tests.fake_driver import FakeDriver


def _make_screens() -> ScreensCatalog:
    return ScreensCatalog(
        {
            "onboarding": {"dismiss_banner": {"accessibility_id": "dismiss_banner"}},
            "login": {
                "username_field": {"accessibility_id": "username_field"},
                "password_field": {"accessibility_id": "password_field", "sensitive": True},
                "login_button": {"accessibility_id": "login_button"},
            },
            "accounts": {
                "account_list": {
                    "android": {"id": "${ANDROID_PACKAGE}:id/account_list"},
                    "ios": {"accessibility_id": "account_list"},
                },
                "account_row": {
                    "android": {"text": "{name}"},
                    "ios": {"accessibility_id": "account_row_{name}"},
                },
                "balance_label": {"accessibility_id": "balance_label"},
                "scroll_container": {"accessibility_id": "scroll_container"},
            },
            "nav": {
                "logout_button": {"accessibility_id": "logout_button"},
                "optional_banner": {"accessibility_id": "optional_banner"},
            },
        }
    )


def _make_env() -> tuple[EnvironmentConfig, EnvContext]:
    env_cfg = EnvironmentConfig(
        android_package="com.bank.qa",
        ios_bundle_id="com.bank.qa",
        default_timeout=0.4,
    )
    env_ctx = EnvContext(
        variables={
            "QA_USER": "qa_alice",
            "QA_PASS": "UltraSecretPass999",
            "QA_TOTP_SECRET": "JBSWY3DPEHPK3PXP",
        }
    )
    env_ctx.register_sensitive_keys()
    return env_cfg, env_ctx


def test_all_steps_flow_expansion_and_secret_redaction_in_reports(tmp_path: Path) -> None:
    screens = _make_screens()
    env_cfg, env_ctx = _make_env()
    profile = DeviceProfile(
        platform="android",
        target_type="simulator",
        device_name="mbtest",
    )

    driver = FakeDriver(platform="android")
    driver.add_element("accessibility_id", "username_field")
    pw_el = driver.add_element(
        "accessibility_id",
        "password_field",
        rect={"x": 40, "y": 120, "width": 200, "height": 40},
    )
    driver.secret_paint_boxes.append(pw_el.rect)
    driver.add_element("accessibility_id", "login_button")
    # Delayed element tests auto-wait readiness
    driver.appear_after("id", "com.bank.qa:id/account_list", calls=2)
    driver.add_element("accessibility_id", "balance_label", text="$1,250.00")
    driver.add_element("accessibility_id", "scroll_container")
    driver.add_element("accessibility_id", "logout_button")
    # Row appears only after a swipe!
    def _on_swipe(drv: FakeDriver) -> None:
        drv.add_element("text", "Checking", text="Checking")

    driver.on_swipe_hook = _on_swipe
    # Banner disappears for assert_not_visible
    driver.add_element("accessibility_id", "dismiss_banner")
    driver.disappear_after[("accessibility_id", "dismiss_banner")] = 2

    flow = FlowSpec(
        name="auth/login",
        params=["username", "password"],
        steps=[
            normalize_step({"launch": "app"}),
            normalize_step({"type": {"target": "login.username_field", "value": "${username}"}}),
            normalize_step(
                {"type": {"target": "login.password_field", "value": "${password}", "secret": True}}
            ),
            normalize_step({"tap": "login.login_button"}),
            normalize_step({"assert_visible": "accounts.account_list"}),
        ],
        source_path=tmp_path / "flows" / "login.md",
    )

    spec = TestSpec(
        id="ALL-STEPS-001",
        title="Exercise full step vocabulary",
        tags=["smoke", "login"],
        platforms=["android", "ios"],
        reset="clear_data",
        steps=[
            normalize_step(
                {
                    "run_flow": {
                        "flow": "auth/login",
                        "params": {"username": "${QA_USER}", "password": "${QA_PASS}"},
                    }
                }
            ),
            normalize_step({"tap_if_visible": {"target": "nav.optional_banner", "timeout": 0.05}}),
            normalize_step({"assert_not_visible": {"target": "onboarding.dismiss_banner", "timeout": 0.5}}),
            normalize_step({"wait_for_visible": "accounts.balance_label"}),
            normalize_step({"assert_text": {"target": "accounts.balance_label", "contains": "$1,250"}}),
            normalize_step({"clear": "login.username_field"}),
            normalize_step({"scroll_until_visible": {"target": "accounts.account_row", "params": {"name": "Checking"}, "max_swipes": 3}}),
            normalize_step({"tap": {"target": "accounts.account_row", "params": {"name": "Checking"}}}),
            normalize_step({"swipe": {"direction": "left", "target": "accounts.scroll_container"}}),
            normalize_step({"background": 1}),
            normalize_step({"biometric": "enroll"}),
            normalize_step({"biometric": "match"}),
            normalize_step({"wait": 0.01}),
            normalize_step("hide_keyboard"),
            normalize_step("back"),
            normalize_step({"screenshot": "after_checks"}),
            normalize_step({"screenshot": {"name": "hidden_screen", "capture": "off"}}),
            normalize_step("reset_app"),
        ],
        teardown=[normalize_step({"tap_if_visible": "nav.logout_button"})],
        expected="All steps execute deterministically.",
        source_path=tmp_path / "specs" / "all.md",
    )

    run_dir = tmp_path / "results" / "run1"
    runner = TestRunner(
        driver_factory=lambda _aa: driver,
        profile=profile,
        profile_name="android_emulator",
        env_name="qa",
        env_cfg=env_cfg,
        env_ctx=env_ctx,
        screens=screens,
        flows={"auth/login": flow},
        run_dir=run_dir,
        run_id="run1",
        poll_interval=0.01,
    )
    res = runner.run([spec])
    assert res.summary.total == 1
    assert res.summary.passed == 1
    assert res.summary.failed == 0

    # Verify capture: off did not write hidden_screen.png
    assert not (run_dir / "screenshots" / "ALL-STEPS-001" / "attempt_1" / "steps_hidden_screen.png").exists()
    # Verify explicit screenshot exists
    assert (run_dir / "screenshots" / "ALL-STEPS-001" / "attempt_1" / "steps_after_checks.png").exists()

    # Verify flow steps recorded origin
    flow_steps = [s for s in res.tests[0].steps if s.flow_name == "auth/login"]
    assert len(flow_steps) == 5

    # Write results.json + report.html + summary.md and verify secret does not leak
    json_path = write_results_json(res, run_dir / "results.json", masker=env_ctx.masker)
    html_path, md_path = generate_reports(res, run_dir, masker=env_ctx.masker)
    for artifact in (json_path, html_path, md_path):
        txt = artifact.read_text(encoding="utf-8")
        assert "UltraSecretPass999" not in txt
    assert driver.quit_called


def test_failure_skips_remaining_steps_but_executes_teardown_and_captures_page_source(tmp_path: Path) -> None:
    screens = _make_screens()
    env_cfg, env_ctx = _make_env()
    profile = DeviceProfile(platform="android", target_type="simulator", device_name="mbtest")
    driver = FakeDriver(platform="android")
    driver.set_page_source("<hierarchy><android.webkit.WebView /></hierarchy>")
    logout_el = driver.add_element("accessibility_id", "logout_button")

    spec = TestSpec(
        id="FAIL-001",
        title="Fails mid-test and runs teardown",
        reset="none",
        steps=[
            normalize_step({"launch": "app"}),
            normalize_step({"tap": "login.login_button"}),  # missing -> fails after timeout
            normalize_step({"screenshot": "never_reached"}),
        ],
        teardown=[normalize_step({"tap": "nav.logout_button"})],
        source_path=tmp_path / "fail.md",
    )

    run_dir = tmp_path / "results" / "fail_run"
    runner = TestRunner(
        driver_factory=lambda _aa: driver,
        profile=profile,
        env_name="qa",
        env_cfg=env_cfg,
        env_ctx=env_ctx,
        screens=screens,
        flows={},
        run_dir=run_dir,
        run_id="fail_run",
        poll_interval=0.01,
    )
    res = runner.run([spec])
    assert res.summary.failed == 1
    assert res.summary.webview_tests == 1
    t = res.tests[0]
    assert t.status == "failed"
    assert t.webview_detected is True
    assert logout_el.clicks == 1  # Teardown ran!
    assert t.steps[1].status == "failed"
    assert t.steps[1].page_source_path is not None
    assert (run_dir / t.steps[1].page_source_path).exists()
    assert t.steps[2].status == "skipped"
    assert t.steps[3].phase == "teardown"
    assert t.steps[3].status == "passed"


def test_real_device_gates_biometric_and_retries_flag_flaky(tmp_path: Path) -> None:
    screens = _make_screens()
    env_cfg, env_ctx = _make_env()

    # 1. Real device skips biometric/simulator spec cleanly
    real_device_profile = DeviceProfile(platform="ios", target_type="device", device_name="iPhone")
    drv1 = FakeDriver(platform="ios")
    bio_spec = TestSpec(
        id="BIO-SKIP",
        title="Biometric",
        targets=["simulator"],
        steps=[normalize_step({"biometric": "match"})],
        source_path=tmp_path / "bio.md",
    )
    runner1 = TestRunner(
        driver_factory=lambda _aa: drv1,
        profile=real_device_profile,
        env_name="qa",
        env_cfg=env_cfg,
        env_ctx=env_ctx,
        screens=screens,
        flows={},
        run_dir=tmp_path / "r1",
        run_id="r1",
        poll_interval=0.01,
    )
    res1 = runner1.run([bio_spec])
    assert res1.summary.skipped == 1
    assert res1.tests[0].status == "skipped"

    # 2. Retry turns attempt-1 fail + attempt-2 pass into flaky=True
    sim_profile = DeviceProfile(platform="android", target_type="simulator", device_name="mbtest")
    drv2 = FakeDriver(platform="android")
    attempts_seen = 0

    def _flaky_factory(_aa: bool | None) -> FakeDriver:
        return drv2

    flaky_spec = TestSpec(
        id="FLAKY-1",
        title="Flaky test",
        reset="reinstall",
        steps=[normalize_step({"tap": "login.login_button"})],
        source_path=tmp_path / "flaky.md",
    )

    orig_remove = drv2.remove_app

    def _on_remove(app_id: str) -> None:
        nonlocal attempts_seen
        orig_remove(app_id)
        attempts_seen += 1
        if attempts_seen >= 2:
            drv2.add_element("accessibility_id", "login_button")

    drv2.remove_app = _on_remove  # type: ignore[method-assign]

    runner2 = TestRunner(
        driver_factory=_flaky_factory,
        profile=sim_profile,
        env_name="qa",
        env_cfg=env_cfg,
        env_ctx=env_ctx,
        screens=screens,
        flows={},
        run_dir=tmp_path / "r2",
        run_id="r2",
        app_path=tmp_path / "app.apk",
        retries=1,
        poll_interval=0.01,
    )
    res2 = runner2.run([flaky_spec])
    assert res2.summary.passed == 1
    assert res2.summary.flaky == 1
    assert res2.tests[0].flaky is True
    assert len(res2.tests[0].attempts) == 2

    # Verify external-assets report links to PNG instead of base64
    write_results_json(res2, tmp_path / "r2" / "results.json")
    html_ext, _ = generate_reports(res2, tmp_path / "r2", external_assets=True)
    html_text = html_ext.read_text(encoding="utf-8")
    assert "data:image/" not in html_text
    assert "screenshots/FLAKY-1/attempt_2/" in html_text
    parsed_json = json.loads((tmp_path / "r2" / "results.json").read_text(encoding="utf-8"))
    assert parsed_json["summary"]["flaky"] == 1
