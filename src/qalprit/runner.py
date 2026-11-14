"""Test suite runner: session lifecycle, between-test reset, step + teardown, retries."""

from __future__ import annotations

import hashlib
import platform as py_platform
import subprocess
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from qalprit import __version__
from qalprit.config_loader import DeviceProfile, EnvironmentConfig, ScreensCatalog
from qalprit.doctor import find_android_tool
from qalprit.env_loader import EnvContext
from qalprit.evidence import EvidenceCollector, ScreenshotCaptureResult
from qalprit.results import (
    TRANSIENT_FAILURE_CATEGORIES,
    AttemptResult,
    RunResult,
    RunSummary,
    StepResult,
    TestResult,
    classify_failure,
)
from qalprit.spec_loader import FlowSpec, StepItem, TestSpec, expand_steps
from qalprit.steps import StepContext, execute_step, format_step_description


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slug(value: str) -> str:
    return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in value)


def _compute_file_sha256(path: Path | None) -> str:
    if path is None or not path.is_file():
        return ""
    try:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


class TestRunner:
    """Executes validated TestSpecs against a DriverProtocol instance."""

    __test__ = False

    def __init__(
        self,
        driver_factory: Callable[[bool | None], Any],
        profile: DeviceProfile,
        env_name: str,
        env_cfg: EnvironmentConfig,
        env_ctx: EnvContext,
        screens: ScreensCatalog,
        flows: dict[str, FlowSpec],
        run_dir: Path,
        run_id: str,
        profile_name: str | None = None,
        app_path: Path | None = None,
        suite: str = "all",
        tags: list[str] | None = None,
        retries: int = 0,
        poll_interval: float | None = None,
        update_baselines: bool = False,
    ) -> None:
        self.driver_factory = driver_factory
        self.profile = profile
        self.profile_name = profile_name or profile.device_name
        self.env_name = env_name
        self.env_cfg = env_cfg
        self.env_ctx = env_ctx
        self.screens = screens
        self.flows = flows
        self.run_dir = run_dir
        self.run_id = run_id
        self.app_path = app_path
        self.suite = suite
        self.tags = tags or []
        self.retries = max(0, retries)
        self.poll_interval = env_cfg.poll_interval if poll_interval is None else poll_interval
        self.update_baselines = update_baselines
        self.app_id = (
            env_cfg.android_package if profile.platform == "android" else env_cfg.ios_bundle_id
        )
        self.evidence = EvidenceCollector(
            run_dir=run_dir,
            screens=screens,
            platform=profile.platform,
            env_ctx=env_ctx,
            extra_env_vars=env_cfg.as_env_vars(),
            masker=env_ctx.masker,
            secure_evidence=env_cfg.secure_evidence,
            save_page_source_on_failure=env_cfg.save_page_source_on_failure,
        )

    def _apply_reset(self, driver: Any, mode: str) -> tuple[bool, str]:
        if not self.app_id:
            return True, ""
        try:
            if mode == "none":
                try:
                    driver.terminate_app(self.app_id)
                except Exception as exc:
                    low = str(exc).lower()
                    if "not running" not in low and "unknown" not in low:
                        return False, self.env_ctx.masker.mask(f"terminate_app failed: {exc}")
                driver.activate_app(self.app_id)
                return True, ""

            if mode == "reinstall":
                try:
                    driver.remove_app(self.app_id)
                except Exception as exc:
                    low = str(exc).lower()
                    if not any(tok in low for tok in ("not installed", "unknown", "does not exist")):
                        return False, self.env_ctx.masker.mask(f"remove_app failed: {exc}")
                if self.app_path is not None:
                    driver.install_app(str(self.app_path))
                driver.activate_app(self.app_id)
                return True, ""

            if mode == "clear_data":
                if self.profile.platform == "android":
                    cleared = False
                    last_clear_err = ""
                    adb_bin = find_android_tool("adb")
                    if adb_bin and not getattr(driver, "is_fake", False):
                        cmd = [adb_bin]
                        if self.profile.udid:
                            cmd.extend(["-s", self.profile.udid])
                        cmd.extend(["shell", "pm", "clear", self.app_id])
                        try:
                            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                            if proc.returncode == 0:
                                cleared = True
                                if self.profile.auto_accept_alerts:
                                    base_cmd = [adb_bin] + (["-s", self.profile.udid] if self.profile.udid else [])
                                    for perm in ("android.permission.POST_NOTIFICATIONS",):
                                        subprocess.run(
                                            base_cmd + ["shell", "pm", "grant", self.app_id, perm],
                                            capture_output=True,
                                            text=True,
                                            timeout=5,
                                        )
                            else:
                                last_clear_err = (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()
                        except Exception as exc:
                            last_clear_err = str(exc)
                    if not cleared:
                        try:
                            driver.execute_script("mobile: clearApp", {"appId": self.app_id})
                            cleared = True
                        except Exception as exc:
                            last_clear_err = str(exc)
                    if not cleared:
                        return False, self.env_ctx.masker.mask(
                            f"clear_data reset failed cleanly: {last_clear_err or 'unable to clear app data'}"
                        )
                    driver.activate_app(self.app_id)
                    return True, ""

                # iOS simulator container reset via uninstall/reinstall
                try:
                    driver.terminate_app(self.app_id)
                except Exception as exc:
                    low = str(exc).lower()
                    if "not running" not in low and "unknown" not in low:
                        return False, self.env_ctx.masker.mask(f"terminate_app failed: {exc}")
                try:
                    driver.remove_app(self.app_id)
                except Exception as exc:
                    low = str(exc).lower()
                    if not any(tok in low for tok in ("not installed", "unknown", "does not exist")):
                        return False, self.env_ctx.masker.mask(f"remove_app failed: {exc}")
                if self.app_path is not None:
                    driver.install_app(str(self.app_path))
                driver.activate_app(self.app_id)
                return True, ""

            return False, f"Unsupported reset mode: {mode}"
        except Exception as exc:
            return False, self.env_ctx.masker.mask(str(exc))

    def _check_target_gate(self, spec: TestSpec, expanded_steps: list[StepItem]) -> str | None:
        target = self.profile.target_type
        if target not in spec.targets:
            return f"Skipped: spec targets {spec.targets} do not include '{target}'"
        if target == "device" and any(s.type == "biometric" for s in expanded_steps):
            return "Skipped: test contains biometric steps (simulator/emulator only)"
        return None

    def _run_step_list(
        self,
        ctx: StepContext,
        spec_id: str,
        attempt_num: int,
        phase: str,
        step_list: list[StepItem],
        continue_on_failure: bool,
        start_index: int,
    ) -> tuple[list[StepResult], str | None, str, str, int | None, str, bool]:
        results: list[StepResult] = []
        first_error: str | None = None
        first_category = ""
        first_exc_type = ""
        failed_step_idx: int | None = None
        failed_step_action = ""
        interrupted = False
        aborted = False

        for offset, step in enumerate(step_list):
            step_idx = start_index + offset
            desc = format_step_description(step, masker=ctx.masker)
            target = step.params.get("target")

            if aborted:
                results.append(
                    StepResult(
                        index=step_idx,
                        phase=phase,  # type: ignore[arg-type]
                        action=step.type,
                        target=target,
                        flow_name=step.flow_name,
                        description=desc,
                        status="skipped",
                    )
                )
                continue

            t0 = time.monotonic()
            secret_elems: list[Any] = []
            shot_res = ScreenshotCaptureResult(rel_path=None, redacted_count=0, redaction_status="none_needed")
            page_source_rel: str | None = None
            webview_detected = False
            webview_details: list[str] = []
            step_status: str = "passed"
            step_error: str | None = None
            step_cat = ""
            step_exc_type = ""
            step_diag: dict[str, Any] = {}
            visual_diff_path = ""
            visual_diff_score: float | None = None

            try:
                outcome = execute_step(ctx, step)
                secret_elems = outcome.secret_elements
                visual_diff_path = outcome.visual_diff_path
                visual_diff_score = outcome.visual_diff_score
                step_diag = dict(outcome.diagnostics or {})
                if outcome.status == "skipped":
                    step_status = "skipped"
                if step.capture != "off":
                    stem_name = outcome.screenshot_name or f"{step_idx:02d}_{step.type}"
                    rel_stem = f"{_slug(spec_id)}/attempt_{attempt_num}/{phase}_{_slug(stem_name)}"
                    shot_res = self.evidence.capture_screenshot_detailed(
                        ctx.driver, rel_stem, extra_secret_elements=secret_elems
                    )
                if step.type in ("launch", "screenshot", "visual_compare"):
                    _, webview_detected, webview_details = self.evidence.capture_page_source(
                        ctx.driver, rel_stem="", save_to_disk=False
                    )
            except KeyboardInterrupt:
                interrupted = True
                step_status = "failed"
                step_cat = "interrupted"
                step_exc_type = "KeyboardInterrupt"
                step_error = "Execution interrupted by user (KeyboardInterrupt)"
                if first_error is None:
                    first_error = step_error
                    first_category = step_cat
                    first_exc_type = step_exc_type
                    failed_step_idx = step_idx
                    failed_step_action = step.type
                aborted = True
            except Exception as exc:
                step_status = "failed"
                step_error = ctx.masker.mask(str(exc))
                step_cat, step_exc_type = classify_failure(exc, step_action=step.type, phase=phase)
                raw_diag = getattr(exc, "diagnostics", None)
                if isinstance(raw_diag, dict):
                    step_diag = ctx.masker.mask_obj(raw_diag)
                    if not visual_diff_path and raw_diag.get("diff_path"):
                        visual_diff_path = str(raw_diag["diff_path"])
                    if visual_diff_score is None and raw_diag.get("mismatch_ratio") is not None:
                        visual_diff_score = float(raw_diag["mismatch_ratio"])
                if first_error is None:
                    first_error = step_error
                    first_category = step_cat
                    first_exc_type = step_exc_type
                    failed_step_idx = step_idx
                    failed_step_action = step.type
                rel_stem = f"{_slug(spec_id)}/attempt_{attempt_num}/{phase}_{step_idx:02d}_{step.type}_FAIL"
                if step.capture != "off":
                    shot_res = self.evidence.capture_screenshot_detailed(
                        ctx.driver, rel_stem, extra_secret_elements=secret_elems
                    )
                page_source_rel, webview_detected, webview_details = self.evidence.capture_page_source(
                    ctx.driver, rel_stem, save_to_disk=True
                )
                if phase in ("setup", "steps") and not continue_on_failure:
                    aborted = True

            duration_ms = int((time.monotonic() - t0) * 1000)
            results.append(
                StepResult(
                    index=step_idx,
                    phase=phase,  # type: ignore[arg-type]
                    action=step.type,
                    target=target,
                    flow_name=step.flow_name,
                    description=desc,
                    status=step_status,  # type: ignore[arg-type]
                    duration_ms=duration_ms,
                    screenshot_path=shot_res.rel_path,
                    page_source_path=page_source_rel,
                    error=step_error,
                    webview_detected=webview_detected,
                    webview_details=webview_details,
                    redacted_count=shot_res.redacted_count,
                    redaction_status=shot_res.redaction_status,
                    redaction_warning=shot_res.warning,
                    failure_category=step_cat,
                    exception_type=step_exc_type,
                    diagnostics=step_diag,
                    visual_diff_path=visual_diff_path,
                    visual_diff_score=visual_diff_score,
                )
            )

        return (
            results,
            first_error,
            first_category,
            first_exc_type,
            failed_step_idx,
            failed_step_action,
            interrupted,
        )

    def run(self, specs: list[TestSpec], appium_log_rel: str | None = None) -> RunResult:
        """Execute all selected specs, reusing one Appium session when compatible."""
        started_at = _utc_now_iso()
        run_t0 = time.monotonic()

        driver: Any = None
        current_alert_override: bool | None = None
        os_version = str(self.profile.platform_version or "")
        app_version = ""
        appium_version = ""
        automation_name = self.profile.automation_name or (
            "UiAutomator2" if self.profile.platform == "android" else "XCUITest"
        )
        app_sha256 = _compute_file_sha256(self.app_path)
        run_interrupted = False
        test_results: list[TestResult] = []

        def _ensure_session(auto_accept_override: bool | None) -> Any:
            nonlocal driver, current_alert_override, os_version, app_version, appium_version, automation_name
            if driver is not None and auto_accept_override != current_alert_override:
                try:
                    driver.quit()
                except Exception:
                    pass
                driver = None
            if driver is None:
                driver = self.driver_factory(auto_accept_override)
                current_alert_override = auto_accept_override
                caps = getattr(driver, "capabilities", {}) or {}
                if caps.get("platformVersion"):
                    os_version = str(caps["platformVersion"])
                if caps.get("appVersion"):
                    app_version = str(caps["appVersion"])
                if caps.get("appiumVersion"):
                    appium_version = str(caps["appiumVersion"])
                if caps.get("automationName"):
                    automation_name = str(caps["automationName"])
            return driver

        try:
            for spec in specs:
                if run_interrupted:
                    break
                expanded_setup = expand_steps(spec.setup, self.flows)
                expanded_steps = expand_steps(spec.steps, self.flows)
                expanded_teardown = expand_steps(spec.teardown, self.flows)

                skip_reason = self._check_target_gate(spec, expanded_setup + expanded_steps)
                if skip_reason:
                    test_results.append(
                        TestResult(
                            id=spec.id,
                            title=spec.title,
                            tags=spec.tags,
                            platforms=list(spec.platforms),
                            targets=list(spec.targets),
                            priority=spec.priority,
                            expected=spec.expected,
                            retriable=spec.retriable,
                            reset_mode=spec.reset,
                            status="skipped",
                            skip_reason=skip_reason,
                        )
                    )
                    continue

                attempts: list[AttemptResult] = []
                effective_retries = self.retries if spec.retries is None else max(0, spec.retries)
                max_attempts = 1 + effective_retries
                test_t0 = time.monotonic()
                pending_retry_reason = ""

                for attempt_num in range(1, max_attempts + 1):
                    att_t0 = time.monotonic()
                    reset_status = "ok"
                    reset_error = ""
                    try:
                        active_driver = _ensure_session(spec.auto_accept_alerts)
                        if spec.reset != "none" or len(test_results) > 0 or attempt_num > 1:
                            reset_ok, reset_error = self._apply_reset(active_driver, spec.reset)
                            if not reset_ok:
                                reset_status = "failed"
                        else:
                            reset_status = "skipped"
                    except KeyboardInterrupt:
                        run_interrupted = True
                        attempts.append(
                            AttemptResult(
                                attempt=attempt_num,
                                status="failed",
                                duration_ms=int((time.monotonic() - att_t0) * 1000),
                                error="Execution interrupted during session setup",
                                failure_category="interrupted",
                                exception_type="KeyboardInterrupt",
                                reset_status="failed",
                                reset_error="Interrupted",
                                retry_reason=pending_retry_reason,
                                steps=[],
                            )
                        )
                        break

                    if reset_status == "failed":
                        attempts.append(
                            AttemptResult(
                                attempt=attempt_num,
                                status="failed",
                                duration_ms=int((time.monotonic() - att_t0) * 1000),
                                error=f"Pre-test reset ({spec.reset}) failed: {reset_error}",
                                failure_category="app_launch_failure",
                                exception_type="RuntimeError",
                                reset_status="failed",
                                reset_error=reset_error,
                                retry_reason=pending_retry_reason,
                                steps=[],
                            )
                        )
                        can_retry = (
                            attempt_num < max_attempts
                            and spec.retriable
                            and ("app_launch_failure" in TRANSIENT_FAILURE_CATEGORIES)
                        )
                        if not can_retry:
                            break
                        pending_retry_reason = "Retrying after reset failure"
                        continue

                    ctx = StepContext(
                        driver=active_driver,
                        platform=self.profile.platform,
                        target_type=self.profile.target_type,
                        profile=self.profile,
                        env_cfg=self.env_cfg,
                        env_ctx=self.env_ctx,
                        screens=self.screens,
                        evidence=self.evidence,
                        masker=self.env_ctx.masker,
                        app_id=self.app_id,
                        app_path=self.app_path,
                        reset_mode=spec.reset,
                        reset_fn=lambda mode, d=active_driver: self._apply_reset(d, mode),
                        poll_interval=self.poll_interval,
                        run_dir=self.run_dir,
                        update_baselines=self.update_baselines,
                    )

                    setup_results: list[StepResult] = []
                    setup_err: str | None = None
                    setup_cat = ""
                    setup_exc = ""
                    setup_idx: int | None = None
                    setup_act = ""
                    setup_int = False
                    if expanded_setup:
                        (
                            setup_results,
                            setup_err,
                            setup_cat,
                            setup_exc,
                            setup_idx,
                            setup_act,
                            setup_int,
                        ) = self._run_step_list(
                            ctx=ctx,
                            spec_id=spec.id,
                            attempt_num=attempt_num,
                            phase="setup",
                            step_list=expanded_setup,
                            continue_on_failure=spec.continue_on_failure,
                            start_index=1,
                        )

                    step_results: list[StepResult] = []
                    step_err: str | None = None
                    step_cat = ""
                    step_exc = ""
                    step_idx: int | None = None
                    step_act = ""
                    step_int = False
                    if not setup_int and (setup_err is None or spec.continue_on_failure):
                        (
                            step_results,
                            step_err,
                            step_cat,
                            step_exc,
                            step_idx,
                            step_act,
                            step_int,
                        ) = self._run_step_list(
                            ctx=ctx,
                            spec_id=spec.id,
                            attempt_num=attempt_num,
                            phase="steps",
                            step_list=expanded_steps,
                            continue_on_failure=spec.continue_on_failure,
                            start_index=len(setup_results) + 1,
                        )
                    elif expanded_steps and not setup_int:
                        # Setup failed -> mark main steps skipped clearly
                        base_idx = len(setup_results) + 1
                        for off, s_item in enumerate(expanded_steps):
                            step_results.append(
                                StepResult(
                                    index=base_idx + off,
                                    phase="steps",
                                    action=s_item.type,
                                    target=s_item.params.get("target"),
                                    flow_name=s_item.flow_name,
                                    description=format_step_description(s_item, masker=ctx.masker),
                                    status="skipped",
                                )
                            )

                    teardown_results: list[StepResult] = []
                    td_err: str | None = None
                    td_cat = ""
                    td_exc = ""
                    td_idx: int | None = None
                    td_act = ""
                    td_int = False
                    if expanded_teardown and not setup_int and not step_int:
                        (
                            teardown_results,
                            td_err,
                            td_cat,
                            td_exc,
                            td_idx,
                            td_act,
                            td_int,
                        ) = self._run_step_list(
                            ctx=ctx,
                            spec_id=spec.id,
                            attempt_num=attempt_num,
                            phase="teardown",
                            step_list=expanded_teardown,
                            continue_on_failure=True,
                            start_index=len(setup_results) + len(step_results) + 1,
                        )

                    all_steps = setup_results + step_results + teardown_results
                    att_err = setup_err or step_err or td_err
                    att_cat = setup_cat or step_cat or td_cat
                    att_exc = setup_exc or step_exc or td_exc
                    att_failed_idx = setup_idx if setup_err else (step_idx if step_err else td_idx)
                    att_failed_act = setup_act if setup_err else (step_act if step_err else td_act)
                    att_interrupted = setup_int or step_int or td_int
                    att_status = "failed" if (att_err or att_interrupted) else "passed"
                    attempts.append(
                        AttemptResult(
                            attempt=attempt_num,
                            status=att_status,  # type: ignore[arg-type]
                            duration_ms=int((time.monotonic() - att_t0) * 1000),
                            error=att_err,
                            failure_category=att_cat,
                            exception_type=att_exc,
                            failed_step_index=att_failed_idx,
                            failed_step_action=att_failed_act,
                            reset_status=reset_status,
                            reset_error=reset_error,
                            retry_reason=pending_retry_reason,
                            steps=all_steps,
                        )
                    )
                    if att_interrupted:
                        run_interrupted = True
                        break
                    if att_status == "passed":
                        break

                    can_retry = (
                        attempt_num < max_attempts
                        and spec.retriable
                        and (
                            att_cat in TRANSIENT_FAILURE_CATEGORIES
                            or (att_cat == "assertion_failure" and spec.retry_on_assertion)
                        )
                    )
                    if not can_retry:
                        break
                    pending_retry_reason = (
                        f"Retrying after transient {att_cat or 'failure'} on attempt {attempt_num}"
                    )

                final_attempt = attempts[-1]
                flaky = len(attempts) > 1 and final_attempt.status == "passed"
                webview_hit = any(s.webview_detected for s in final_attempt.steps)
                redaction_warn_count = sum(
                    1
                    for s in final_attempt.steps
                    if s.redaction_status in ("warning", "failed", "skipped_secure_mode")
                )
                test_results.append(
                    TestResult(
                        id=spec.id,
                        title=spec.title,
                        tags=spec.tags,
                        platforms=list(spec.platforms),
                        targets=list(spec.targets),
                        priority=spec.priority,
                        expected=spec.expected,
                        status=final_attempt.status,
                        flaky=flaky,
                        failure_reason=final_attempt.error,
                        failure_category=final_attempt.failure_category,
                        exception_type=final_attempt.exception_type,
                        failed_step_index=final_attempt.failed_step_index,
                        failed_step_action=final_attempt.failed_step_action,
                        retriable=spec.retriable,
                        reset_mode=spec.reset,
                        redaction_warnings=redaction_warn_count,
                        duration_ms=int((time.monotonic() - test_t0) * 1000),
                        webview_detected=webview_hit,
                        attempts=attempts,
                        steps=final_attempt.steps,
                    )
                )
        except KeyboardInterrupt:
            run_interrupted = True
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass

        finished_at = _utc_now_iso()
        cat_counts: dict[str, int] = {}
        for t in test_results:
            if t.status == "failed" and t.failure_category:
                cat_counts[t.failure_category] = cat_counts.get(t.failure_category, 0) + 1

        summary = RunSummary(
            total=len(test_results),
            passed=sum(1 for t in test_results if t.status == "passed"),
            failed=sum(1 for t in test_results if t.status == "failed"),
            skipped=sum(1 for t in test_results if t.status == "skipped"),
            flaky=sum(1 for t in test_results if t.flaky),
            interrupted=sum(1 for t in test_results if t.failure_category == "interrupted"),
            webview_tests=sum(1 for t in test_results if t.webview_detected),
            redaction_warnings=sum(t.redaction_warnings for t in test_results),
            failure_categories=cat_counts,
            duration_ms=int((time.monotonic() - run_t0) * 1000),
        )

        return RunResult(
            schema_version="1.1",
            run_id=self.run_id,
            tool_version=__version__,
            started_at=started_at,
            finished_at=finished_at,
            environment=self.env_name,
            platform=self.profile.platform,
            device_profile=self.profile_name,
            device_name=self.profile.device_name,
            target_type=self.profile.target_type,
            os_version=os_version,
            app_path=str(self.app_path) if self.app_path else "",
            app_version=app_version,
            app_sha256=app_sha256,
            appium_version=appium_version,
            automation_name=automation_name,
            host_os=py_platform.platform(),
            python_version=py_platform.python_version(),
            suite=self.suite,
            tags=self.tags,
            interrupted=run_interrupted,
            summary=summary,
            tests=test_results,
            appium_log_path=appium_log_rel,
        )
