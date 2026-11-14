"""UI interaction step handlers: tap, tap_if_visible, type, clear, swipe, scroll, back, hide_keyboard."""

from __future__ import annotations

from typing import Any

from qalprit.spec_loader import StepItem
from qalprit.steps.base import (
    StepContext,
    StepOutcome,
    StepTimeoutError,
    perform_with_stale_retry,
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


def exec_tap(ctx: StepContext, step: StepItem) -> StepOutcome:
    target = step.params["target"]
    tpl_params = step.params.get("params")
    timeout = step.params.get("timeout")
    strict_single = bool(step.params.get("strict_single", False))
    _, elem, resolved = perform_with_stale_retry(
        ctx,
        target,
        lambda el, _res: el.click(),
        params=tpl_params,
        timeout=timeout,
        strict_single=strict_single,
    )
    secret_elems = [elem] if (step.secret or resolved.sensitive) else []
    return StepOutcome(secret_elements=secret_elems)


def exec_tap_if_visible(ctx: StepContext, step: StepItem) -> StepOutcome:
    target = step.params["target"]
    tpl_params = step.params.get("params")
    timeout = float(step.params.get("timeout", 2.0))
    try:
        _, elem, resolved = perform_with_stale_retry(
            ctx,
            target,
            lambda el, _res: el.click(),
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


def exec_hide_keyboard(ctx: StepContext, step: StepItem) -> StepOutcome:
    ctx.driver.hide_keyboard()
    return StepOutcome()
