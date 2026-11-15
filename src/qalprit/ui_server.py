"""Lightweight local web workbench server for QAlprit (`qalprit ui`)."""

from __future__ import annotations

import base64
import json
import mimetypes
import platform
from dataclasses import asdict
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from qalprit import __version__
from qalprit.config_loader import load_screens
from qalprit.devices import list_android_devices, list_ios_simulators
from qalprit.doctor import discover_system_software, run_doctor
from qalprit.emulator_manager import DEFAULT_AVD_NAME, EmulatorJobManager
from qalprit.recorder import RecorderManager
from qalprit.results import load_results_json
from qalprit.spec_loader import STEP_VOCABULARY, load_flows, load_specs
from qalprit.workbench_api import (
    RunJobManager,
    delete_flow_spec,
    delete_test_journey,
    duplicate_flow_spec,
    duplicate_test_journey,
    inspect_live_screen,
    inspect_xml_payload,
    list_workspace_apps,
    list_workspace_configs,
    next_journey_id,
    reveal_in_file_manager,
    run_workspace_validation,
    save_environment_entry,
    save_flow_spec,
    save_screen_locators,
    save_test_journey,
    save_uploaded_app,
    update_storage_preferences,
    upload_flow_markdown,
    upload_spec_markdown,
)

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
    corrupt_runs: list[dict[str, str]] = []
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
                        "dir_name": entry.name,
                        "started_at": run.started_at,
                        "finished_at": run.finished_at,
                        "environment": run.environment,
                        "platform": run.platform,
                        "device_name": run.device_name,
                        "target_type": run.target_type,
                        "os_version": run.os_version,
                        "app_version": run.app_version,
                        "suite": run.suite,
                        "tags": run.tags,
                        "interrupted": run.interrupted,
                        "summary": run.summary.model_dump(),
                        "tests": [
                            {
                                "id": t.id,
                                "title": t.title,
                                "status": t.status,
                                "flaky": t.flaky,
                                "failure_category": t.failure_category,
                                "duration_ms": t.duration_ms,
                            }
                            for t in run.tests
                        ],
                        "has_html": (entry / "report.html").exists(),
                        "has_md": (entry / "summary.md").exists(),
                        "has_junit": (entry / "junit.xml").exists(),
                    }
                )
            except Exception as exc:
                corrupt_runs.append({"run_id": entry.name, "error": str(exc)})
                continue

    parse_warnings: list[str] = []
    specs, spec_warnings = load_specs(root / "specs")
    flows, flow_warnings = load_flows(root / "flows")
    parse_warnings.extend(spec_warnings)
    parse_warnings.extend(flow_warnings)

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
        except Exception as exc:
            parse_warnings.append(f"screens/screens.yaml: {exc}")

    apps = list_workspace_apps(root)
    environments, device_profiles = list_workspace_configs(root)

    return {
        "tool": "QAlprit",
        "version": __version__,
        "host_os": platform.system(),
        "tagline": "Mobile QA. Bugs caught with proof.",
        "project_root": str(root),
        "apps": apps,
        "environments": environments,
        "device_profiles": device_profiles,
        "step_vocabulary": sorted(STEP_VOCABULARY),
        "runs": runs,
        "corrupt_runs": corrupt_runs,
        "parse_warnings": parse_warnings,
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
                "retries": s.retries,
                "expected": s.expected,
                "body": s.body,
                "setup": [st.model_dump() for st in s.setup],
                "steps": [st.model_dump() for st in s.steps],
                "teardown": [st.model_dump() for st in s.teardown],
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
                "body": f.body,
                "step_count": len(f.steps),
                "steps": [st.model_dump() for st in f.steps],
                "source_path": f.source_path.relative_to(root).as_posix()
                if f.source_path.is_relative_to(root)
                else str(f.source_path),
            }
            for f in sorted(flows.values(), key=lambda x: x.name)
        ],
        "screens": screens_list,
    }


def _find_run_json(results_dir: Path, run_id: str) -> Path | None:
    if not run_id or "/" in run_id or "\\" in run_id or ".." in run_id:
        return None
    direct = (results_dir / run_id / "results.json").resolve()
    if direct.is_relative_to(results_dir.resolve()) and direct.is_file():
        return direct
    for entry in results_dir.iterdir():
        if not entry.is_dir():
            continue
        p = entry / "results.json"
        if p.is_file():
            try:
                r = load_results_json(p)
                if r.run_id == run_id:
                    return p
            except Exception:
                continue
    return None


