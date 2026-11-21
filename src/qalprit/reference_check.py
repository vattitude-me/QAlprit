"""Compare each replayed step's screenshot with the one saved when the flow was recorded.

The recorder writes `references/flows/<flow>/manifest.json` and one PNG per step. A run looks
up the screenshot for the same flow step, copies it next to the replay screenshot (so the run
folder is complete on its own) and stores a pixel-difference score. The score is a signal for
review and for the AI check; it never fails a step on its own.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from qalprit.recorder_paths import step_references_dir
from qalprit.visual_diff import compare_with_baseline

# Above this share of changed pixels a screen is shown as "changed" in reports.
CHANGED_THRESHOLD = 0.08


@dataclass
class ReferenceMatch:
    screenshot_path: str  # relative to the run folder
    diff_path: str  # relative to the run folder, '' when not written
    mismatch: float | None
    label: str
    note: str = ""


class ReferenceLibrary:
    """Loads recorded step screenshots for flows, once per run."""

    def __init__(self, project_root: Path | None) -> None:
        self.project_root = project_root
        self._cache: dict[str, dict[str, Any] | None] = {}

    def manifest(self, flow_name: str) -> dict[str, Any] | None:
        if self.project_root is None or not flow_name:
            return None
        if flow_name not in self._cache:
            path = step_references_dir(self.project_root, flow_name) / "manifest.json"
            try:
                self._cache[flow_name] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._cache[flow_name] = None
        return self._cache[flow_name]

    def reference_for(self, flow_name: str, flow_index: int, action: str, target: str | None) -> tuple[Path | None, str, str]:
        """Return (png path, step label, note). The path is None when there is nothing to compare."""
        man = self.manifest(flow_name)
        if not man or self.project_root is None:
            return None, "", ""
        steps = man.get("steps") or []
        if not (0 <= flow_index < len(steps)):
            return None, "", "The workflow has more steps than when it was recorded."
        entry = steps[flow_index]
        label = str(entry.get("label") or "")
        if str(entry.get("action") or "") != action or (
            entry.get("target") and target and str(entry["target"]) != str(target)
        ):
            return None, label, "The workflow was edited after recording, so this step has no recorded screenshot."
        name = str(entry.get("screenshot") or "")
        if not name:
            return None, label, ""
        png = step_references_dir(self.project_root, flow_name) / name
        return (png if png.is_file() else None), label, ""

    def compare(
        self,
        *,
        run_dir: Path,
        replay_rel: str,
        flow_name: str,
        flow_index: int,
        action: str,
        target: str | None,
    ) -> ReferenceMatch | None:
        ref_png, label, note = self.reference_for(flow_name, flow_index, action, target)
        if ref_png is None:
            return ReferenceMatch("", "", None, label, note) if note else None
        replay_path = run_dir / replay_rel
        ref_copy = replay_path.with_name(replay_path.stem + "_REF.png")
        diff_out = replay_path.with_name(replay_path.stem + "_REFDIFF.png")
        shutil.copyfile(ref_png, ref_copy)
        try:
            res = compare_with_baseline(replay_path.read_bytes(), ref_copy, diff_out, threshold=CHANGED_THRESHOLD)
        except Exception as exc:  # unreadable image: keep the reference, skip the score
            return ReferenceMatch(ref_copy.relative_to(run_dir).as_posix(), "", None, label, f"Could not compare: {exc}")
        diff_rel = diff_out.relative_to(run_dir).as_posix() if res.diff_path else ""
        return ReferenceMatch(ref_copy.relative_to(run_dir).as_posix(), diff_rel, res.mismatch_ratio, label)
