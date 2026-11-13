"""Media/evidence step handlers: screenshot."""

from __future__ import annotations

from qalprit.spec_loader import StepItem
from qalprit.steps.base import StepContext, StepOutcome


def exec_screenshot(ctx: StepContext, step: StepItem) -> StepOutcome:
    name = str(step.params["name"])
    return StepOutcome(screenshot_name=name)
