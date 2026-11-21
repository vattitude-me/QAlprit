"""App lifecycle step handlers: launch, reset_app, background."""

from __future__ import annotations

import re

from qalprit.spec_loader import StepItem
from qalprit.steps.base import StepContext, StepOutcome


_APP_ID_RE = re.compile(r"^[A-Za-z][\w-]*(\.[A-Za-z_][\w-]*)+$")


def exec_launch(ctx: StepContext, step: StepItem) -> StepOutcome:
    """Open the app under test, or the installed app named in the step (`launch: com.example.app`).

    With `restart: true` the app is closed first, so it opens on its first screen and keeps its data.
    """
    named = str(step.params.get("app") or "").strip()
    app_id = named if _APP_ID_RE.match(named) else ctx.app_id
    if not app_id:
        raise ValueError(
            f"No app identifier configured for platform '{ctx.platform}' in environments.yaml"
        )
    if step.params.get("restart"):
        try:
            ctx.driver.terminate_app(app_id)
        except Exception:
            pass
    ctx.driver.activate_app(app_id)
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
