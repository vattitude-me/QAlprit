"""Steps that hold other steps: ``if_visible`` and ``repeat``.

The runner runs the nested steps itself, one row each in the report, so they get the same
screenshots, retries and failure handling as any other step. This module only decides which
branch to take and when a ``repeat`` is finished.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from qalprit.spec_loader import DEFAULT_REPEAT_VAR, StepItem
from qalprit.steps.base import StepContext, StepFailureError, StepTimeoutError, wait_for_element

DEFAULT_IF_TIMEOUT = 3.0
DEFAULT_UNTIL_TIMEOUT = 1.5
DEFAULT_MAX_ROUNDS = 10
MAX_ROUNDS_LIMIT = 100

# Internal step the runner queues after each round of a ``repeat`` to decide on the next one.
REPEAT_NEXT = "__repeat_next__"


def is_visible(ctx: StepContext, target: str, params: dict[str, Any] | None, timeout: float) -> bool:
    """True when ``target`` shows up within ``timeout`` seconds. An unknown locator still raises."""
    try:
        wait_for_element(ctx, target, params=params, timeout=timeout)
    except StepTimeoutError:
        return False
    return True


def if_visible_branch(ctx: StepContext, step: StepItem) -> tuple[bool, str]:
    """Check the condition of an ``if_visible`` step. Returns (shown, "then" or "else")."""
    target = str(step.params.get("target") or "").strip()
    if not target:
        raise ValueError("'if_visible' requires 'target'")
    timeout = float(step.params.get("timeout", DEFAULT_IF_TIMEOUT))
    shown = is_visible(ctx, target, step.params.get("params"), timeout)
    return shown, ("then" if shown else "else")


@dataclass
class RepeatPlan:
    max_rounds: int
    until: str = ""
    until_params: dict[str, Any] | None = None
    until_timeout: float = DEFAULT_UNTIL_TIMEOUT
    var: str = DEFAULT_REPEAT_VAR


def repeat_plan(step: StepItem) -> RepeatPlan:
    """Read ``times``, ``until``, ``max`` and ``as`` from a ``repeat`` step.

    ``times: 3`` runs the steps three times. ``until: screen.element`` runs them until that
    element shows up, checked before every round, and fails when it has not shown up after
    ``max`` rounds (or ``times``, when both are given). Default ``max`` is 10.
    """
    p = step.params
    until = str(p.get("until") or "").strip()
    raw_times = p.get("times")
    raw_max = p.get("max")
    if raw_times is None and not until:
        raise ValueError("'repeat' needs 'times' or 'until'")
    limit = raw_times if raw_times is not None else (raw_max if raw_max is not None else DEFAULT_MAX_ROUNDS)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError(f"'repeat' rounds must be a whole number of 1 or more, got {limit!r}")
    return RepeatPlan(
        max_rounds=min(int(limit), MAX_ROUNDS_LIMIT),
        until=until,
        until_params=p.get("params"),
        until_timeout=float(p.get("until_timeout", DEFAULT_UNTIL_TIMEOUT)),
        var=str(p.get("as") or DEFAULT_REPEAT_VAR).strip() or DEFAULT_REPEAT_VAR,
    )


def next_round(ctx: StepContext, plan: RepeatPlan, rounds_done: int) -> tuple[int | None, str]:
    """Decide what a ``repeat`` does after ``rounds_done`` rounds.

    Returns (round number to run next, "") to keep going, or (None, summary) when it is done.
    Raises StepFailureError when ``until`` never showed up.
    """
    if plan.until and is_visible(ctx, plan.until, plan.until_params, plan.until_timeout):
        word = "round" if rounds_done == 1 else "rounds"
        return None, f"'{plan.until}' is shown after {rounds_done} {word}"
    if rounds_done >= plan.max_rounds:
        if plan.until:
            raise StepFailureError(
                f"'{plan.until}' did not appear after {rounds_done} rounds. "
                "Raise 'max', or check the steps and the 'until' locator.",
                failure_category="assertion_failure",
                diagnostics={"until": plan.until, "rounds": rounds_done},
            )
        word = "round" if rounds_done == 1 else "rounds"
        return None, f"{rounds_done} {word} passed"
    round_no = rounds_done + 1
    ctx.captured_vars[plan.var] = str(round_no)
    return round_no, ""


def repeat_next_step(block: StepItem, plan: RepeatPlan, rounds_done: int) -> StepItem:
    return StepItem(
        type=REPEAT_NEXT,
        params={"block": block, "plan": plan, "rounds_done": rounds_done},
        capture="off",
        flow_name=block.flow_name,
    )
