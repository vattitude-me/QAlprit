"""UI interaction step handlers: tap, tap_if_visible, type, clear, swipe, scroll, back, hide_keyboard, enter_otp."""

from __future__ import annotations

import time
from typing import Any

from qalprit.otp import BROKER
from qalprit.spec_loader import StepItem
from qalprit.steps.base import (
    StepContext,
    StepFailureError,
    StepOutcome,
    StepTimeoutError,
    perform_with_stale_retry,
    read_toggle_state,
    toggle_want,
    wait_for_element,
)


def _compute_swipe_coords(
    box: dict[str, int | float],
    direction: str,
    distance: float | None = None,
) -> tuple[int, int, int, int]:
    x = float(box.get("x", 0))
    y = float(box.get("y", 0))
    w = float(box.get("width", 1000))
    h = float(box.get("height", 2000))
    cx = int(x + w * 0.5)
    cy = int(y + h * 0.5)
    span = min(0.9, max(0.1, float(distance if distance is not None else 0.56)))
    margin = (1.0 - span) / 2.0
    low = margin
    high = 1.0 - margin

    if direction == "up":
        return cx, int(y + h * high), cx, int(y + h * low)
    if direction == "down":
        return cx, int(y + h * low), cx, int(y + h * high)
    if direction == "left":
        return int(x + w * high), cy, int(x + w * low), cy
    if direction == "right":
        return int(x + w * low), cy, int(x + w * high), cy
    raise ValueError(f"Unsupported swipe direction: {direction}")


def _perform_swipe(
    ctx: StepContext,
    direction: str,
    target: str | None = None,
    params: dict | None = None,
    *,
    duration: int = 350,
    distance: float | None = None,
) -> None:
    if target:
        elem, _ = wait_for_element(ctx, target, params=params)
        box = elem.rect
    else:
        win = ctx.driver.get_window_size()
        box = {"x": 0, "y": 0, "width": win["width"], "height": win["height"]}
    sx, sy, ex, ey = _compute_swipe_coords(box, direction, distance=distance)
    ctx.driver.swipe(sx, sy, ex, ey, int(duration))


def _tap_element(
    ctx: StepContext,
    elem: Any,
    *,
    offset_x: float | None = None,
    offset_y: float | None = None,
) -> None:
    """Click an element at its center, or at a relative (0..1) offset within its bounds."""
    if offset_x is None and offset_y is None:
        elem.click()
        return
    box = elem.rect
    fx = max(0.0, min(1.0, float(0.5 if offset_x is None else offset_x)))
    fy = max(0.0, min(1.0, float(0.5 if offset_y is None else offset_y)))
    gx = int(round(float(box["x"]) + float(box["width"]) * fx))
    gy = int(round(float(box["y"]) + float(box["height"]) * fy))
    if ctx.platform == "ios":
        ctx.driver.execute_script("mobile: tap", {"x": gx, "y": gy})
    else:
        ctx.driver.execute_script("mobile: clickGesture", {"x": gx, "y": gy})


def exec_tap(ctx: StepContext, step: StepItem) -> StepOutcome:
    target = step.params["target"]
    tpl_params = step.params.get("params")
    timeout = step.params.get("timeout")
    strict_single = bool(step.params.get("strict_single", False))
    offset_x = step.params.get("offset_x")
    offset_y = step.params.get("offset_y")
    _, elem, resolved = perform_with_stale_retry(
        ctx,
        target,
        lambda el, _res: _tap_element(ctx, el, offset_x=offset_x, offset_y=offset_y),
        params=tpl_params,
        timeout=timeout,
        strict_single=strict_single,
    )
    secret_elems = [elem] if (step.secret or resolved.sensitive) else []
    return StepOutcome(secret_elements=secret_elems)


def exec_long_press(ctx: StepContext, step: StepItem) -> StepOutcome:
    """Press and hold an element (context menus, drag handles, multi-select)."""
    target = step.params["target"]
    tpl_params = step.params.get("params")
    timeout = step.params.get("timeout")
    hold_ms = int(step.params.get("duration", 1000))

    def _hold(el: Any, _res: Any) -> None:
        box = el.rect
        gx = int(round(float(box["x"]) + float(box["width"]) / 2))
        gy = int(round(float(box["y"]) + float(box["height"]) / 2))
        if ctx.platform == "ios":
            ctx.driver.execute_script("mobile: touchAndHold", {"x": gx, "y": gy, "duration": hold_ms / 1000.0})
        else:
            ctx.driver.execute_script("mobile: longClickGesture", {"x": gx, "y": gy, "duration": hold_ms})

    _, elem, resolved = perform_with_stale_retry(ctx, target, _hold, params=tpl_params, timeout=timeout)
    secret_elems = [elem] if (step.secret or resolved.sensitive) else []
    return StepOutcome(secret_elements=secret_elems)


def exec_tap_if_visible(ctx: StepContext, step: StepItem) -> StepOutcome:
    target = step.params["target"]
    tpl_params = step.params.get("params")
    timeout = float(step.params.get("timeout", 2.0))
    offset_x = step.params.get("offset_x")
    offset_y = step.params.get("offset_y")
    try:
        _, elem, resolved = perform_with_stale_retry(
            ctx,
            target,
            lambda el, _res: _tap_element(ctx, el, offset_x=offset_x, offset_y=offset_y),
            params=tpl_params,
            timeout=timeout,
        )
    except TimeoutError:
        return StepOutcome(note=f"'{target}' not visible within {timeout}s (no-op)")
    secret_elems = [elem] if (step.secret or resolved.sensitive) else []
    return StepOutcome(secret_elements=secret_elems)


