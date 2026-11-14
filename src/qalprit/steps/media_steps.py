"""Media/evidence step handlers: screenshot, visual_compare."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from qalprit.evidence import redact_screenshot_bytes
from qalprit.spec_loader import StepItem
from qalprit.steps.base import StepContext, StepFailureError, StepOutcome, wait_for_element
from qalprit.visual_diff import compare_with_baseline


def _run_visual_comparison(
    ctx: StepContext,
    baseline_name: str,
    *,
    threshold: float = 0.02,
    mask_targets: list[str] | None = None,
    create_if_missing: bool = False,
) -> tuple[str, float, bool, str]:
    raw_png = ctx.driver.get_screenshot_as_png()
    try:
        window_size = ctx.driver.get_window_size()
    except Exception:
        window_size = None

    boxes = ctx.evidence._collect_sensitive_boxes(ctx.driver)
    for target in mask_targets or []:
        try:
            elem, _ = wait_for_element(ctx, target, timeout=0.2)
            if isinstance(getattr(elem, "rect", None), dict):
                boxes.append(elem.rect)
        except Exception:
            continue

    redacted_png, _ = redact_screenshot_bytes(raw_png, boxes, window_size)
    base_root = (ctx.run_dir.parent.parent if ctx.run_dir else Path.cwd()) / "baselines" / ctx.platform
    baseline_path = base_root / f"{baseline_name}.png"
    diff_dir = (ctx.run_dir / "visual_diffs") if ctx.run_dir else (Path.cwd() / "visual_diffs")
    diff_output_path = diff_dir / f"{baseline_name}_diff.png"

    res = compare_with_baseline(
        redacted_png,
        baseline_path,
        diff_output_path,
        threshold=threshold,
        mask_boxes=boxes,
        window_size=window_size,
        create_if_missing=create_if_missing or ctx.update_baselines,
        update_baseline=ctx.update_baselines,
        metadata={
            "platform": ctx.platform,
            "device_name": ctx.profile.device_name,
            "os_version": str(ctx.profile.platform_version or ""),
        },
    )
    rel_diff = ""
    if res.diff_path and ctx.run_dir:
        try:
            rel_diff = Path(res.diff_path).relative_to(ctx.run_dir).as_posix()
        except ValueError:
            rel_diff = str(res.diff_path)
    return rel_diff, res.mismatch_ratio, res.passed, res.message


def exec_screenshot(ctx: StepContext, step: StepItem) -> StepOutcome:
    name = str(step.params["name"])
    baseline = step.params.get("baseline")
    if baseline:
        threshold = float(step.params.get("threshold", 0.02))
        mask_targets = [str(t) for t in (step.params.get("mask_targets") or [])]
        create_if_missing = bool(step.params.get("create_if_missing", False))
        diff_path, score, passed, message = _run_visual_comparison(
            ctx,
            str(baseline),
            threshold=threshold,
            mask_targets=mask_targets,
            create_if_missing=create_if_missing,
        )
        if not passed:
            raise StepFailureError(
                f"Visual diff failed for baseline '{baseline}': {message}",
                failure_category="assertion_failure",
                diagnostics={"baseline": str(baseline), "mismatch_ratio": score, "diff_path": diff_path},
            )
        return StepOutcome(
            screenshot_name=name,
            visual_diff_path=diff_path,
            visual_diff_score=score,
            note=message,
        )
    return StepOutcome(screenshot_name=name)


def exec_visual_compare(ctx: StepContext, step: StepItem) -> StepOutcome:
    baseline = str(step.params.get("baseline") or step.params.get("name"))
    threshold = float(step.params.get("threshold", 0.02))
    mask_targets = [str(t) for t in (step.params.get("mask_targets") or [])]
    create_if_missing = bool(step.params.get("create_if_missing", False))
    diff_path, score, passed, message = _run_visual_comparison(
        ctx,
        baseline,
        threshold=threshold,
        mask_targets=mask_targets,
        create_if_missing=create_if_missing,
    )
    if not passed:
        raise StepFailureError(
            f"Visual diff failed for baseline '{baseline}': {message}",
            failure_category="assertion_failure",
            diagnostics={"baseline": baseline, "mismatch_ratio": score, "diff_path": diff_path},
        )
    return StepOutcome(
        screenshot_name=str(step.params.get("name") or baseline),
        visual_diff_path=diff_path,
        visual_diff_score=score,
        note=message,
    )
