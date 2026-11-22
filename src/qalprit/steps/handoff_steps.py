"""Steps a person finishes by hand: ``wait_for_user`` (and ``enter_otp``, which builds on it)."""

from __future__ import annotations

from typing import Any

from qalprit.handoff import BROKER, HandoffFailedError, HandoffUnavailableError
from qalprit.spec_loader import StepItem
from qalprit.steps.base import (
    StepContext,
    StepFailureError,
    StepOutcome,
    StepTimeoutError,
    perform_with_stale_retry,
    wait_for_element,
)

DEFAULT_INPUT_TIMEOUT = 120.0
DEFAULT_ACTION_TIMEOUT = 300.0


def keep_session_alive(ctx: StepContext) -> bool:
    """Touch the driver so Appium does not end an idle session during a long wait."""
    try:
        ctx.driver.get_window_size()
    except Exception:
        pass
    return False


def _visible_now(ctx: StepContext, target: str, params: dict[str, Any] | None) -> bool:
    try:
        wait_for_element(ctx, target, params=params, timeout=0)
    except (StepTimeoutError, StepFailureError):
        return False
    return True


def ask_person(
    ctx: StepContext,
    *,
    label: str,
    kind: str,
    prompt: str,
    timeout: float,
    secret: bool,
    until: str = "",
    until_params: dict[str, Any] | None = None,
    can_skip: bool = False,
) -> Any:
    """Ask the broker and turn its errors into step failures the report can explain."""
    poll = (lambda: _visible_now(ctx, until, until_params)) if until else (lambda: keep_session_alive(ctx))
    try:
        return BROKER.request(
            label,
            timeout=timeout,
            kind=kind,  # type: ignore[arg-type]
            prompt=prompt,
            secret=secret,
            until=until,
            can_skip=can_skip,
            poll=poll,
        )
    except HandoffFailedError as exc:
        raise StepFailureError(
            str(exc),
            failure_category="application_failure",
            diagnostics={"manual": True, "prompt": prompt, "answer": "fail"},
        ) from exc
    except HandoffUnavailableError as exc:
        raise StepFailureError(
            str(exc),
            failure_category="environment_configuration",
            diagnostics={"manual": True, "prompt": prompt, "answer": "none"},
        ) from exc


def type_into(ctx: StepContext, target: str, value: str, params: Any = None, find_timeout: Any = None) -> Any:
    def _type_action(el: Any, _res: Any) -> None:
        el.click()
        el.clear()
        el.send_keys(value)

    _, elem, _resolved = perform_with_stale_retry(ctx, target, _type_action, params=params, timeout=find_timeout)
    return elem


def exec_wait_for_user(ctx: StepContext, step: StepItem) -> StepOutcome:
    """Pause until a person has done something on the device, or typed a value.

    With ``input: NAME`` the person types a value, saved as ``${NAME}`` and typed into ``target``
    when one is given. A value already set in the environment or dataset is used without asking.
    Without ``input`` the person does the action on the device and presses Done. ``until`` names
    an element that ends the wait on its own when it shows up, and is checked after Done.
    """
    p = step.params
    prompt = ctx.substitute_text(str(p.get("prompt") or "").strip()) or "Finish this step on the device"
    var = str(p.get("input") or "").strip()
    target = str(p.get("target") or "").strip()
    until = str(p.get("until") or "").strip()
    tpl_params = p.get("params")
    secret = bool(p.get("secret", step.secret))
    can_skip = bool(p.get("optional", False))
    default_timeout = DEFAULT_INPUT_TIMEOUT if var else DEFAULT_ACTION_TIMEOUT
    timeout = float(p.get("timeout") or default_timeout)
    diag: dict[str, Any] = {"manual": True, "prompt": prompt, "kind": "input" if var else "action"}

    if var:
        value = str(ctx.extra_env_vars.get(var) or "").strip()
        if value:
            diag["answer"] = "preset"
        else:
            res = ask_person(ctx, label=var, kind="input", prompt=prompt, timeout=timeout, secret=secret, can_skip=can_skip)
            diag.update(answer=res.answer, waited_sec=res.waited_sec)
            if res.answer == "skip":
                return StepOutcome(status="skipped", note="Skipped by the person running the test", diagnostics=diag)
            value = res.value
        if secret:
            ctx.masker.add(value)
        ctx.captured_vars[var] = value
        if not target:
            return StepOutcome(diagnostics=diag)
        elem = type_into(ctx, target, value, tpl_params, p.get("find_timeout"))
        return StepOutcome(secret_elements=[elem] if secret else [], diagnostics=diag)

    res = ask_person(
        ctx,
        label=prompt,
        kind="action",
        prompt=prompt,
        timeout=timeout,
        secret=False,
        until=until,
        until_params=tpl_params,
        can_skip=can_skip,
    )
    diag.update(answer=res.answer, waited_sec=res.waited_sec)
    if until:
        diag["until"] = until
    if res.answer == "skip":
        return StepOutcome(status="skipped", note="Skipped by the person running the test", diagnostics=diag)
    if res.answer == "done" and until:
        try:
            wait_for_element(ctx, until, params=tpl_params, timeout=p.get("find_timeout") or 15)
        except StepTimeoutError as exc:
            raise StepFailureError(
                f"Done was pressed, but '{until}' did not appear. Check the device and the 'until' locator.",
                failure_category="assertion_failure",
                diagnostics=dict(diag, **(getattr(exc, "diagnostics", {}) or {})),
            ) from exc
    return StepOutcome(diagnostics=diag)
