"""Step dispatcher registry."""

from __future__ import annotations

from collections.abc import Callable

from qalprit.spec_loader import StepItem
from qalprit.steps.app_steps import exec_background, exec_launch, exec_reset_app
from qalprit.steps.base import StepContext, StepOutcome, format_step_description
from qalprit.steps.device_steps import exec_biometric
from qalprit.steps.interaction_steps import (
    exec_back,
    exec_clear,
    exec_hide_keyboard,
    exec_scroll_until_visible,
    exec_swipe,
    exec_tap,
    exec_tap_if_visible,
    exec_type,
)
from qalprit.steps.media_steps import exec_screenshot
from qalprit.steps.wait_assert_steps import (
    exec_assert_not_visible,
    exec_assert_text,
    exec_assert_visible,
    exec_wait,
    exec_wait_for_visible,
)

STEP_EXECUTORS: dict[str, Callable[[StepContext, StepItem], StepOutcome]] = {
    "launch": exec_launch,
    "reset_app": exec_reset_app,
    "background": exec_background,
    "tap": exec_tap,
    "tap_if_visible": exec_tap_if_visible,
    "type": exec_type,
    "clear": exec_clear,
    "swipe": exec_swipe,
    "scroll_until_visible": exec_scroll_until_visible,
    "wait": exec_wait,
    "wait_for_visible": exec_wait_for_visible,
    "assert_visible": exec_assert_visible,
    "assert_not_visible": exec_assert_not_visible,
    "assert_text": exec_assert_text,
    "back": exec_back,
    "hide_keyboard": exec_hide_keyboard,
    "biometric": exec_biometric,
    "screenshot": exec_screenshot,
}


def execute_step(ctx: StepContext, step: StepItem) -> StepOutcome:
    """Dispatch a normalized StepItem to its handler."""
    fn = STEP_EXECUTORS.get(step.type)
    if fn is None:
        raise ValueError(f"Unsupported step type: {step.type}")
    return fn(ctx, step)


__all__ = ["StepContext", "StepOutcome", "execute_step", "format_step_description"]
