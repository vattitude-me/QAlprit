"""Wait and assertion step handlers."""

from __future__ import annotations

import re
import time

from qalprit.spec_loader import StepItem
from qalprit.steps.base import (
    StepContext,
    StepFailureError,
    StepOutcome,
    StepTimeoutError,
    perform_with_stale_retry,
    wait_for_element,
    wait_until_not_visible,
)


def _match_text_condition(actual: str, step: StepItem, ctx: StepContext) -> tuple[bool, str]:
    """Evaluate equals/contains/regex expectation on text."""
    if "equals" in step.params:
        expected = ctx.substitute_text(str(step.params["equals"]))
        return actual == expected, f"equals {ctx.masker.mask(repr(expected))}"
    regex_pat = step.params.get("regex") if "regex" in step.params else step.params.get("matches")
    if regex_pat is not None:
        pattern = ctx.substitute_text(str(regex_pat))
        return bool(re.search(pattern, actual)), f"matches /{ctx.masker.mask(pattern)}/"
    expected = ctx.substitute_text(str(step.params.get("contains", "")))
    return expected in actual, f"contains {ctx.masker.mask(repr(expected))}"


def exec_wait(ctx: StepContext, step: StepItem) -> StepOutcome:
    seconds = float(step.params["seconds"])
    time.sleep(seconds)
    return StepOutcome()


def exec_wait_for_visible(ctx: StepContext, step: StepItem) -> StepOutcome:
    target = step.params["target"]
    tpl_params = step.params.get("params")
    timeout = step.params.get("timeout")
    strict_single = bool(step.params.get("strict_single", False))
    elem, resolved = wait_for_element(
        ctx,
        target,
        params=tpl_params,
        timeout=timeout,
        strict_single=strict_single,
        failure_category="wait_timeout",
    )
    return StepOutcome(secret_elements=[elem] if resolved.sensitive else [])


def exec_wait_for_enabled(ctx: StepContext, step: StepItem) -> StepOutcome:
    target = step.params["target"]
    tpl_params = step.params.get("params")
    timeout = step.params.get("timeout")
    strict_single = bool(step.params.get("strict_single", False))
    elem, resolved = wait_for_element(
        ctx,
        target,
        params=tpl_params,
        timeout=timeout,
        require_displayed=True,
        require_enabled=True,
        strict_single=strict_single,
        failure_category="wait_timeout",
    )
    return StepOutcome(secret_elements=[elem] if resolved.sensitive else [])


def exec_wait_for_text(ctx: StepContext, step: StepItem) -> StepOutcome:
    target = step.params["target"]
    tpl_params = step.params.get("params")
    wait_seconds = float(
        ctx.env_cfg.default_timeout if step.params.get("timeout") is None else step.params["timeout"]
    )
    sleep_step = min(ctx.poll_interval, max(0.01, wait_seconds / 4.0)) if wait_seconds > 0 else 0.0
    deadline = time.monotonic() + max(0.0, wait_seconds)
    last_actual = ""
    last_desc = ""
    elem = None
    resolved = None

    while True:
        remaining = max(0.0, deadline - time.monotonic())
        elem, resolved = wait_for_element(
            ctx,
            target,
            params=tpl_params,
            timeout=remaining,
            failure_category="wait_timeout",
        )
        try:
            last_actual = elem.text or ""
        except Exception:
            last_actual = ""
        matched, last_desc = _match_text_condition(last_actual, step, ctx)
        if matched:
            return StepOutcome(secret_elements=[elem] if resolved.sensitive else [])
        if time.monotonic() >= deadline:
            break
        if sleep_step > 0:
            time.sleep(sleep_step)

    masked_actual = "***REDACTED***" if (step.secret or (resolved and resolved.sensitive)) else ctx.masker.mask(last_actual)
    raise StepTimeoutError(
        f"Timed out after {wait_seconds:.1f}s waiting for '{target}' text to satisfy {last_desc}; last seen {masked_actual!r}",
        failure_category="wait_timeout",
        diagnostics={"target": target, "last_text": masked_actual, "timeout_s": wait_seconds},
    )


