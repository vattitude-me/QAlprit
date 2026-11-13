"""Lightweight local web workbench server for QAlprit (`qalprit ui`)."""

from __future__ import annotations

import json
import mimetypes
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from qalprit import __version__
from qalprit.config_loader import load_screens
from qalprit.results import load_results_json
from qalprit.spec_loader import load_flows, load_specs

_UI_DIR = Path(__file__).parent / "ui"
_UI_HTML_PATH = _UI_DIR / "index.html"
_UI_ICON_PATH = _UI_DIR / "qalprit-icon.svg"


def collect_workspace_overview(
    project_root: Path,
    results_dir: Path | None = None,
) -> dict[str, Any]:
    """Collect runs, specs, flows, and screen locators for the local workbench UI."""
    root = project_root.resolve()
    res_dir = (results_dir or (root / "results")).resolve()

    runs: list[dict[str, Any]] = []
    if res_dir.exists() and res_dir.is_dir():
        for entry in sorted(res_dir.iterdir(), key=lambda p: p.name, reverse=True):
            if not entry.is_dir():
                continue
            json_path = entry / "results.json"
            if not json_path.exists():
                continue
            try:
                run = load_results_json(json_path)
                runs.append(
                    {
                        "run_id": run.run_id,
                        "started_at": run.started_at,
                        "finished_at": run.finished_at,
                        "environment": run.environment,
                        "platform": run.platform,
                        "device_name": run.device_name,
                        "target_type": run.target_type,
                        "suite": run.suite,
                        "summary": run.summary.model_dump(),
                        "has_html": (entry / "report.html").exists(),
                        "has_md": (entry / "summary.md").exists(),
                    }
                )
            except Exception:
                continue

    specs, _ = load_specs(root / "specs")
    flows, _ = load_flows(root / "flows")

    screens_list: list[dict[str, Any]] = []
    screens_path = root / "screens" / "screens.yaml"
    if screens_path.exists():
        try:
            catalog = load_screens(screens_path)
            for target in catalog.all_targets():
                platforms: dict[str, dict[str, Any]] = {}
                is_sensitive = False
                for plat in ("android", "ios"):
                    if catalog.has_target(target, plat):
                        entry = catalog.get_entry(target, plat)
                        strategy, val = entry.strategy_and_value()
                        platforms[plat] = {"strategy": strategy, "value": val}
                        if entry.sensitive:
                            is_sensitive = True
                screens_list.append(
                    {
                        "target": target,
                        "sensitive": is_sensitive,
                        "platforms": platforms,
                    }
                )
        except Exception:
            pass

    return {
        "tool": "QAlprit",
        "version": __version__,
        "tagline": "Mobile QA. Bugs caught with proof.",
        "project_root": str(root),
        "runs": runs,
        "specs": [
            {
                "id": s.id,
                "title": s.title,
                "suite": s.suite,
                "tags": s.tags,
                "platforms": s.platforms,
                "targets": s.targets,
                "priority": s.priority,
                "reset": s.reset,
                "step_count": len(s.steps),
                "teardown_count": len(s.teardown),
                "source_path": s.source_path.relative_to(root).as_posix()
                if s.source_path.is_relative_to(root)
                else str(s.source_path),
            }
            for s in specs
        ],
        "flows": [
            {
                "name": f.name,
                "params": f.params,
                "step_count": len(f.steps),
                "source_path": f.source_path.relative_to(root).as_posix()
                if f.source_path.is_relative_to(root)
                else str(f.source_path),
            }
            for f in sorted(flows.values(), key=lambda x: x.name)
        ],
        "screens": screens_list,
    }


class _WorkbenchHandler(BaseHTTPRequestHandler):
    def __init__(
        self,
        *args: Any,
        project_root: Path,
        results_dir: Path,
        **kwargs: Any,
    ) -> None:
        self.project_root = project_root.resolve()
        self.results_dir = results_dir.resolve()
        super().__init__(*args, **kwargs)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def _send_bytes(self, body: bytes, content_type: str, status: int = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        req_path = unquote(parsed.path)

        if req_path in ("/", "/index.html"):
            if not _UI_HTML_PATH.exists():
                self._send_bytes(b"UI template missing", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                return
            self._send_bytes(_UI_HTML_PATH.read_bytes(), "text/html; charset=utf-8")
            return

        if req_path in ("/qalprit-icon.svg", "/favicon.ico"):
            if not _UI_ICON_PATH.exists():
                self._send_bytes(b"Icon missing", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                return
            self._send_bytes(_UI_ICON_PATH.read_bytes(), "image/svg+xml; charset=utf-8")
            return

        if req_path == "/api/overview":
            payload = collect_workspace_overview(self.project_root, self.results_dir)
            self._send_bytes(
                json.dumps(payload, indent=2).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if req_path.startswith("/results/"):
            rel = req_path[len("/results/") :].lstrip("/")
            if not rel:
                self._send_bytes(b"Not found", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                return
            candidate = (self.results_dir / rel).resolve()
            if not candidate.is_relative_to(self.results_dir) or not candidate.is_file():
                self._send_bytes(b"Not found", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                return
            mime, _ = mimetypes.guess_type(candidate.name)
            self._send_bytes(candidate.read_bytes(), mime or "application/octet-stream")
            return

        self._send_bytes(b"Not found", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)


def create_ui_server(
    project_root: Path,
    results_dir: Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> ThreadingHTTPServer:
    """Create a bound ThreadingHTTPServer for the local QAlprit workbench UI."""
    root = project_root.resolve()
    res_dir = (results_dir or (root / "results")).resolve()
    handler = partial(_WorkbenchHandler, project_root=root, results_dir=res_dir)
    return ThreadingHTTPServer((host, port), handler)
