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


@dataclass
class StepOutcome:
    """Result returned by an individual step executor."""

    status: Literal["passed", "skipped"] = "passed"
    secret_elements: list[Any] = field(default_factory=list)
    screenshot_name: str | None = None
    note: str | None = None


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
    reset_fn: Callable[[str], None]
    poll_interval: float = 0.25

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
) -> tuple[Any, ResolvedLocator]:
    """Poll until the target element exists and is displayed, or raise TimeoutError."""
    resolved = ctx.resolve_locator(target, params=params)
    wait_seconds = float(ctx.env_cfg.default_timeout if timeout is None else timeout)
    deadline = time.monotonic() + max(0.0, wait_seconds)
    last_err: Exception | None = None

    while True:
        try:
            elems = ctx.driver.find_elements(resolved.strategy, resolved.value)
            for elem in elems:
                try:
                    if elem.is_displayed():
                        return elem, resolved
                except Exception:
                    continue
        except Exception as exc:
            last_err = exc

        if time.monotonic() >= deadline:
            break
        time.sleep(ctx.poll_interval)

    detail = f" ({last_err})" if last_err else ""
    raise TimeoutError(
        f"Timed out after {wait_seconds:.1f}s waiting for '{target}' "
        f"[{resolved.strategy}={resolved.value!r}]{detail}"
    )


def wait_until_not_visible(
    ctx: StepContext,
    target: str,
    params: dict[str, Any] | None = None,
    timeout: float | None = None,
) -> ResolvedLocator:
    """Poll until the target element is absent or not displayed."""
    resolved = ctx.resolve_locator(target, params=params)
    wait_seconds = float(ctx.env_cfg.default_timeout if timeout is None else timeout)
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
        time.sleep(ctx.poll_interval)

    raise AssertionError(
        f"Element '{target}' [{resolved.strategy}={resolved.value!r}] "
        f"was still visible after {wait_seconds:.1f}s"
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
    elif t in ("tap", "tap_if_visible", "clear", "wait_for_visible", "assert_visible", "assert_not_visible"):
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
    elif t == "assert_text":
        if "equals" in p:
            desc = f"Assert {p.get('target')} text equals '{p['equals']}'"
        else:
            desc = f"Assert {p.get('target')} text contains '{p.get('contains')}'"
    elif t == "back":
        desc = "Press device back"
    elif t == "hide_keyboard":
        desc = "Hide soft keyboard"
    elif t == "biometric":
        desc = f"Simulate biometric {p.get('action')}"
    elif t == "screenshot":
        desc = f"Capture screenshot '{p.get('name')}'"
    else:
        desc = f"{t} {p}"

    if masker is not None:
        return masker.mask(desc)
    return desc