def compare_runs(results_dir: Path, base_run_id: str, target_run_id: str) -> dict[str, Any]:
    """Compare two runs and report regressions, fixes, persistent failures, and delta."""
    base_path = _find_run_json(results_dir, base_run_id)
    target_path = _find_run_json(results_dir, target_run_id)
    if base_path is None:
        raise FileNotFoundError(f"Base run '{base_run_id}' not found")
    if target_path is None:
        raise FileNotFoundError(f"Target run '{target_run_id}' not found")

    base = load_results_json(base_path)
    target = load_results_json(target_path)
    base_map = {t.id: t for t in base.tests}
    target_map = {t.id: t for t in target.tests}

    regressions: list[dict[str, Any]] = []
    fixes: list[dict[str, Any]] = []
    persistent_failures: list[dict[str, Any]] = []
    new_tests: list[dict[str, Any]] = []
    removed_tests: list[dict[str, Any]] = []

    for tid, tt in target_map.items():
        bt = base_map.get(tid)
        if bt is None:
            new_tests.append({"id": tid, "title": tt.title, "status": tt.status})
            continue
        if bt.status != "failed" and tt.status == "failed":
            regressions.append(
                {
                    "id": tid,
                    "title": tt.title,
                    "base_status": bt.status,
                    "target_status": tt.status,
                    "failure_category": tt.failure_category,
                    "failure_reason": tt.failure_reason or "",
                }
            )
        elif bt.status == "failed" and tt.status == "passed":
            fixes.append({"id": tid, "title": tt.title, "base_status": bt.status, "target_status": tt.status})
        elif bt.status == "failed" and tt.status == "failed":
            persistent_failures.append(
                {
                    "id": tid,
                    "title": tt.title,
                    "failure_category": tt.failure_category,
                }
            )

    for tid, bt in base_map.items():
        if tid not in target_map:
            removed_tests.append({"id": tid, "title": bt.title, "status": bt.status})

    return {
        "base_run_id": base.run_id,
        "target_run_id": target.run_id,
        "duration_delta_ms": target.summary.duration_ms - base.summary.duration_ms,
        "regressions": regressions,
        "fixes": fixes,
        "persistent_failures": persistent_failures,
        "new_tests": new_tests,
        "removed_tests": removed_tests,
    }


_MAX_HTTP_UPLOAD_BYTES = 310 * 1024 * 1024


