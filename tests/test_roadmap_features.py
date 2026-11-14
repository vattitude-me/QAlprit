"""Comprehensive offline unit tests covering QAlprit roadmap M1-M11 and G1-G9 features."""

from __future__ import annotations

import io
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from PIL import Image, ImageDraw

from qalprit.appium_manager import AppiumManager
from qalprit.cli import main as cli_main
from qalprit.config_loader import DeviceProfile, EnvironmentConfig, LocatorEntry, ScreensCatalog
from qalprit.driver_factory import AppiumDriverAdapter
from qalprit.env_loader import EnvContext, SecretMasker
from qalprit.evidence import EvidenceCollector, _redact_sensitive_xml
from qalprit.inspector import extract_interactive_elements
from qalprit.report.generator import (
    _safe_resolve_within,
    generate_junit_xml,
    generate_reports,
    generate_summary_markdown,
)
from qalprit.results import (
    FAILURE_CATEGORIES,
    TRANSIENT_FAILURE_CATEGORIES,
    AttemptResult,
    RunResult,
    RunSummary,
    StepResult,
    TestResult,
    classify_failure,
    load_results_json,
    write_results_json,
)
from qalprit.runner import TestRunner
from qalprit.spec_loader import TestSpec, normalize_step
from qalprit.steps import execute_step
from qalprit.steps.base import StepContext, StepFailureError, StepTimeoutError
from qalprit.ui_server import collect_workspace_overview, compare_runs
from qalprit.validator import validate_project
from qalprit.visual_diff import compare_with_baseline
from tests.fake_driver import FakeDriver, FakeStaleElementReferenceError

REPO_ROOT = Path(__file__).resolve().parent.parent


def _make_png(color: tuple[int, int, int] = (20, 40, 80), size: tuple[int, int] = (120, 160)) -> bytes:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_minimal_project(tmp_path: Path) -> Path:
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "screens").mkdir(parents=True, exist_ok=True)
    (tmp_path / "flows" / "auth").mkdir(parents=True, exist_ok=True)
    (tmp_path / "specs" / "smoke").mkdir(parents=True, exist_ok=True)
    (tmp_path / "results").mkdir(parents=True, exist_ok=True)

    (tmp_path / ".env").write_text("QA_USER=alice\nQA_PASS=SuperSecret#123\n", encoding="utf-8")
    (tmp_path / "config" / "devices.yaml").write_text(
        "android_emulator:\n  platform: android\n  target_type: simulator\n  device_name: Pixel 8\n",
        encoding="utf-8",
    )
    (tmp_path / "config" / "environments.yaml").write_text(
        "qa:\n  android_package: com.example.qa\n  ios_bundle_id: com.example.qa\n  default_timeout: 0.3\n",
        encoding="utf-8",
    )
    (tmp_path / "screens" / "screens.yaml").write_text(
        "login:\n"
        "  username_field:\n"
        "    accessibility_id: username\n"
        "  password_field:\n"
        "    accessibility_id: password\n"
        "    sensitive: true\n"
        "  submit_button:\n"
        "    accessibility_id: submit\n",
        encoding="utf-8",
    )
    return tmp_path


