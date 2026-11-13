"""Test suite runner: session lifecycle, between-test reset, step + teardown, retries."""

from __future__ import annotations

import os
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
from qalprit.evidence import EvidenceCollector
from qalprit.results import (
    AttemptResult,
    RunResult,
    RunSummary,
    StepResult,
    TestResult,
)
from qalprit.spec_loader import FlowSpec, StepItem, TestSpec, expand_steps
from qalprit.steps import StepContext, execute_step, format_step_description


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slug(value: str) -> str:
    return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in value)


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
        poll_interval: float = 0.25,
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
        self.poll_interval = poll_interval
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
        )

    def _apply_reset(self, driver: Any, mode: str) -> None:
        if not self.app_id:
            return
        if mode == "none":
            try:
                driver.terminate_app(self.app_id)
            except Exception:
                pass
            driver.activate_app(self.app_id)
            return

        if mode == "reinstall":
            try:
                driver.remove_app(self.app_id)
            except Exception:
                pass
            if self.app_path is not None:
                driver.install_app(str(self.app_path))
            driver.activate_app(self.app_id)
            return

        if mode == "clear_data":
            if self.profile.platform == "android":
                cleared = False
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
                    except Exception:
                        pass
                if not cleared:
                    try:
                        driver.execute_script("mobile: clearApp", {"appId": self.app_id})
                        cleared = True
                    except Exception:
                        pass
                if not cleared:
                    try:
                        driver.terminate_app(self.app_id)
                    except Exception:
                        pass
                driver.activate_app(self.app_id)
            else:
                # iOS simulator container reset via uninstall/reinstall (note: Keychain persists)
                try:
                    driver.terminate_app(self.app_id)
                except Exception:
                    pass
                try:
                    driver.remove_app(self.app_id)
                except Exception:
                    pass
                if self.app_path is not None:
                    driver.install_app(str(self.app_path))
                driver.activate_app(self.app_id)

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
    ) -> tuple[list[StepResult], str | None]:
        results: list[StepResult] = []
        first_error: str | None = None
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
            screenshot_rel: str | None = None
            page_source_rel: str | None = None
            redacted_count = 0
            webview_detected = False
            webview_details: list[str] = []
            step_status: str = "passed"
            step_error: str | None = None

            try:
                outcome = execute_step(ctx, step)
                secret_elems = outcome.secret_elements
                if outcome.status == "skipped":
                    step_status = "skipped"
                if step.capture != "off":
                    stem_name = outcome.screenshot_name or f"{step_idx:02d}_{step.type}"
                    rel_stem = f"{_slug(spec_id)}/attempt_{attempt_num}/{phase}_{_slug(stem_name)}"
                    screenshot_rel, redacted_count = self.evidence.capture_screenshot(
                        ctx.driver, rel_stem, extra_secret_elements=secret_elems
                    )
                if step.type in ("launch", "screenshot"):
                    _, webview_detected, webview_details = self.evidence.capture_page_source(
                        ctx.driver, rel_stem="", save_to_disk=False
                    )
            except Exception as exc:
                step_status = "failed"
                step_error = ctx.masker.mask(str(exc))
                if first_error is None:
                    first_error = step_error
                rel_stem = f"{_slug(spec_id)}/attempt_{attempt_num}/{phase}_{step_idx:02d}_{step.type}_FAIL"
                if step.capture != "off":
                    screenshot_rel, redacted_count = self.evidence.capture_screenshot(
                        ctx.driver, rel_stem, extra_secret_elements=secret_elems
                    )
                page_source_rel, webview_detected, webview_details = self.evidence.capture_page_source(
                    ctx.driver, rel_stem, save_to_disk=True
                )
                if phase == "steps" and not continue_on_failure:
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
                    screenshot_path=screenshot_rel,
                    page_source_path=page_source_rel,
                    error=step_error,
                    webview_detected=webview_detected,
                    webview_details=webview_details,
                    redacted_count=redacted_count,
                )
            )

        return results, first_error

    def run(self, specs: list[TestSpec], appium_log_rel: str | None = None) -> RunResult:
        """Execute all selected specs, reusing one Appium session when compatible."""
        started_at = _utc_now_iso()
        run_t0 = time.monotonic()

        driver: Any = None
        current_alert_override: bool | None = None
        os_version = str(self.profile.platform_version or "")
        test_results: list[TestResult] = []

        def _ensure_session(auto_accept_override: bool | None) -> Any:
            nonlocal driver, current_alert_override, os_version
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
            return driver

        try:
            for spec in specs:
                expanded_steps = expand_steps(spec.steps, self.flows)
                expanded_teardown = expand_steps(spec.teardown, self.flows)

                skip_reason = self._check_target_gate(spec, expanded_steps)
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
                            status="skipped",
                            skip_reason=skip_reason,
                        )
                    )
                    continue

                attempts: list[AttemptResult] = []
                max_attempts = 1 + self.retries
                test_t0 = time.monotonic()

                for attempt_num in range(1, max_attempts + 1):
                    active_driver = _ensure_session(spec.auto_accept_alerts)
                    if spec.reset != "none" or len(test_results) > 0 or attempt_num > 1:
                        self._apply_reset(active_driver, spec.reset)

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
                    )

                    att_t0 = time.monotonic()
                    step_results, step_err = self._run_step_list(
                        ctx=ctx,
                        spec_id=spec.id,
                        attempt_num=attempt_num,
                        phase="steps",
                        step_list=expanded_steps,
                        continue_on_failure=spec.continue_on_failure,
                        start_index=1,
                    )

                    teardown_results: list[StepResult] = []
                    td_err: str | None = None
                    if expanded_teardown:
                        teardown_results, td_err = self._run_step_list(
                            ctx=ctx,
                            spec_id=spec.id,
                            attempt_num=attempt_num,
                            phase="teardown",
                            step_list=expanded_teardown,
                            continue_on_failure=True,
                            start_index=len(step_results) + 1,
                        )

                    all_steps = step_results + teardown_results
                    att_err = step_err or td_err
                    att_status = "failed" if att_err else "passed"
                    attempts.append(
                        AttemptResult(
                            attempt=attempt_num,
                            status=att_status,  # type: ignore[arg-type]
                            duration_ms=int((time.monotonic() - att_t0) * 1000),
                            error=att_err,
                            steps=all_steps,
                        )
                    )
                    if att_status == "passed":
                        break

                final_attempt = attempts[-1]
                flaky = len(attempts) > 1 and final_attempt.status == "passed"
                webview_hit = any(s.webview_detected for s in final_attempt.steps)
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
                        duration_ms=int((time.monotonic() - test_t0) * 1000),
                        webview_detected=webview_hit,
                        attempts=attempts,
                        steps=final_attempt.steps,
                    )
                )
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass

        finished_at = _utc_now_iso()
        summary = RunSummary(
            total=len(test_results),
            passed=sum(1 for t in test_results if t.status == "passed"),
            failed=sum(1 for t in test_results if t.status == "failed"),
            skipped=sum(1 for t in test_results if t.status == "skipped"),
            flaky=sum(1 for t in test_results if t.flaky),
            webview_tests=sum(1 for t in test_results if t.webview_detected),
            duration_ms=int((time.monotonic() - run_t0) * 1000),
        )

        return RunResult(
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
            suite=self.suite,
            tags=self.tags,
            summary=summary,
            tests=test_results,
            appium_log_path=appium_log_rel,
        )
