"""App lifecycle step handlers: launch, reset_app, background."""

from __future__ import annotations

from qalprit.spec_loader import StepItem
from qalprit.steps.base import StepContext, StepOutcome


def exec_launch(ctx: StepContext, step: StepItem) -> StepOutcome:
    if not ctx.app_id:
        raise ValueError(
            f"No app identifier configured for platform '{ctx.platform}' in environments.yaml"
        )
    ctx.driver.activate_app(ctx.app_id)
    return StepOutcome()


def exec_reset_app(ctx: StepContext, step: StepItem) -> StepOutcome:
    res = ctx.reset_fn(ctx.reset_mode)
    if isinstance(res, tuple) and len(res) == 2 and res[0] is False:
        raise RuntimeError(f"Application reset ({ctx.reset_mode}) failed: {res[1]}")
    return StepOutcome()


def exec_background(ctx: StepContext, step: StepItem) -> StepOutcome:
    seconds = float(step.params["seconds"])
    ctx.driver.background_app(seconds)
    if ctx.app_id:
        ctx.driver.activate_app(ctx.app_id)
    return StepOutcome()
