"""Deterministic Pillow-based visual baseline capture and screenshot comparison."""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw


@dataclass
class VisualDiffResult:
    """Outcome of comparing a redacted screenshot against a stored baseline."""

    passed: bool
    mismatch_ratio: float
    threshold: float
    baseline_path: str
    diff_path: str
    baseline_created: bool = False
    message: str = ""


def _apply_mask_boxes(
    img: Image.Image,
    boxes: list[dict[str, int | float]] | None,
    window_size: dict[str, int | float] | None,
) -> Image.Image:
    if not boxes:
        return img
    working = img.copy()
    scale_x = 1.0
    scale_y = 1.0
    if window_size:
        win_w = float(window_size.get("width") or 0)
        win_h = float(window_size.get("height") or 0)
        if win_w > 0 and win_h > 0:
            scale_x = working.width / win_w
            scale_y = working.height / win_h

    draw = ImageDraw.Draw(working)
    for box in boxes:
        x = float(box.get("x", 0))
        y = float(box.get("y", 0))
        w = float(box.get("width", 0))
        h = float(box.get("height", 0))
        if w <= 0 or h <= 0:
            continue
        x0 = max(0, int(round(x * scale_x)))
        y0 = max(0, int(round(y * scale_y)))
        x1 = min(working.width, int(round((x + w) * scale_x)))
        y1 = min(working.height, int(round((y + h) * scale_y)))
        if x1 > x0 and y1 > y0:
            draw.rectangle([x0, y0, x1, y1], fill=(0, 0, 0))
    return working


def compare_with_baseline(
    candidate_png: bytes,
    baseline_path: Path,
    diff_output_path: Path,
    *,
    threshold: float = 0.02,
    pixel_tolerance: int = 16,
    mask_boxes: list[dict[str, int | float]] | None = None,
    window_size: dict[str, int | float] | None = None,
    create_if_missing: bool = False,
    update_baseline: bool = False,
    metadata: dict[str, Any] | None = None,
) -> VisualDiffResult:
    """Compare redacted candidate PNG bytes with baseline_path and write diff image."""
    with Image.open(io.BytesIO(candidate_png)) as raw_cand:
        cand_rgb = _apply_mask_boxes(raw_cand.convert("RGB"), mask_boxes, window_size)

    if update_baseline or (not baseline_path.exists() and create_if_missing):
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        cand_rgb.save(baseline_path, format="PNG")
        meta = dict(metadata or {})
        meta.update({"width": cand_rgb.width, "height": cand_rgb.height})
        baseline_path.with_suffix(".meta.json").write_text(
            json.dumps(meta, indent=2),
            encoding="utf-8",
        )
        return VisualDiffResult(
            passed=True,
            mismatch_ratio=0.0,
            threshold=threshold,
            baseline_path=str(baseline_path),
            diff_path="",
            baseline_created=True,
            message=f"Saved baseline image ({cand_rgb.width}x{cand_rgb.height}) at {baseline_path.name}",
        )

    if not baseline_path.exists():
        return VisualDiffResult(
            passed=False,
            mismatch_ratio=1.0,
            threshold=threshold,
            baseline_path=str(baseline_path),
            diff_path="",
            baseline_created=False,
            message=f"Visual baseline not found: {baseline_path}",
        )

    notes: list[str] = []
    with Image.open(baseline_path) as raw_base:
        base_rgb = _apply_mask_boxes(raw_base.convert("RGB"), mask_boxes, window_size)

    if cand_rgb.size != base_rgb.size:
        notes.append(f"Normalized candidate size {cand_rgb.size} -> baseline {base_rgb.size}")
        cand_rgb = cand_rgb.resize(base_rgb.size, Image.Resampling.LANCZOS)

    diff = ImageChops.difference(base_rgb, cand_rgb)
    diff_pixels = diff.get_flattened_data() if hasattr(diff, "get_flattened_data") else diff.getdata()
    total_pixels = max(1, base_rgb.width * base_rgb.height)
    mismatch_count = 0
    for r, g, b in diff_pixels:
        if r > pixel_tolerance or g > pixel_tolerance or b > pixel_tolerance:
            mismatch_count += 1

    mismatch_ratio = mismatch_count / float(total_pixels)
    passed = mismatch_ratio <= threshold

    # Build a clear highlight diff image (dimmed base + bright magenta changed pixels)
    highlight = base_rgb.point(lambda c: int(c * 0.35))
    mask = diff.convert("L").point(lambda p: 255 if p > pixel_tolerance else 0)
    overlay = Image.new("RGB", base_rgb.size, (255, 0, 128))
    highlight.paste(overlay, mask=mask)
    diff_output_path.parent.mkdir(parents=True, exist_ok=True)
    highlight.save(diff_output_path, format="PNG")

    summary = f"mismatch={mismatch_ratio:.4%} (threshold={threshold:.4%}, pixels={mismatch_count}/{total_pixels})"
    if notes:
        summary += f"; {'; '.join(notes)}"
    return VisualDiffResult(
        passed=passed,
        mismatch_ratio=round(mismatch_ratio, 6),
        threshold=threshold,
        baseline_path=str(baseline_path),
        diff_path=str(diff_output_path),
        baseline_created=False,
        message=summary,
    )