def exec_wait_for_keyboard(ctx: StepContext, step: StepItem) -> StepOutcome:
    if "shown" in step.params:
        expected_shown = bool(step.params["shown"])
    elif "visible" in step.params:
        expected_shown = bool(step.params["visible"])
    else:
        expected_shown = True
    wait_seconds = float(
        ctx.env_cfg.default_timeout if step.params.get("timeout") is None else step.params["timeout"]
    )
    sleep_step = min(ctx.poll_interval, max(0.01, wait_seconds / 4.0)) if wait_seconds > 0 else 0.0
    deadline = time.monotonic() + max(0.0, wait_seconds)

    def _is_shown() -> bool:
        fn = getattr(ctx.driver, "is_keyboard_shown", None)
        if callable(fn):
            return bool(fn())
        return False

    while True:
        current = _is_shown()
        if current == expected_shown:
            return StepOutcome()
        if time.monotonic() >= deadline:
            break
        if sleep_step > 0:
            time.sleep(sleep_step)

    state_label = "shown" if expected_shown else "hidden"
    raise StepTimeoutError(
        f"Timed out after {wait_seconds:.1f}s waiting for soft keyboard to be {state_label}",
        failure_category="wait_timeout",
        diagnostics={"expected_keyboard_shown": expected_shown, "timeout_s": wait_seconds},
    )


def exec_assert_visible(ctx: StepContext, step: StepItem) -> StepOutcome:
    target = step.params["target"]
    tpl_params = step.params.get("params")
    timeout = step.params.get("timeout")
    strict_single = bool(step.params.get("strict_single", False))
    try:
        elem, resolved = wait_for_element(
            ctx,
            target,
            params=tpl_params,
            timeout=timeout,
            strict_single=strict_single,
            failure_category="assertion_failure",
        )
    except StepTimeoutError as exc:
        raise StepFailureError(
            str(exc),
            failure_category="assertion_failure",
            diagnostics=exc.diagnostics,
        ) from exc
    return StepOutcome(secret_elements=[elem] if resolved.sensitive else [])


def exec_assert_not_visible(ctx: StepContext, step: StepItem) -> StepOutcome:
    target = step.params["target"]
    tpl_params = step.params.get("params")
    timeout = step.params.get("timeout")
    wait_until_not_visible(ctx, target, params=tpl_params, timeout=timeout)
    return StepOutcome()


def exec_assert_enabled(ctx: StepContext, step: StepItem) -> StepOutcome:
    target = step.params["target"]
    tpl_params = step.params.get("params")
    timeout = step.params.get("timeout")
    _, elem, resolved = perform_with_stale_retry(
        ctx,
        target,
        lambda el, _res: el.is_enabled() if hasattr(el, "is_enabled") else True,
        params=tpl_params,
        timeout=timeout,
        failure_category="assertion_failure",
    )
    if hasattr(elem, "is_enabled") and not elem.is_enabled():
        raise StepFailureError(
            f"Assertion failed on '{target}': expected element to be enabled, but it is disabled",
            failure_category="assertion_failure",
            diagnostics={"target": target, "platform": ctx.platform},
        )
    return StepOutcome(secret_elements=[elem] if resolved.sensitive else [])


def exec_assert_disabled(ctx: StepContext, step: StepItem) -> StepOutcome:
    target = step.params["target"]
    tpl_params = step.params.get("params")
    timeout = step.params.get("timeout")
    _, elem, resolved = perform_with_stale_retry(
        ctx,
        target,
        lambda el, _res: el.is_enabled() if hasattr(el, "is_enabled") else False,
        params=tpl_params,
        timeout=timeout,
        failure_category="assertion_failure",
    )
    if hasattr(elem, "is_enabled") and elem.is_enabled():
        raise StepFailureError(
            f"Assertion failed on '{target}': expected element to be disabled, but it is enabled",
            failure_category="assertion_failure",
            diagnostics={"target": target, "platform": ctx.platform},
        )
    return StepOutcome(secret_elements=[elem] if resolved.sensitive else [])


def exec_assert_text(ctx: StepContext, step: StepItem) -> StepOutcome:
    target = step.params["target"]
    tpl_params = step.params.get("params")
    timeout = step.params.get("timeout")
    actual, elem, resolved = perform_with_stale_retry(
        ctx,
        target,
        lambda el, _res: el.text or "",
        params=tpl_params,
        timeout=timeout,
        failure_category="assertion_failure",
    )

    matched, expectation_desc = _match_text_condition(actual, step, ctx)
    if not matched:
        masked_actual = "***REDACTED***" if (step.secret or resolved.sensitive) else ctx.masker.mask(actual)
        raise StepFailureError(
            f"Text mismatch on '{target}': expected {expectation_desc}, got {masked_actual!r}",
            failure_category="assertion_failure",
            diagnostics={"target": target, "actual": masked_actual},
        )

    return StepOutcome(secret_elements=[elem] if resolved.sensitive else [])