def exec_type(ctx: StepContext, step: StepItem) -> StepOutcome:
    target = step.params["target"]
    tpl_params = step.params.get("params")
    timeout = step.params.get("timeout")
    raw_value = str(step.params["value"])
    is_secret = bool(step.secret or step.params.get("secret", False))
    resolved_text = ctx.substitute_text(raw_value, secret=is_secret)

    # Ensure sensitive text is registered BEFORE any interaction can fail
    resolved_loc = ctx.resolve_locator(target, params=tpl_params)
    if is_secret or resolved_loc.sensitive:
        ctx.masker.add(resolved_text)

    def _type_action(el: Any, _res: Any) -> None:
        el.click()
        if step.params.get("clear_first"):
            el.clear()
        el.send_keys(resolved_text)

    _, elem, resolved = perform_with_stale_retry(
        ctx,
        target,
        _type_action,
        params=tpl_params,
        timeout=timeout,
    )
    secret_elems = [elem] if (is_secret or resolved.sensitive) else []
    return StepOutcome(secret_elements=secret_elems)


def exec_clear(ctx: StepContext, step: StepItem) -> StepOutcome:
    target = step.params["target"]
    tpl_params = step.params.get("params")
    timeout = step.params.get("timeout")
    _, elem, resolved = perform_with_stale_retry(
        ctx,
        target,
        lambda el, _res: el.clear(),
        params=tpl_params,
        timeout=timeout,
    )
    secret_elems = [elem] if (step.secret or resolved.sensitive) else []
    return StepOutcome(secret_elements=secret_elems)


def exec_swipe(ctx: StepContext, step: StepItem) -> StepOutcome:
    direction = step.params["direction"]
    target = step.params.get("target")
    tpl_params = step.params.get("params")
    duration = int(step.params.get("duration", 350))
    distance = step.params.get("distance")
    _perform_swipe(
        ctx,
        direction,
        target=target,
        params=tpl_params,
        duration=duration,
        distance=float(distance) if distance is not None else None,
    )
    return StepOutcome()


def exec_scroll_until_visible(ctx: StepContext, step: StepItem) -> StepOutcome:
    target = step.params["target"]
    tpl_params = step.params.get("params")
    direction = step.params.get("direction", "down")
    max_swipes = int(step.params.get("max_swipes", 8))

    # Content scroll direction -> finger drag direction
    finger_dir_map = {"down": "up", "up": "down", "left": "right", "right": "left"}
    finger_dir = finger_dir_map.get(direction, "up")

    for _ in range(max_swipes + 1):
        try:
            elem, _ = wait_for_element(ctx, target, params=tpl_params, timeout=0.6)
            return StepOutcome(secret_elements=[elem] if step.secret else [])
        except TimeoutError:
            _perform_swipe(ctx, finger_dir)

    raise StepTimeoutError(
        f"Element '{target}' not visible after {max_swipes} scroll_until_visible swipes ({direction})",
        failure_category="wait_timeout",
        diagnostics={"target": target, "direction": direction, "max_swipes": max_swipes},
    )


def exec_back(ctx: StepContext, step: StepItem) -> StepOutcome:
    ctx.driver.back()
    return StepOutcome()


def exec_set_toggle(ctx: StepContext, step: StepItem) -> StepOutcome:
    """Put a switch into the wanted state. Taps only when it is not already there."""
    target = step.params["target"]
    want = toggle_want(step)
    word = "on" if want else "off"
    tpl_params = step.params.get("params")
    timeout = step.params.get("timeout")

    elem, resolved = wait_for_element(ctx, target, params=tpl_params, timeout=timeout)
    state = read_toggle_state(elem)
    if state is None:
        raise StepFailureError(
            f"Cannot read the on/off state of '{target}'. Pick the switch element itself.",
            failure_category="assertion_failure",
            diagnostics={"target": target, "platform": ctx.platform},
        )
    if state != want:
        elem.click()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                state = read_toggle_state(elem)
            except Exception:
                state = None
            if state == want:
                break
            time.sleep(ctx.poll_interval)
        if state != want:
            raise StepFailureError(
                f"'{target}' did not turn {word} after tapping it",
                failure_category="assertion_failure",
                diagnostics={"target": target, "wanted": word, "platform": ctx.platform},
            )
    return StepOutcome(secret_elements=[elem] if resolved.sensitive else [])


def exec_enter_otp(ctx: StepContext, step: StepItem) -> StepOutcome:
    """Pause for a one-time code, then type it into the field (when a target is given).

    If the variable (default ``OTP``) is already set in the environment or dataset, that value is
    used and nothing pauses. Otherwise a person is asked. The code is saved under the variable
    name for later steps (``${OTP}``) and is always masked in reports.
    """
    variable = str(step.params.get("variable") or "OTP")
    target = step.params.get("target")
    tpl_params = step.params.get("params")
    timeout = float(step.params.get("timeout") or 120)

    code = str(ctx.extra_env_vars.get(variable) or "").strip()
    if not code:
        label = str(target or variable)
        code = BROKER.request(label, timeout=timeout)
    ctx.masker.add(code)
    ctx.captured_vars[variable] = code
    if not target:
        return StepOutcome()

    def _type_action(el: Any, _res: Any) -> None:
        el.click()
        el.clear()
        el.send_keys(code)

    _, elem, _resolved = perform_with_stale_retry(
        ctx, target, _type_action, params=tpl_params, timeout=step.params.get("find_timeout")
    )
    return StepOutcome(secret_elements=[elem])


def exec_hide_keyboard(ctx: StepContext, step: StepItem) -> StepOutcome:
    ctx.driver.hide_keyboard()
    return StepOutcome()
