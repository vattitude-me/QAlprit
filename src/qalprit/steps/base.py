"""Shared context, auto-wait element resolution, and step handler registry."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from qalprit.config_loader import DeviceProfile, EnvironmentConfig, ResolvedLocator, ScreensCatalog
from qalprit.env_loader import EnvContext, SecretMasker
from qalprit.evidence import EvidenceCollector
from qalprit.spec_loader import StepItem


class StepFailureError(AssertionError):
    """Assertion failure carrying structured failure category and diagnostics."""

    def __init__(
        self,
        message: str,
        *,
        failure_category: str = "assertion_failure",
        diagnostics: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.failure_category = failure_category
        self.diagnostics = diagnostics or {}


class StepTimeoutError(TimeoutError):
    """Wait/locator timeout carrying structured failure category and diagnostics."""

    def __init__(
        self,
        message: str,
        *,
        failure_category: str = "locator_not_found",
        diagnostics: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.failure_category = failure_category
        self.diagnostics = diagnostics or {}


def _is_stale_error(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    return "stale" in name or "stale element" in msg or "stale reference" in msg


@dataclass
class StepOutcome:
    """Result returned by an individual step executor."""

    status: Literal["passed", "skipped"] = "passed"
    secret_elements: list[Any] = field(default_factory=list)
    screenshot_name: str | None = None
    note: str | None = None
    visual_diff_path: str = ""
    visual_diff_score: float | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepContext:
    """Execution context passed to step handlers."""

    driver: Any
    platform: str
    target_type: Literal["simulator", "device"]
    profile: DeviceProfile
    env_cfg: EnvironmentConfig
    env_ctx: EnvContext
    screens: ScreensCatalog
    evidence: EvidenceCollector
    masker: SecretMasker
    app_id: str
    app_path: Path | None
    reset_mode: Literal["reinstall", "clear_data", "none"]
    reset_fn: Callable[[str], Any]
    poll_interval: float = 0.25
    run_dir: Path | None = None
    update_baselines: bool = False

    @property
    def extra_env_vars(self) -> dict[str, str]:
        return self.env_cfg.as_env_vars()

    def resolve_locator(self, target: str, params: dict[str, Any] | None = None) -> ResolvedLocator:
        return self.screens.resolve(
            target=target,
            platform=self.platform,
            env_ctx=self.env_ctx,
            params=params,
            extra_vars=self.extra_env_vars,
        )

    def substitute_text(self, value: str, secret: bool = False) -> str:
        return self.env_ctx.substitute(
            str(value),
            extra_vars=self.extra_env_vars,
            record_secret=secret,
        )


def wait_for_element(
    ctx: StepContext,
    target: str,
    params: dict[str, Any] | None = None,
    timeout: float | None = None,
    *,
    require_displayed: bool = True,
    require_enabled: bool = False,
    strict_single: bool = False,
    failure_category: str = "locator_not_found",
) -> tuple[Any, ResolvedLocator]:
    """Poll until the target element meets visibility/readiness criteria or raise StepTimeoutError."""
    resolved = ctx.resolve_locator(target, params=params)
    wait_seconds = float(ctx.env_cfg.default_timeout if timeout is None else timeout)
    sleep_step = min(ctx.poll_interval, max(0.01, wait_seconds / 4.0)) if wait_seconds > 0 else 0.0
    deadline = time.monotonic() + max(0.0, wait_seconds)
    last_err: Exception | None = None
    last_state = "not_found"
    last_match_count = 0
    polls = 0

    while True:
        polls += 1
        try:
            elems = ctx.driver.find_elements(resolved.strategy, resolved.value)
            last_match_count = len(elems)
            if not elems:
                last_state = "not_found"
            else:
                visible_matches: list[Any] = []
                saw_hidden = False
                saw_stale = False
                for elem in elems:
                    try:
                        displayed = elem.is_displayed() if require_displayed else True
                        if not displayed:
                            saw_hidden = True
                            continue
                        visible_matches.append(elem)
                    except Exception as exc:
                        if _is_stale_error(exc):
                            saw_stale = True
                        last_err = exc
                        continue

                if visible_matches:
                    if strict_single and len(visible_matches) > 1:
                        last_state = "multiple_matches"
                    else:
                        ready: list[Any] = []
                        saw_disabled = False
                        for elem in visible_matches:
                            try:
                                if require_enabled and hasattr(elem, "is_enabled") and not elem.is_enabled():
                                    saw_disabled = True
                                    continue
                                ready.append(elem)
                            except Exception as exc:
                                if _is_stale_error(exc):
                                    saw_stale = True
                                last_err = exc
                                continue
                        if ready:
                            return ready[0], resolved
                        if saw_disabled:
                            last_state = "disabled"
                        elif saw_stale:
                            last_state = "stale_reference"
                elif saw_hidden:
                    last_state = "hidden"
                elif saw_stale:
                    last_state = "stale_reference"
        except Exception as exc:
            last_err = exc
            if _is_stale_error(exc):
                last_state = "stale_reference"

        if time.monotonic() >= deadline:
            break
        if sleep_step > 0:
            time.sleep(sleep_step)

    masked_val = ctx.masker.mask(resolved.value)
    resolved_category = failure_category
    if last_state in ("hidden", "disabled", "stale_reference") and failure_category == "locator_not_found":
        resolved_category = "element_not_interactable"

    diagnostics = {
        "target": target,
        "platform": ctx.platform,
        "strategy": resolved.strategy,
        "locator_value": masked_val,
        "timeout_s": round(wait_seconds, 3),
        "polls": polls,
        "last_state": last_state,
        "last_match_count": last_match_count,
    }
    detail = f"; last error: {ctx.masker.mask(str(last_err))}" if last_err else ""
    raise StepTimeoutError(
        f"Timed out after {wait_seconds:.1f}s waiting for '{target}' on {ctx.platform} "
        f"[{resolved.strategy}={masked_val!r}] (state={last_state}, matches={last_match_count}, polls={polls}{detail})",
        failure_category=resolved_category,
        diagnostics=diagnostics,
    )


def perform_with_stale_retry(
    ctx: StepContext,
    target: str,
    action_fn: Callable[[Any, ResolvedLocator], Any],
    *,
    params: dict[str, Any] | None = None,
    timeout: float | None = None,
    require_displayed: bool = True,
    require_enabled: bool = False,
    strict_single: bool = False,
    failure_category: str = "locator_not_found",
) -> tuple[Any, Any, ResolvedLocator]:
    """Find element and execute action_fn, retrying lookup once on StaleElementReference."""
    elem, resolved = wait_for_element(
        ctx,
        target,
        params=params,
        timeout=timeout,
        require_displayed=require_displayed,
        require_enabled=require_enabled,
        strict_single=strict_single,
        failure_category=failure_category,
    )
    try:
        return action_fn(elem, resolved), elem, resolved
    except Exception as exc:
        if not _is_stale_error(exc):
            raise
        elem, resolved = wait_for_element(
            ctx,
            target,
            params=params,
            timeout=timeout,
            require_displayed=require_displayed,
            require_enabled=require_enabled,
            strict_single=strict_single,
            failure_category=failure_category,
        )
        return action_fn(elem, resolved), elem, resolved


def wait_until_not_visible(
    ctx: StepContext,
    target: str,
    params: dict[str, Any] | None = None,
    timeout: float | None = None,
) -> ResolvedLocator:
    """Poll until the target element is absent or not displayed."""
    resolved = ctx.resolve_locator(target, params=params)
    wait_seconds = float(ctx.env_cfg.default_timeout if timeout is None else timeout)
    sleep_step = min(ctx.poll_interval, max(0.01, wait_seconds / 4.0)) if wait_seconds > 0 else 0.0
    deadline = time.monotonic() + max(0.0, wait_seconds)

    while True:
        visible = False
        try:
            elems = ctx.driver.find_elements(resolved.strategy, resolved.value)
            for elem in elems:
                try:
                    if elem.is_displayed():
                        visible = True
                        break
                except Exception:
                    continue
        except Exception:
            visible = False

        if not visible:
            return resolved

        if time.monotonic() >= deadline:
            break
        if sleep_step > 0:
            time.sleep(sleep_step)

    masked_val = ctx.masker.mask(resolved.value)
    raise StepFailureError(
        f"Element '{target}' [{resolved.strategy}={masked_val!r}] "
        f"was still visible after {wait_seconds:.1f}s",
        failure_category="assertion_failure",
        diagnostics={"target": target, "platform": ctx.platform, "strategy": resolved.strategy},
    )


def format_step_description(step: StepItem, masker: SecretMasker | None = None) -> str:
    """Render a human-readable, secret-safe step description for logs and reports."""
    p = step.params
    t = step.type
    if t == "launch":
        desc = f"Launch {p.get('app', 'app')}"
    elif t == "reset_app":
        desc = "Reset application state"
    elif t == "background":
        desc = f"Background app for {p.get('seconds')}s and resume"
    elif t in (
        "tap",
        "tap_if_visible",
        "clear",
        "wait_for_visible",
        "wait_for_enabled",
        "assert_visible",
        "assert_not_visible",
        "assert_enabled",
        "assert_disabled",
    ):
        verb = t.replace("_", " ").capitalize()
        desc = f"{verb} {p.get('target')}"
        if p.get("params"):
            desc += f" ({p['params']})"
    elif t == "type":
        val = "***REDACTED***" if step.secret else str(p.get("value", ""))
        desc = f"Type '{val}' into {p.get('target')}"
    elif t == "swipe":
        target = p.get("target")
        desc = f"Swipe {p.get('direction')}" + (f" on {target}" if target else "")
    elif t == "scroll_until_visible":
        desc = f"Scroll {p.get('direction', 'down')} until {p.get('target')} is visible"
    elif t == "wait":
        desc = f"Wait {p.get('seconds')}s"
    elif t in ("assert_text", "wait_for_text"):
        verb = "Assert" if t == "assert_text" else "Wait for"
        if "equals" in p:
            desc = f"{verb} {p.get('target')} text equals '{p['equals']}'"
        elif "regex" in p:
            desc = f"{verb} {p.get('target')} text matches /{p['regex']}/"
        else:
            desc = f"{verb} {p.get('target')} text contains '{p.get('contains')}'"
    elif t == "wait_for_keyboard":
        shown = p.get("shown", True)
        desc = f"Wait for soft keyboard to {'appear' if shown else 'dismiss'}"
    elif t == "back":
        desc = "Press device back"
    elif t == "hide_keyboard":
        desc = "Hide soft keyboard"
    elif t == "biometric":
        desc = f"Simulate biometric {p.get('action')}"
    elif t == "screenshot":
        label = p.get("label")
        desc = f"Capture screenshot '{p.get('name')}'" + (f" ({label})" if label else "")
    elif t == "visual_compare":
        desc = f"Compare screen visually against baseline '{p.get('baseline') or p.get('name')}'"
    else:
        desc = f"{t} {p}"

    if masker is not None:
        return masker.mask(desc)
    return desc