def _make_step_context(
    tmp_path: Path,
    driver: FakeDriver,
    *,
    screens: ScreensCatalog | None = None,
    update_baselines: bool = False,
) -> StepContext:
    env_cfg = EnvironmentConfig(
        android_package="com.bank.qa",
        ios_bundle_id="com.bank.qa",
        default_timeout=0.35,
        poll_interval=0.02,
    )
    env_ctx = EnvContext(variables={"QA_USER": "alice", "QA_PASS": "UltraSecret#9"})
    env_ctx.register_sensitive_keys()
    catalog = screens or ScreensCatalog(
        {
            "auth": {
                "submit_btn": {"accessibility_id": "submit_btn"},
                "status_lbl": {"accessibility_id": "status_lbl"},
                "password_box": {"accessibility_id": "password_box", "sensitive": True},
                "card_canvas": {"accessibility_id": "card_canvas"},
            }
        }
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    evidence = EvidenceCollector(
        run_dir=run_dir,
        screens=catalog,
        platform=driver.platform,
        env_ctx=env_ctx,
        extra_env_vars=env_cfg.as_env_vars(),
        masker=env_ctx.masker,
    )
    profile = DeviceProfile(platform=driver.platform, target_type="simulator", device_name="test-dev")
    return StepContext(
        driver=driver,
        platform=driver.platform,
        target_type="simulator",
        profile=profile,
        env_cfg=env_cfg,
        env_ctx=env_ctx,
        screens=catalog,
        evidence=evidence,
        masker=env_ctx.masker,
        app_id="com.bank.qa",
        app_path=None,
        reset_mode="none",
        reset_fn=lambda _mode: None,
        poll_interval=0.02,
        run_dir=run_dir,
        update_baselines=update_baselines,
    )


# ---------------------------------------------------------------------------
# 1. Failure taxonomy & schema v1.0 -> v1.1 migration
# ---------------------------------------------------------------------------


def test_failure_taxonomy_and_v1_0_migration() -> None:
    assert "assertion_failure" in FAILURE_CATEGORIES
    assert "locator_not_found" in TRANSIENT_FAILURE_CATEGORIES
    assert "assertion_failure" not in TRANSIENT_FAILURE_CATEGORIES

    cases = [
        (StepFailureError("text mismatch", failure_category="assertion_failure"), "assert_text", "assertion_failure"),
        (StepTimeoutError("not found", failure_category="locator_not_found"), "tap", "locator_not_found"),
        (StepTimeoutError("disabled", failure_category="element_not_interactable"), "tap", "element_not_interactable"),
        (StepTimeoutError("wait_for_keyboard timed out", failure_category="wait_timeout"), "wait_for_keyboard", "wait_timeout"),
        (TimeoutError("stale element reference"), "tap", "element_not_interactable"),
        (RuntimeError("Unable to launch com.demo"), "launch", "app_launch_failure"),
        (RuntimeError("session terminated by driver"), "tap", "appium_session_failure"),
        (RuntimeError("adb device offline"), "tap", "device_unavailable"),
        (KeyError("Missing required environment variable 'QA_PASS'"), "type", "environment_configuration"),
        (RuntimeError("instrumentation process fatal exception"), "tap", "application_failure"),
        (RuntimeError("completely unexpected bug"), "wait", "unknown"),
    ]
    for exc, action, expected_cat in cases:
        cat, exc_type = classify_failure(exc, step_action=action)
        assert cat == expected_cat
        assert exc_type == type(exc).__name__

    # Backward-compatible v1.0 loading on the repository's sample results.json
    sample_json = REPO_ROOT / "examples" / "mda-sample-run" / "results.json"
    migrated = load_results_json(sample_json)
    assert migrated.schema_version in ("1.0", "1.1")
    assert len(migrated.tests) == 5
    assert all(step.phase in ("setup", "steps", "teardown") for t in migrated.tests for step in t.steps)


# ---------------------------------------------------------------------------
# 2. Reporting, JUnit XML, Markdown escaping, Path Traversal & Corrupt PNG
# ---------------------------------------------------------------------------


def test_reports_junit_markdown_escaping_and_asset_safety(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_01"
    shots = run_dir / "screenshots" / "T1" / "attempt_1"
    shots.mkdir(parents=True, exist_ok=True)

    # Corrupt PNG bytes on disk + out-of-tree symlink + traversal path
    bad_png = shots / "bad.png"
    bad_png.write_bytes(b"not-a-valid-png")
    outside = tmp_path / "secret.png"
    outside.write_bytes(_make_png())
    evil_link = shots / "escape.png"
    evil_link.symlink_to(outside)

    assert _safe_resolve_within(run_dir, "../secret.png") is None
    assert _safe_resolve_within(run_dir, "screenshots/T1/attempt_1/escape.png") is None
    assert _safe_resolve_within(run_dir, "screenshots/T1/attempt_1/bad.png") == bad_png.resolve()

    masker = SecretMasker({"TokenSecret!99"})
    run_res = RunResult(
        run_id="run_01",
        tool_version="0.1.1",
        started_at="2026-11-13T00:00:00Z",
        finished_at="2026-11-13T00:00:05Z",
        environment="qa",
        platform="android",
        device_profile="android_emulator",
        device_name="Pixel 8",
        target_type="simulator",
        suite="smoke",
        summary=RunSummary(total=2, passed=0, failed=1, skipped=1, duration_ms=5000),
        tests=[
            TestResult(
                id="T-PIPE|1",
                title="Login | Header\nMultiline",
                status="failed",
                failure_category="assertion_failure",
                failure_reason="Saw TokenSecret!99 | bad\nline",
                duration_ms=2500,
                steps=[
                    StepResult(
                        index=1,
                        phase="steps",
                        action="assert_text",
                        description="Check label with TokenSecret!99",
                        status="failed",
                        error="Bad value TokenSecret!99 | line2",
                        screenshot_path="screenshots/T1/attempt_1/bad.png",
                    )
                ],
                attempts=[
                    AttemptResult(
                        attempt=1,
                        status="failed",
                        failure_category="assertion_failure",
                        failure_reason="Saw TokenSecret!99",
                    )
                ],
            ),
            TestResult(
                id="T-SKIP",
                title="Skipped spec",
                status="skipped",
                failure_reason="Requires simulator",
                duration_ms=0,
            ),
        ],
    )

    md = generate_summary_markdown(run_res, masker=masker)
    assert "TokenSecret!99" not in md
    assert "Login \\| Header Multiline" in md
    assert "| `T-PIPE\\|1` |" in md

    junit_path = generate_junit_xml(run_res, run_dir / "junit.xml", masker=masker)
    junit_text = junit_path.read_text(encoding="utf-8")
    assert "TokenSecret!99" not in junit_text
    root = ET.fromstring(junit_text)
    assert root.attrib["tests"] == "2"
    assert root.attrib["failures"] == "1"
    assert root.attrib["skipped"] == "1"

    html_ext, _ = generate_reports(run_res, run_dir, external_assets=True, masker=masker, home_url="/")
    ext_text = html_ext.read_text(encoding="utf-8")
    assert "data:image/" not in ext_text
    assert "https://vattitude.ca" in ext_text

    html_emb, _ = generate_reports(run_res, run_dir, external_assets=False, masker=masker)
    emb_text = html_emb.read_text(encoding="utf-8")
    assert "TokenSecret!99" not in emb_text
    assert "Unreadable screenshot image:" in emb_text


# ---------------------------------------------------------------------------
# 3. Config single-strategy locator validation & text selector escaping
# ---------------------------------------------------------------------------


def test_locator_single_strategy_validator_and_text_escaping() -> None:
    with pytest.raises(ValueError, match="competing strategies"):
        LocatorEntry(accessibility_id="login_btn", xpath="//android.widget.Button")

    android_adapter = AppiumDriverAdapter(raw_driver=object(), platform="android")
    _, and_val = android_adapter._to_by_and_val("text", 'Say "Hi" \\ now')
    assert and_val == 'new UiSelector().text("Say \\"Hi\\" \\\\ now")'

    ios_adapter = AppiumDriverAdapter(raw_driver=object(), platform="ios")
    _, ios_val = ios_adapter._to_by_and_val("text", "O'Brien \\ test")
    assert ios_val == "label == 'O\\'Brien \\\\ test' OR name == 'O\\'Brien \\\\ test' OR value == 'O\\'Brien \\\\ test'"


# ---------------------------------------------------------------------------
# 4. Expanded step vocabulary, wait diagnostics & stale element 1x retry
# ---------------------------------------------------------------------------


def test_new_wait_assert_steps_diagnostics_and_stale_retry(tmp_path: Path) -> None:
    driver = FakeDriver(platform="android")
    submit = driver.add_element("accessibility_id", "submit_btn", text="Continue", enabled=False)
    driver.add_element("accessibility_id", "status_lbl", text="Order #4821 Confirmed")
    ctx = _make_step_context(tmp_path, driver)

    # Assert initially disabled, then flip enabled and verify wait_for_enabled + assert_enabled
    execute_step(ctx, normalize_step({"assert_disabled": "auth.submit_btn"}))
    submit.enabled = True
    execute_step(ctx, normalize_step({"wait_for_enabled": {"target": "auth.submit_btn", "timeout": 0.2}}))
    execute_step(ctx, normalize_step({"assert_enabled": "auth.submit_btn"}))

    # wait_for_text & assert_text with regex matches
    execute_step(
        ctx,
        normalize_step({"wait_for_text": {"target": "auth.status_lbl", "matches": r"Order #\d+ Confirmed", "timeout": 0.2}}),
    )
    execute_step(
        ctx,
        normalize_step({"assert_text": {"target": "auth.status_lbl", "matches": r"#\d+"}}),
    )

    # wait_for_keyboard
    driver.keyboard_shown = True
    execute_step(ctx, normalize_step({"wait_for_keyboard": {"visible": True, "timeout": 0.2}}))
    driver.keyboard_shown = False
    execute_step(ctx, normalize_step({"wait_for_keyboard": {"visible": False, "timeout": 0.2}}))

    # swipe with duration and distance
    execute_step(ctx, normalize_step({"swipe": {"direction": "down", "duration": 250, "distance": 0.4}}))
    swipe_call = [c for c in driver.calls if c[0] == "swipe"][-1][1]
    assert swipe_call[-1] == 250

    # Stale element automatic 1x retry on is_displayed and click
    stale_btn = driver.add_element("accessibility_id", "submit_btn")
    submit.stale_once = True
    submit.stale_on_action = True
    execute_step(ctx, normalize_step({"tap": "auth.submit_btn"}))
    assert submit.clicks == 1 or stale_btn.clicks == 1

    # Rich diagnostics on wait failure
    submit.enabled = False
    stale_btn.enabled = False
    with pytest.raises(StepTimeoutError) as disabled_err:
        execute_step(
            ctx,
            normalize_step({"wait_for_enabled": {"target": "auth.submit_btn", "timeout": 0.06}}),
        )
    assert disabled_err.value.failure_category == "wait_timeout"
    assert disabled_err.value.diagnostics["target"] == "auth.submit_btn"
    assert disabled_err.value.diagnostics["last_state"] == "disabled"
    assert disabled_err.value.diagnostics["polls"] >= 1


# ---------------------------------------------------------------------------
# 5. SecretMasker, Fail-Safe Secure Evidence, XML Redaction & Log Scrubbing
# ---------------------------------------------------------------------------


def test_secret_masker_secure_evidence_xml_and_log_scrubbing(tmp_path: Path) -> None:
    masker = SecretMasker()
    masker.add("on")
    masker.add("true")
    masker.add("ab")
    masker.add("Pass&123/xyz")
    assert masker.mask("status=on&flag=true&ab=1") == "status=on&flag=true&ab=1"
    masked = masker.mask("raw=Pass&123/xyz html=Pass&amp;123/xyz url=Pass%26123%2Fxyz")
    assert "Pass" not in masked

    # secure_evidence=True omits unredacted PNG when sensitive bounds are invalid (0x0)
    driver = FakeDriver(platform="android")
    driver.add_element(
        "accessibility_id",
        "password_box",
        rect={"x": 10, "y": 20, "width": 0, "height": 0},
    )
    ctx = _make_step_context(tmp_path, driver)
    res = ctx.evidence.capture_screenshot_detailed(driver, "step_01_pw")
    assert res.rel_path is None
    assert res.redaction_status == "skipped_secure_mode"
    assert res.warning
    assert not (ctx.run_dir / "screenshots" / "step_01_pw.png").exists()

    # _redact_sensitive_xml scrubs sensitive node attributes and inner text
    xml = (
        '<hierarchy>'
        '<EditText content-desc="password_box" text="UltraSecret#9" value="UltraSecret#9">UltraSecret#9</EditText>'
        '<TextView content-desc="status_lbl" text="Welcome"/>'
        '</hierarchy>'
    )
    scrubbed_xml = _redact_sensitive_xml(xml, ctx.screens, "android", ctx.env_ctx, {})
    assert "UltraSecret#9" not in scrubbed_xml
    assert SecretMasker.REPLACEMENT in scrubbed_xml
    assert "Welcome" in scrubbed_xml

    # AppiumManager.__exit__ scrubs secrets from appium.log
    log_path = tmp_path / "appium.log"
    log_path.write_text("typed text: Pass&123/xyz into app\n", encoding="utf-8")
    mgr = AppiumManager(log_path=log_path, masker=masker)
    mgr.__exit__(None, None, None)
    log_after = log_path.read_text(encoding="utf-8")
    assert "Pass&123/xyz" not in log_after
    assert SecretMasker.REPLACEMENT in log_after


# ---------------------------------------------------------------------------
# 6. Runner: Reset Failure, Transient-Only Retries, Setup Phase & Interrupt
# ---------------------------------------------------------------------------


def _build_runner_and_env(tmp_path: Path, driver_factory: Any, *, retries: int = 0) -> tuple[TestRunner, ScreensCatalog]:
    screens = ScreensCatalog(
        {
            "auth": {
                "submit_btn": {"accessibility_id": "submit_btn"},
                "status_lbl": {"accessibility_id": "status_lbl"},
            }
        }
    )
    env_cfg = EnvironmentConfig(
        android_package="com.bank.qa",
        ios_bundle_id="com.bank.qa",
        default_timeout=0.06,
        poll_interval=0.02,
    )
    env_ctx = EnvContext(variables={})
    profile = DeviceProfile(platform="android", target_type="simulator", device_name="Pixel 8")
    run_dir = tmp_path / "results" / "run_test"
    runner = TestRunner(
        driver_factory=driver_factory,
        profile=profile,
        profile_name="android_emulator",
        env_name="qa",
        env_cfg=env_cfg,
        env_ctx=env_ctx,
        screens=screens,
        flows={},
        run_dir=run_dir,
        run_id="run_test",
        app_path=None,
        suite="smoke",
        retries=retries,
    )
    return runner, screens


def test_runner_reset_failure_transient_retries_setup_and_interrupt(tmp_path: Path) -> None:
    # A. Failed clear_data reset aborts attempt immediately with app_launch_failure
    class BrokenResetDriver(FakeDriver):
        def execute_script(self, script: str, *args: Any) -> Any:
            if script == "mobile: clearApp":
                raise RuntimeError("clearApp permission denied")
            return super().execute_script(script, *args)

    runner_a, _ = _build_runner_and_env(tmp_path / "a", lambda _aa: BrokenResetDriver())
    spec_a = TestSpec(
        id="RST-1",
        title="Reset fails cleanly",
        reset="clear_data",
        steps=[normalize_step({"wait": 0.01})],
        source_path=tmp_path / "rst.md",
    )
    res_a = runner_a.run([spec_a])
    assert res_a.summary.failed == 1
    assert res_a.tests[0].failure_category == "app_launch_failure"
    assert res_a.tests[0].attempts[0].reset_status == "failed"

    # B. Deterministic assertion_failure does NOT retry even with retries=2,
    #    whereas transient wait_timeout retries and succeeds on attempt 2 + executes setup phase.
    class RetryToggleDriver(FakeDriver):
        def __init__(self) -> None:
            super().__init__()
            self.activate_count = 0
            self.add_element("accessibility_id", "submit_btn", text="OK")

        def activate_app(self, app_id: str) -> None:
            super().activate_app(app_id)
            self.activate_count += 1
            # First activate_app is before TRN-1 attempt 1; second is before TRN-1 attempt 2
            if self.activate_count >= 2 and ("accessibility_id", "status_lbl") not in self.elements:
                self.add_element("accessibility_id", "status_lbl", text="Ready")

    shared_drv = RetryToggleDriver()
    runner_b, _ = _build_runner_and_env(tmp_path / "b", lambda _aa: shared_drv, retries=2)
    spec_assert = TestSpec(
        id="DET-1",
        title="Deterministic assertion failure",
        reset="none",
        steps=[normalize_step({"assert_text": {"target": "auth.submit_btn", "equals": "WRONG"}})],
        source_path=tmp_path / "det.md",
    )
    spec_transient = TestSpec(
        id="TRN-1",
        title="Transient locator passes on retry with setup",
        reset="none",
        setup=[normalize_step({"tap": "auth.submit_btn"})],
        steps=[normalize_step({"wait_for_visible": {"target": "auth.status_lbl", "timeout": 0.05}})],
        source_path=tmp_path / "trn.md",
    )
    res_b = runner_b.run([spec_assert, spec_transient])
    t_det, t_trn = res_b.tests
    assert len(t_det.attempts) == 1
    assert t_det.status == "failed"
    assert t_det.failure_category == "assertion_failure"
    assert t_trn.status == "passed"
    assert t_trn.flaky is True
    assert len(t_trn.attempts) == 2
    assert t_trn.attempts[1].retry_reason
    assert any(s.phase == "setup" for s in t_trn.steps)

    # C. KeyboardInterrupt mid-run sets interrupted=True and preserves partial results
    class InterruptOnSecondTestDriver(FakeDriver):
        def __init__(self) -> None:
            super().__init__()
            self.add_element("accessibility_id", "submit_btn")
            self.taps_seen = 0

        def find_elements(self, strategy: str, value: str) -> list[Any]:
            self.taps_seen += 1
            if self.taps_seen >= 2:
                raise KeyboardInterrupt("Ctrl+C")
            return super().find_elements(strategy, value)

    int_drv = InterruptOnSecondTestDriver()
    runner_c, _ = _build_runner_and_env(tmp_path / "c", lambda _aa: int_drv)
    s1 = TestSpec(id="OK-1", title="First passes", reset="none", steps=[normalize_step({"tap": "auth.submit_btn"})], source_path=tmp_path / "ok.md")
    s2 = TestSpec(id="INT-2", title="Interrupted", reset="none", steps=[normalize_step({"tap": "auth.submit_btn"})], source_path=tmp_path / "int.md")
    res_c = runner_c.run([s1, s2])
    assert res_c.interrupted is True
    assert res_c.summary.interrupted == 1
    assert res_c.summary.passed == 1
    assert res_c.summary.total == 2


# ---------------------------------------------------------------------------
# 7. Validator bounds & warnings (M5)
# ---------------------------------------------------------------------------


def test_validator_bounds_regex_and_sensitive_warnings(tmp_path: Path) -> None:
    proj = _make_minimal_project(tmp_path)
    (proj / "flows" / "auth" / "sample.md").write_text(
        "---\n"
        "name: auth/sample\n"
        "params: [user, unused_param]\n"
        "steps:\n"
        "  - type: {target: login.username_field, value: \"${user}\"}\n"
        "---\n",
        encoding="utf-8",
    )
    (proj / "specs" / "smoke" / "bad.md").write_text(
        "---\n"
        "id: BAD-1\n"
        "title: Validator catches bounds and unmasked secret warning\n"
        "retries: 9\n"
        "steps:\n"
        "  - wait_for_visible: {target: login.username_field, timeout: -1}\n"
        "  - assert_text: {target: login.username_field, matches: \"[unclosed\"}\n"
        "  - swipe: {direction: down, distance: 1.5}\n"
        "  - type: {target: login.password_field, value: \"${QA_PASS}\", secret: false}\n"
        "  - run_flow: {flow: auth/sample, params: {user: alice, unused_param: x}}\n"
        "---\n",
        encoding="utf-8",
    )

    val = validate_project(proj, env_name="qa", suite="smoke", app_path=tmp_path / "missing.apk")
    assert not val.ok
    err_msgs = "\n".join(i.message for i in val.errors)
    warn_msgs = "\n".join(i.message for i in val.warnings)
    assert "retries" in err_msgs
    assert "timeout" in err_msgs
    assert "Invalid regex" in err_msgs
    assert "distance" in err_msgs
    assert "App binary not found:" in err_msgs
    assert "Typing into sensitive locator" in warn_msgs
    assert "unused_param" in warn_msgs


# ---------------------------------------------------------------------------
# 8. CLI ergonomics: list, run --dry-run / --id / --exclude-tags / --rerun-failed, inspect --export-json
# ---------------------------------------------------------------------------


def test_cli_list_dry_run_rerun_failed_and_inspect_json(tmp_path: Path) -> None:
    runner = CliRunner()
    # list --json-output against repository mda suite
    res_list = runner.invoke(
        cli_main,
        ["list", "--suite", "mda", "--json-output", "--project-root", str(REPO_ROOT)],
    )
    assert res_list.exit_code == 0, res_list.output
    payload = json.loads(res_list.output)
    ids = {s["id"] for s in payload["specs"]}
    assert "MDA-001" in ids

    # Build isolated project for dry-run and rerun-failed tests
    proj = _make_minimal_project(tmp_path)
    for tid, tags in [("T-A", "[smoke, fast]"), ("T-B", "[smoke, slow]")]:
        (proj / "specs" / "smoke" / f"{tid}.md").write_text(
            f"---\nid: {tid}\ntitle: Test {tid}\ntags: {tags}\nsteps:\n  - tap: login.submit_button\n---\n",
            encoding="utf-8",
        )

    res_dry = runner.invoke(
        cli_main,
        [
            "run",
            "--platform",
            "android",
            "--device",
            "android_emulator",
            "--env",
            "qa",
            "--suite",
            "smoke",
            "--exclude-tags",
            "slow",
            "--dry-run",
            "--project-root",
            str(proj),
        ],
    )
    assert res_dry.exit_code == 0, res_dry.output
    assert "DRY RUN: 1 spec(s)" in res_dry.output
    assert "T-A" in res_dry.output
    assert "T-B" not in res_dry.output

    # Write a prior failed run and verify --rerun-failed only selects T-B
    prev_dir = proj / "results" / "prev_run"
    prev_dir.mkdir(parents=True, exist_ok=True)
    write_results_json(
        RunResult(
            run_id="prev_run",
            tool_version="0.1.1",
            started_at="2026-11-13T00:00:00Z",
            finished_at="2026-11-13T00:00:01Z",
            environment="qa",
            platform="android",
            device_profile="android_emulator",
            device_name="Pixel 8",
            target_type="simulator",
            suite="smoke",
            summary=RunSummary(total=2, passed=1, failed=1),
            tests=[
                TestResult(id="T-A", title="Test T-A", status="passed"),
                TestResult(id="T-B", title="Test T-B", status="failed"),
            ],
        ),
        prev_dir / "results.json",
    )
    res_rerun = runner.invoke(
        cli_main,
        [
            "run",
            "--platform",
            "android",
            "--device",
            "android_emulator",
            "--env",
            "qa",
            "--rerun-failed",
            str(prev_dir),
            "--dry-run",
            "--project-root",
            str(proj),
        ],
    )
    assert res_rerun.exit_code == 0, res_rerun.output
    assert "T-B" in res_rerun.output
    assert "T-A" not in res_rerun.output

    # inspect --from-xml --export-json
    xml_path = tmp_path / "dump.xml"
    xml_path.write_text(
        '<hierarchy><EditText content-desc="password_field" text="Secret9" password="true" clickable="true"/></hierarchy>',
        encoding="utf-8",
    )
    export_path = tmp_path / "out" / "screen.json"
    res_insp = runner.invoke(
        cli_main,
        [
            "inspect",
            "--platform",
            "android",
            "--from-xml",
            str(xml_path),
            "--screen",
            "login",
            "--export-json",
            str(export_path),
        ],
    )
    assert res_insp.exit_code == 0, res_insp.output
    exported = json.loads(export_path.read_text(encoding="utf-8"))
    assert exported["elements"][0]["suggested_sensitive"] is True
    assert "Secret9" not in export_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 9. Visual Diff & Inspector Ranking / Ambiguity
# ---------------------------------------------------------------------------


def test_visual_diff_baseline_match_mismatch_and_masking(tmp_path: Path) -> None:
    base_img = Image.new("RGB", (100, 100), (25, 60, 120))
    buf_base = io.BytesIO()
    base_img.save(buf_base, format="PNG")
    baseline = tmp_path / "baselines" / "home.png"

    # 1. Create baseline
    created = compare_with_baseline(
        buf_base.getvalue(),
        baseline,
        tmp_path / "diffs" / "create.png",
        create_if_missing=True,
    )
    assert created.passed and created.baseline_created
    assert baseline.with_suffix(".meta.json").is_file()

    # 2. Diff with changed top-left box triggers failure & writes diff image
    changed = base_img.copy()
    ImageDraw.Draw(changed).rectangle([0, 0, 39, 39], fill=(240, 20, 20))
    buf_changed = io.BytesIO()
    changed.save(buf_changed, format="PNG")
    diff_png = tmp_path / "diffs" / "fail.png"
    failed = compare_with_baseline(
        buf_changed.getvalue(),
        baseline,
        diff_png,
        threshold=0.02,
    )
    assert not failed.passed
    assert failed.mismatch_ratio > 0.02
    assert diff_png.is_file()

    # 3. Recreate baseline with mask_boxes covering the dynamic clock region -> passes
    masked_res = compare_with_baseline(
        buf_base.getvalue(),
        baseline,
        tmp_path / "diffs" / "mask_update.png",
        mask_boxes=[{"x": 0, "y": 0, "width": 40, "height": 40}],
        update_baseline=True,
    )
    assert masked_res.passed
    masked_match = compare_with_baseline(
        buf_changed.getvalue(),
        baseline,
        tmp_path / "diffs" / "mask_pass.png",
        mask_boxes=[{"x": 0, "y": 0, "width": 40, "height": 40}],
        threshold=0.01,
    )
    assert masked_match.passed and masked_match.mismatch_ratio == 0.0


def test_inspector_ranking_ambiguity_and_sensitivity() -> None:
    xml = (
        '<hierarchy>'
        '<Button content-desc="dup_id" resource-id="com.app:id/btn1" text="Save" clickable="true"/>'
        '<Button content-desc="dup_id" resource-id="com.app:id/btn2" text="Cancel" clickable="true"/>'
        '<Button content-desc="dup_only_acc" clickable="true"/>'
        '<Button content-desc="dup_only_acc" clickable="true"/>'
        '<EditText resource-id="com.app:id/pin_input" text="1234" password="true" clickable="true"/>'
        '<TextView text="JustText" clickable="true"/>'
        '<View clickable="true"/>'
        '</hierarchy>'
    )
    ranked = extract_interactive_elements(xml, platform="android")
    # Ambiguous duplicate accessibility_id drops score (100 - 15 = 85), below unique id (90)!
    first = ranked[0]
    assert first["recommended_strategy"] == "id"
    assert first["recommended_value"] == "com.app:id/btn1"

    # Element with only duplicate accessibility_id is flagged as ambiguous
    dup_acc = [e for e in ranked if e["recommended_value"] == "dup_only_acc"][0]
    assert dup_acc["ambiguous"] is True
    assert any("Duplicate accessibility_id" in w for w in dup_acc["warnings"])

    pin = [e for e in ranked if e["recommended_value"] == "com.app:id/pin_input"][0]
    assert pin["suggested_sensitive"] is True
    assert pin["text"] == SecretMasker.REPLACEMENT


# ---------------------------------------------------------------------------
# 10. UI Server Corrupt Runs & Run Comparison (G4)
# ---------------------------------------------------------------------------


def test_ui_server_corrupt_runs_and_compare_runs(tmp_path: Path) -> None:
    proj = _make_minimal_project(tmp_path)
    results_dir = proj / "results"

    # Corrupt run directory should be recorded without crashing overview
    corrupt_dir = results_dir / "broken_run"
    corrupt_dir.mkdir(parents=True, exist_ok=True)
    (corrupt_dir / "results.json").write_text("{not valid json", encoding="utf-8")

    # Base and target runs for comparison
    base_dir = results_dir / "run_base"
    target_dir = results_dir / "run_target"
    write_results_json(
        RunResult(
            run_id="run_base",
            tool_version="0.1.1",
            started_at="2026-11-13T00:00:00Z",
            finished_at="2026-11-13T00:00:10Z",
            environment="qa",
            platform="android",
            device_profile="android_emulator",
            device_name="Pixel 8",
            target_type="simulator",
            suite="smoke",
            summary=RunSummary(total=3, passed=1, failed=2, duration_ms=10000),
            tests=[
                TestResult(id="T1", title="Regression candidate", status="passed"),
                TestResult(id="T2", title="Fixed candidate", status="failed", failure_category="locator_not_found"),
                TestResult(id="T3", title="Still broken", status="failed", failure_category="assertion_failure"),
            ],
        ),
        base_dir / "results.json",
    )
    write_results_json(
        RunResult(
            run_id="run_target",
            tool_version="0.1.1",
            started_at="2026-11-13T01:00:00Z",
            finished_at="2026-11-13T01:00:12Z",
            environment="qa",
            platform="android",
            device_profile="android_emulator",
            device_name="Pixel 8",
            target_type="simulator",
            suite="smoke",
            summary=RunSummary(total=3, passed=1, failed=2, duration_ms=12500),
            tests=[
                TestResult(id="T1", title="Regression candidate", status="failed", failure_category="assertion_failure"),
                TestResult(id="T2", title="Fixed candidate", status="passed"),
                TestResult(id="T3", title="Still broken", status="failed", failure_category="assertion_failure"),
                TestResult(id="T4", title="New test", status="passed"),
            ],
        ),
        target_dir / "results.json",
    )

    overview = collect_workspace_overview(proj, results_dir=results_dir)
    assert len(overview["runs"]) == 2
    assert len(overview["corrupt_runs"]) == 1
    assert overview["corrupt_runs"][0]["run_id"] == "broken_run"

    diff = compare_runs(results_dir, "run_base", "run_target")
    assert diff["duration_delta_ms"] == 2500
    assert [r["id"] for r in diff["regressions"]] == ["T1"]
    assert [f["id"] for f in diff["fixes"]] == ["T2"]
    assert [p["id"] for p in diff["persistent_failures"]] == ["T3"]
    assert [n["id"] for n in diff["new_tests"]] == ["T4"]
