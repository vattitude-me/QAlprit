"""Wait and assertion step handlers."""

from __future__ import annotations

import time

from qalprit.spec_loader import StepItem
from qalprit.steps.base import (
    StepContext,
    StepOutcome,
    wait_for_element,
    wait_until_not_visible,
)


def exec_wait(ctx: StepContext, step: StepItem) -> StepOutcome:
    seconds = float(step.params["seconds"])
    time.sleep(seconds)
    return StepOutcome()


def exec_wait_for_visible(ctx: StepContext, step: StepItem) -> StepOutcome:
    target = step.params["target"]
    tpl_params = step.params.get("params")
    timeout = step.params.get("timeout")
    elem, resolved = wait_for_element(ctx, target, params=tpl_params, timeout=timeout)
    return StepOutcome(secret_elements=[elem] if resolved.sensitive else [])


def exec_assert_visible(ctx: StepContext, step: StepItem) -> StepOutcome:
    target = step.params["target"]
    tpl_params = step.params.get("params")
    timeout = step.params.get("timeout")
    elem, resolved = wait_for_element(ctx, target, params=tpl_params, timeout=timeout)
    return StepOutcome(secret_elements=[elem] if resolved.sensitive else [])


def exec_assert_not_visible(ctx: StepContext, step: StepItem) -> StepOutcome:
    target = step.params["target"]
    tpl_params = step.params.get("params")
    timeout = step.params.get("timeout")
    wait_until_not_visible(ctx, target, params=tpl_params, timeout=timeout)
    return StepOutcome()


def exec_assert_text(ctx: StepContext, step: StepItem) -> StepOutcome:
    target = step.params["target"]
    tpl_params = step.params.get("params")
    timeout = step.params.get("timeout")
    elem, resolved = wait_for_element(ctx, target, params=tpl_params, timeout=timeout)
    actual = elem.text or ""

    if "equals" in step.params:
        expected = ctx.substitute_text(str(step.params["equals"]))
        if actual != expected:
            raise AssertionError(
                f"Text mismatch on '{target}': expected equals {expected!r}, got {actual!r}"
            )
    elif "contains" in step.params:
        expected = ctx.substitute_text(str(step.params["contains"]))
        if expected not in actual:
            raise AssertionError(
                f"Text mismatch on '{target}': expected to contain {expected!r}, got {actual!r}"
            )

    return StepOutcome(secret_elements=[elem] if resolved.sensitive else [])