class _WorkbenchHandler(BaseHTTPRequestHandler):
    def __init__(
        self,
        *args: Any,
        project_root: Path,
        results_dir: Path,
        workspace_state: dict[str, Path] | None = None,
        job_manager: RunJobManager | None = None,
        emulator_manager: EmulatorJobManager | None = None,
        recorder_manager: RecorderManager | None = None,
        **kwargs: Any,
    ) -> None:
        self.workspace_state = workspace_state if workspace_state is not None else {
            "project_root": project_root.resolve(),
            "results_dir": results_dir.resolve(),
        }
        self.job_manager = job_manager or RunJobManager()
        self.emulator_manager = emulator_manager or EmulatorJobManager()
        self.recorder_manager = recorder_manager or RecorderManager()
        super().__init__(*args, **kwargs)

    @property
    def project_root(self) -> Path:
        return self.workspace_state["project_root"]

    @property
    def results_dir(self) -> Path:
        return self.workspace_state["results_dir"]

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def _send_bytes(self, body: bytes, content_type: str, status: int = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        self._send_bytes(
            json.dumps(payload, indent=2).encode("utf-8"),
            "application/json; charset=utf-8",
            status=status,
        )

    def _read_raw_body(self, max_bytes: int = 8 * 1024 * 1024) -> bytes:
        raw_len = self.headers.get("Content-Length") or "0"
        try:
            length = int(raw_len)
        except ValueError as exc:
            raise ValueError("Invalid Content-Length header.") from exc
        if length < 0:
            raise ValueError("Negative Content-Length header.")
        if length > max_bytes:
            raise ValueError(f"Request body exceeds {max_bytes // (1024 * 1024)} MB limit.")
        return self.rfile.read(length) if length > 0 else b""

    def _read_json_body(self, max_bytes: int = 8 * 1024 * 1024) -> dict[str, Any]:
        raw = self._read_raw_body(max_bytes=max_bytes)
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise ValueError(f"Invalid JSON request body: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("JSON payload must be an object.")
        return data

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
            self._send_json(payload)
            return

        if req_path == "/api/specs/next-id":
            qs = parse_qs(parsed.query)
            suite = (qs.get("suite") or ["smoke"])[0]
            self._send_json({"id": next_journey_id(self.project_root, suite)})
            return

        if req_path == "/api/doctor":
            checks = [asdict(item) for item in run_doctor()]
            self._send_json({"checks": checks})
            return

        if req_path == "/api/system/preflight":
            self._send_json(discover_system_software(self.project_root))
            return

        if req_path == "/api/devices":
            android_devs, android_err = list_android_devices()
            ios_devs, ios_err = list_ios_simulators()
            self._send_json(
                {
                    "android": [asdict(d) for d in android_devs],
                    "android_error": android_err,
                    "ios": [asdict(d) for d in ios_devs],
                    "ios_error": ios_err,
                }
            )
            return

        if req_path == "/api/runs/status":
            self._send_json(self.job_manager.status())
            return

        if req_path == "/api/emulator/status":
            self._send_json(self.emulator_manager.status())
            return

        if req_path == "/api/recorder/status":
            self._send_json(self.recorder_manager.status())
            return

        if req_path == "/api/recorder/screenshot":
            png = self.recorder_manager.screenshot_bytes()
            if not png:
                self._send_bytes(b"No screenshot captured yet", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                return
            self._send_bytes(png, "image/png")
            return

        if req_path == "/api/compare":
            qs = parse_qs(parsed.query)
            base_id = (qs.get("base") or [""])[0]
            target_id = (qs.get("target") or [""])[0]
            if not base_id or not target_id:
                self._send_bytes(
                    json.dumps({"error": "Both 'base' and 'target' query params are required"}).encode("utf-8"),
                    "application/json; charset=utf-8",
                    HTTPStatus.BAD_REQUEST,
                )
                return
            try:
                payload = compare_runs(self.results_dir, base_id, target_id)
                self._send_bytes(
                    json.dumps(payload, indent=2).encode("utf-8"),
                    "application/json; charset=utf-8",
                )
            except FileNotFoundError as exc:
                self._send_bytes(
                    json.dumps({"error": str(exc)}).encode("utf-8"),
                    "application/json; charset=utf-8",
                    HTTPStatus.NOT_FOUND,
                )
            return

        if req_path.startswith("/results/"):
            rel = req_path[len("/results/") :].lstrip("/")
            if not rel or ".." in Path(rel).parts:
                self._send_bytes(b"Not found", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                return
            raw_candidate = self.results_dir / rel
            if raw_candidate.is_symlink() and not raw_candidate.resolve().is_relative_to(self.results_dir):
                self._send_bytes(b"Not found", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                return
            candidate = raw_candidate.resolve()
            if not candidate.is_relative_to(self.results_dir) or not candidate.is_file():
                self._send_bytes(b"Not found", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                return
            mime, _ = mimetypes.guess_type(candidate.name)
            self._send_bytes(candidate.read_bytes(), mime or "application/octet-stream")
            return

        self._send_bytes(b"Not found", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        req_path = unquote(parsed.path)
        try:
            if req_path == "/api/apps/upload":
                ctype = (self.headers.get("Content-Type") or "").lower()
                if "application/json" in ctype:
                    body = self._read_json_body(max_bytes=_MAX_HTTP_UPLOAD_BYTES)
                    filename = str(body.get("filename") or "")
                    b64_raw = str(body.get("content_b64") or "")
                    if "," in b64_raw and b64_raw.startswith("data:"):
                        b64_raw = b64_raw.split(",", 1)[1]
                    try:
                        raw_bytes = base64.b64decode(b64_raw, validate=True)
                    except Exception as exc:
                        raise ValueError("Invalid base64 payload for uploaded app.") from exc
                else:
                    qs = parse_qs(parsed.query)
                    filename = (qs.get("filename") or [self.headers.get("X-Filename") or ""])[0]
                    raw_bytes = self._read_raw_body(max_bytes=_MAX_HTTP_UPLOAD_BYTES)
                self._send_json(save_uploaded_app(self.project_root, filename, raw_bytes))
                return

            body = self._read_json_body()
            if req_path == "/api/environments/save":
                self._send_json(
                    save_environment_entry(
                        self.project_root,
                        env_name=str(body.get("env_name") or "qa"),
                        android_package=str(body.get("android_package") or ""),
                        ios_bundle_id=str(body.get("ios_bundle_id") or ""),
                        default_timeout=float(body.get("default_timeout") or 12.0),
                    )
                )
                return

            if req_path == "/api/inspect/xml":
                self._send_json(
                    inspect_xml_payload(
                        self.project_root,
                        xml_text=str(body.get("xml_text") or ""),
                        platform=str(body.get("platform") or "android"),
                        screen_name=str(body.get("screen_name") or "screen"),
                    )
                )
                return

            if req_path == "/api/inspect/live":
                self._send_json(
                    inspect_live_screen(
                        self.project_root,
                        platform=str(body.get("platform") or "android"),
                        device_profile=str(body.get("device_profile") or "android_emulator"),
                        env_name=str(body.get("env_name") or "qa"),
                        app_path_str=str(body.get("app_path") or ""),
                        screen_name=str(body.get("screen_name") or "screen"),
                        wait_seconds=float(body.get("wait_seconds") or 2.0),
                    )
                )
                return

            if req_path == "/api/screens/save":
                self._send_json(
                    save_screen_locators(
                        self.project_root,
                        screen_name=str(body.get("screen_name") or "screen"),
                        platform=str(body.get("platform") or "android"),
                        entries=list(body.get("entries") or []),
                    )
                )
                return

            if req_path == "/api/flows/save":
                self._send_json(
                    save_flow_spec(
                        self.project_root,
                        name=str(body.get("name") or ""),
                        params=list(body.get("params") or []),
                        steps=list(body.get("steps") or []),
                        notes=str(body.get("notes") or ""),
                    )
                )
                return

            if req_path == "/api/flows/upload":
                self._send_json(
                    upload_flow_markdown(
                        self.project_root,
                        filename=str(body.get("filename") or "flow.md"),
                        content_text=str(body.get("content_text") or ""),
                    )
                )
                return

            if req_path == "/api/flows/duplicate":
                self._send_json(
                    duplicate_flow_spec(
                        self.project_root,
                        name=str(body.get("name") or ""),
                        new_name=str(body.get("new_name") or ""),
                    )
                )
                return

            if req_path == "/api/flows/delete":
                self._send_json(
                    delete_flow_spec(
                        self.project_root,
                        name=str(body.get("name") or ""),
                    )
                )
                return

            if req_path == "/api/specs/save":
                self._send_json(save_test_journey(self.project_root, body))
                return

            if req_path == "/api/specs/upload":
                self._send_json(
                    upload_spec_markdown(
                        self.project_root,
                        filename=str(body.get("filename") or "journey.md"),
                        content_text=str(body.get("content_text") or ""),
                        suite=str(body.get("suite") or ""),
                    )
                )
                return

            if req_path == "/api/specs/duplicate":
                self._send_json(
                    duplicate_test_journey(
                        self.project_root,
                        source_path=str(body.get("source_path") or ""),
                        new_id=str(body.get("new_id") or ""),
                        suite=str(body.get("suite") or ""),
                    )
                )
                return

            if req_path == "/api/specs/delete":
                self._send_json(
                    delete_test_journey(
                        self.project_root,
                        source_path=str(body.get("source_path") or ""),
                    )
                )
                return

            if req_path == "/api/workspace/reveal":
                self._send_json(
                    reveal_in_file_manager(
                        self.project_root,
                        rel_path=str(body.get("rel_path") or ""),
                    )
                )
                return

            if req_path == "/api/system/preferences":
                res = update_storage_preferences(self.project_root, body)
                new_ws = Path(res["workspace_dir"]).resolve()
                self.workspace_state["project_root"] = new_ws
                self.workspace_state["results_dir"] = (new_ws / "results").resolve()
                self._send_json(res)
                return

            if req_path == "/api/validate":
                self._send_json(
                    run_workspace_validation(
                        self.project_root,
                        suite=str(body.get("suite") or "") or None,
                        env_name=str(body.get("env_name") or "") or None,
                        platform=str(body.get("platform") or "") or None,
                        app_path_str=str(body.get("app_path") or ""),
                    )
                )
                return

            if req_path == "/api/runs/start":
                if self.recorder_manager.status().get("active"):
                    self.recorder_manager.stop()
                self._send_json(self.job_manager.start(self.project_root, self.results_dir, body))
                return

            if req_path == "/api/runs/stop":
                self._send_json(self.job_manager.stop())
                return

            if req_path == "/api/emulator/setup":
                self._send_json(
                    self.emulator_manager.start_setup(
                        avd_name=str(body.get("avd_name") or DEFAULT_AVD_NAME),
                    )
                )
                return

            if req_path == "/api/emulator/boot":
                target = str(body.get("target") or "android").lower()
                if target == "ios":
                    self._send_json(
                        self.emulator_manager.boot_ios_simulator(
                            udid=str(body.get("udid") or ""),
                        )
                    )
                else:
                    self._send_json(
                        self.emulator_manager.start_boot_android(
                            avd_name=str(body.get("avd_name") or DEFAULT_AVD_NAME),
                        )
                    )
                return

            if req_path == "/api/emulator/stop":
                self._send_json(self.emulator_manager.stop())
                return

            if req_path == "/api/recorder/start":
                self._send_json(
                    self.recorder_manager.start(
                        self.project_root,
                        platform=str(body.get("platform") or "android"),
                        device_profile=str(body.get("device_profile") or "android_emulator"),
                        env_name=str(body.get("env_name") or "qa"),
                        app_path_str=str(body.get("app_path") or ""),
                        screen_name=str(body.get("screen_name") or "login"),
                        flow_name=str(body.get("flow_name") or "recorded_flow"),
                    )
                )
                return

            if req_path == "/api/recorder/action":
                raw_idx = body.get("element_index")
                raw_x = body.get("x")
                raw_y = body.get("y")
                self._send_json(
                    self.recorder_manager.perform_action(
                        action=str(body.get("action") or "tap"),
                        element_index=int(raw_idx) if raw_idx is not None else None,
                        x=float(raw_x) if raw_x is not None else None,
                        y=float(raw_y) if raw_y is not None else None,
                        value=str(body.get("value") or ""),
                        secret=bool(body.get("secret", False)),
                        direction=str(body.get("direction") or "up"),
                        screen_name=str(body.get("screen_name") or ""),
                        key_override=str(body.get("key_override") or ""),
                    )
                )
                return

            if req_path == "/api/recorder/sync":
                self._send_json(
                    self.recorder_manager.sync_screen(
                        screen_name=str(body.get("screen_name") or ""),
                    )
                )
                return

            if req_path == "/api/recorder/undo":
                self._send_json(self.recorder_manager.undo())
                return

            if req_path == "/api/recorder/update":
                self._send_json(
                    self.recorder_manager.update_step(
                        int(body.get("index") if body.get("index") is not None else -1),
                        value=body.get("value"),
                        secret=body.get("secret"),
                        delete=bool(body.get("delete", False)),
                    )
                )
                return

            if req_path == "/api/recorder/save":
                self._send_json(
                    self.recorder_manager.save_recorded(
                        flow_name=str(body.get("flow_name") or "recorded_flow"),
                        suite=str(body.get("suite") or "smoke"),
                        test_id=str(body.get("test_id") or ""),
                        test_title=str(body.get("test_title") or ""),
                        create_test=bool(body.get("create_test", True)),
                        stop_after=bool(body.get("stop_after", True)),
                    )
                )
                return

            if req_path == "/api/recorder/stop":
                self._send_json(self.recorder_manager.stop())
                return

            self._send_json({"error": f"Unknown API endpoint: {req_path}"}, status=HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except RuntimeError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.CONFLICT)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)


def create_ui_server(
    project_root: Path,
    results_dir: Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> ThreadingHTTPServer:
    """Create a bound ThreadingHTTPServer for the local QAlprit workbench UI."""
    root = project_root.resolve()
    res_dir = (results_dir or (root / "results")).resolve()
    workspace_state = {"project_root": root, "results_dir": res_dir}
    job_manager = RunJobManager()
    emulator_manager = EmulatorJobManager()
    recorder_manager = RecorderManager()
    handler = partial(
        _WorkbenchHandler,
        project_root=root,
        results_dir=res_dir,
        workspace_state=workspace_state,
        job_manager=job_manager,
        emulator_manager=emulator_manager,
        recorder_manager=recorder_manager,
    )
    try:
        return ThreadingHTTPServer((host, port), handler)
    except OSError:
        if port == 0:
            raise
        return ThreadingHTTPServer((host, 0), handler)
