"""Where recorded step screenshots live in a workspace. No other qalprit imports, to avoid cycles."""

from __future__ import annotations

import re
from pathlib import Path

REFERENCES_DIR = "references"
_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _slug(raw: str, default: str) -> str:
    cleaned = _SLUG_RE.sub("_", (raw or "").strip()).strip("._-")
    return cleaned or default


def step_references_dir(project_root: Path, flow_name: str) -> Path:
    """Folder holding a recorded flow's step screenshots: references/flows/<flow>/."""
    safe = "/".join(_slug(part, "flow") for part in str(flow_name).split("/") if part)
    return project_root / REFERENCES_DIR / "flows" / (safe or "flow")


def reference_docs_dir(project_root: Path) -> Path:
    """Folder holding fetched Jira issues and Confluence pages: references/docs/."""
    return project_root / REFERENCES_DIR / "docs"
