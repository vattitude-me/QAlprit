"""Lightweight local web workbench server for QAlprit (`qalprit ui`)."""

from __future__ import annotations

import base64
import json
import mimetypes
import platform
import re
from dataclasses import asdict
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from qalprit import __version__, background_devices
from qalprit.handoff import BROKER
from qalprit.cleanup import execute_selective_cleanup, inspect_cleanup_targets
from qalprit.config_loader import load_screens
from qalprit.datasets import (
    DatasetBundle,
    export_csv_datasets,
    extract_variables_from_steps,
    import_csv_to_datasets,
    load_datasets,
    mask_bundle_for_ui,
    save_datasets,
)
from qalprit.devices import list_android_devices, list_ios_simulators
from qalprit.doctor import discover_system_software, run_doctor
from qalprit.emulator_manager import DEFAULT_AVD_NAME, EmulatorJobManager
from qalprit.error_text import explain_failure, friendly_error
from qalprit.ai_review import review_run_dir, set_step_review
from qalprit.atlassian_refs import check_connection, fetch_reference_docs, read_connection, save_connection
from qalprit.env_loader import load_env
from qalprit.recorder import RecorderManager
from qalprit.results import load_results_json
from qalprit.spec_loader import STEP_VOCABULARY, effective_references, load_flows, load_specs
from qalprit.workbench_api import (
    RunJobManager,
    delete_environment_entry,
    delete_flow_spec,
    delete_run_results,
    delete_screen_locator,
    delete_test_journey,
    delete_workspace_app,
    rename_flow_spec,
    duplicate_flow_spec,
    duplicate_test_journey,
    inspect_live_screen,
    inspect_xml_payload,
    list_workspace_apps,
    list_workspace_configs,
    pick_folder,
    next_journey_id,
    rename_flow_spec,
    reveal_in_file_manager,
    run_workspace_validation,
    save_environment_entry,
    save_flow_spec,
    save_screen_locators,
    save_test_journey,
    save_uploaded_app,
    save_uploaded_app_stream,
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
    batches: list[dict[str, Any]] = []
    corrupt_runs: list[dict[str, str]] = []
    if res_dir.exists() and res_dir.is_dir():
        for entry in sorted(res_dir.iterdir(), key=lambda p: p.name, reverse=True):
            if not entry.is_dir():
                continue
            batch_json = entry / "batch.json"
            if batch_json.is_file():
                try:
                    b_data = json.loads(batch_json.read_text(encoding="utf-8"))
                    if isinstance(b_data, dict):
                        b_data["dir_name"] = entry.name
                        b_data["has_html"] = (entry / "batch_report.html").exists()
                        batches.append(b_data)
                except Exception:
                    pass
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
                        "batch_id": run.batch_id,
                        "dataset_name": run.dataset_name,
                        "dataset_expected": run.dataset_expected,
                        "interrupted": run.interrupted,
                        "summary": run.summary.model_dump(),
                        "tests": [
                            {
                                "id": t.id,
                                "title": t.title,
                                "status": t.status,
                                "flaky": t.flaky,
                                "failure_category": t.failure_category,
                                "failure_reason": t.failure_reason or "",
                                "failure_summary": explain_failure(t.failure_reason, t.failure_category),
                                "failed_step_index": t.failed_step_index,
                                "failed_step_action": t.failed_step_action,
                                "duration_ms": t.duration_ms,
                                "steps": [
                                    {
                                        "index": st.index,
                                        "phase": st.phase,
                                        "action": st.action,
                                        "target": st.target or "",
                                        "description": st.description,
                                        "status": st.status,
                                        "error": st.error or "",
                                        "screenshot_path": st.screenshot_path or "",
                                        "reference_screenshot_path": st.reference_screenshot_path,
                                        "reference_mismatch": st.reference_mismatch,
                                        "reference_note": st.reference_note,
                                        "ai_verdict": st.ai_verdict,
                                        "ai_reason": st.ai_reason,
                                        "ai_requirement": st.ai_requirement,
                                        "review": st.review,
                                        "review_note": st.review_note,
                                    }
                                    for st in t.steps
                                ],
                                "references": list(t.references),
                                "ai_summary": t.ai_summary,
                                "ai_uncovered": list(t.ai_uncovered),
                                "ai_error": t.ai_error,
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

    def _datasets_or_warn(kind: str, name: str) -> DatasetBundle:
        # One unreadable CSV must not blank the whole Workbench. Show the test without data rows
        # and say which file needs fixing.
        try:
            return load_datasets(root, kind, name)  # type: ignore[arg-type]
        except Exception as exc:
            folder = "flows" if kind == "flow" else "specs"
            parse_warnings.append(f"datasets/{folder}/{name}.csv could not be read: {exc}")
            return DatasetBundle(target_kind=kind, target_name=name, variables=[], secret_columns=[], rows=[])  # type: ignore[arg-type]

    specs_payload: list[dict[str, Any]] = []
    for s in specs:
        ds_bundle = _datasets_or_warn("spec", s.id)
        detected_vars = extract_variables_from_steps(s.setup + s.steps + s.teardown, flows=flows)
        merged_params = list(dict.fromkeys([*s.params, *ds_bundle.variables, *detected_vars]))
        specs_payload.append(
            {
                "id": s.id,
                "title": s.title,
                "suite": s.suite,
                "tags": s.tags,
                "platforms": s.platforms,
                "targets": s.targets,
                "priority": s.priority,
                "params": merged_params,
                "reset": s.reset,
                "retries": s.retries,
                "expected": s.expected,
                "references": list(s.references),
                "inherited_references": [r for r in effective_references(s, flows) if r not in s.references],
                "body": s.body,
                "setup": [st.model_dump() for st in s.setup],
                "steps": [st.model_dump() for st in s.steps],
                "teardown": [st.model_dump() for st in s.teardown],
                "step_count": len(s.steps),
                "teardown_count": len(s.teardown),
                "datasets": mask_bundle_for_ui(ds_bundle),
                "source_path": s.source_path.relative_to(root).as_posix()
                if s.source_path.is_relative_to(root)
                else str(s.source_path),
            }
        )

    flows_payload: list[dict[str, Any]] = []
    for f in sorted(flows.values(), key=lambda x: x.name):
        ds_bundle = _datasets_or_warn("flow", f.name)
        detected_vars = extract_variables_from_steps(f.steps)
        merged_params = list(dict.fromkeys([*f.params, *ds_bundle.variables, *detected_vars]))
        flows_payload.append(
            {
                "name": f.name,
                "params": merged_params,
                "declared_params": f.params,
                "platforms": list(f.platforms),
                "apps": dict(f.apps),
                "references": list(f.references),
                "body": f.body,
                "step_count": len(f.steps),
                "steps": [st.model_dump() for st in f.steps],
                "datasets": mask_bundle_for_ui(ds_bundle),
                "source_path": f.source_path.relative_to(root).as_posix()
                if f.source_path.is_relative_to(root)
                else str(f.source_path),
            }
        )

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
        "batches": batches,
        "corrupt_runs": corrupt_runs,
        "parse_warnings": parse_warnings,
        "specs": specs_payload,
        "flows": flows_payload,
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


def _find_run_json_deep(results_dir: Path, run_id: str) -> Path | None:
    """Like _find_run_json, but also finds the per-row runs inside a dataset batch folder."""
    found = _find_run_json(results_dir, run_id)
    if found is not None or not run_id or "/" in run_id or "\\" in run_id or ".." in run_id:
        return found
    for p in results_dir.glob("*/*/results.json"):
        try:
            if p.parent.name == run_id or load_results_json(p).run_id == run_id:
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


_MAX_HTTP_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024 + 16 * 1024 * 1024


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
        # Without this an exception closes the socket with no reply, and the UI can only say
        # "QAlprit is not responding". Send the real reason instead, as do_POST does.
        try:
            self._handle_get()
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._send_json(
                {"error": friendly_error(exc), "error_type": type(exc).__name__},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def _handle_get(self) -> None:
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

        if req_path == "/api/atlassian/settings":
            self._send_json(read_connection(dict(load_env(self.project_root, include_os_env=False).variables)))
            return

        if req_path == "/api/system/cleanup":
            self._send_json(inspect_cleanup_targets(self.project_root))
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
            pending = BROKER.pending()
            self._send_json({**self.job_manager.status(), "handoff_pending": pending, "otp_pending": pending})
            return

        if req_path == "/api/runs/log":
            name, text = self.job_manager.log_text()
            body = text.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        if req_path == "/api/emulator/status":
            self._send_json(self.emulator_manager.status())
            return

        if req_path == "/api/recorder/status":
            self._send_json(self.recorder_manager.status())
            return

        if req_path == "/api/recorder/step-shot":
            uid = (parse_qs(parsed.query).get("uid") or [""])[0]
            png = self.recorder_manager.step_shot_bytes(uid)
            if not png:
                self._send_bytes(b"No screenshot for that step", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                return
            self._send_bytes(png, "image/png")
            return

        if req_path == "/api/recorder/screenshot":
            png = self.recorder_manager.screenshot_bytes()
            if not png:
                self._send_bytes(b"No screenshot captured yet", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                return
            self._send_bytes(png, "image/png")
            return

        if req_path == "/api/datasets":
            qs = parse_qs(parsed.query)
            kind = (qs.get("kind") or ["flow"])[0]
            name = (qs.get("name") or [""])[0]
            if kind not in {"flow", "spec"} or not name:
                self._send_json({"error": "Valid 'kind' (flow|spec) and 'name' are required."}, HTTPStatus.BAD_REQUEST)
                return
            bundle = load_datasets(self.project_root, kind, name)  # type: ignore[arg-type]
            self._send_json(mask_bundle_for_ui(bundle))
            return

        if req_path == "/api/datasets/export":
            qs = parse_qs(parsed.query)
            kind = (qs.get("kind") or ["flow"])[0]
            name = (qs.get("name") or [""])[0]
            if kind not in {"flow", "spec"} or not name:
                self._send_json({"error": "Valid 'kind' (flow|spec) and 'name' are required."}, HTTPStatus.BAD_REQUEST)
                return
            bundle = load_datasets(self.project_root, kind, name)  # type: ignore[arg-type]
            csv_text = export_csv_datasets(bundle)
            self._send_bytes(csv_text.encode("utf-8"), "text/csv; charset=utf-8")
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
                    length = int(self.headers.get("Content-Length") or "0")
                    if length > _MAX_HTTP_UPLOAD_BYTES:
                        raise ValueError("Request body exceeds the 2 GB limit.")
                    self._send_json(
                        save_uploaded_app_stream(self.project_root, filename, self.rfile, length)
                    )
                    return
                self._send_json(save_uploaded_app(self.project_root, filename, raw_bytes))
                return

            body = self._read_json_body()
            if req_path == "/api/environments/save":
                raw_redact = body.get("redact_screenshots")
                self._send_json(
                    save_environment_entry(
                        self.project_root,
                        env_name=str(body.get("env_name") or "qa"),
                        android_package=str(body.get("android_package") or ""),
                        ios_bundle_id=str(body.get("ios_bundle_id") or ""),
                        default_timeout=float(body.get("default_timeout") or 12.0),
                        redact_screenshots=None if raw_redact is None else bool(raw_redact),
                        sync_placeholders=bool(body.get("sync_placeholders", False)),
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
                plat = str(body.get("platform") or "android").lower()
                default_dev = "ios_simulator" if plat == "ios" else "android_emulator"
                self._send_json(
                    inspect_live_screen(
                        self.project_root,
                        platform=plat,
                        device_profile=str(body.get("device_profile") or default_dev),
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
                        references=(
                            [str(r).strip() for r in body["references"] if str(r).strip()]
                            if isinstance(body.get("references"), list) else None
                        ),
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

            if req_path == "/api/flows/rename":
                self._send_json(
                    rename_flow_spec(
                        self.project_root,
                        name=str(body.get("name") or ""),
                        new_name=str(body.get("new_name") or ""),
                    )
                )
                return

            if req_path == "/api/references/fetch":
                refs = body.get("refs") or []
                if isinstance(refs, str):
                    refs = [r for r in re.split(r"[\s,]+", refs) if r]
                env_vars = dict(load_env(self.project_root, env_name=str(body.get("env_name") or "") or None).variables)
                docs = fetch_reference_docs(
                    self.project_root, [str(r) for r in refs][:20], env_vars, refresh=bool(body.get("refresh"))
                )
                self._send_json({"docs": [d.as_dict() for d in docs]})
                return

            if req_path in ("/api/runs/ai-review", "/api/runs/review"):
                run_json = _find_run_json_deep(self.results_dir, str(body.get("run_id") or ""))
                if run_json is None:
                    raise FileNotFoundError("That run was not found.")
                if req_path == "/api/runs/ai-review":
                    if self.job_manager.status().get("running"):
                        raise ValueError("A run is in progress. Check it with AI when it has finished.")
                    ids = [str(t) for t in (body.get("test_ids") or []) if str(t).strip()]
                    out = review_run_dir(
                        self.project_root, run_json.parent, test_ids=ids or None, refresh_refs=bool(body.get("refresh_refs")),
                        provider=str(body.get("ai_provider") or "").strip() or None,
                    )
                else:
                    out = set_step_review(
                        self.project_root,
                        run_json.parent,
                        test_id=str(body.get("test_id") or ""),
                        step_index=int(body.get("step_index")),
                        decision=str(body.get("decision") or ""),
                        note=str(body.get("note") or ""),
                    )
                self._send_json(out)
                return

            if req_path == "/api/runs/delete":
                if self.job_manager.status().get("running"):
                    raise ValueError("A run is in progress. Stop it or wait for it to finish, then delete.")
                self._send_json(delete_run_results(self.results_dir, str(body.get("dir_name") or "")))
                return

            if req_path == "/api/apps/delete":
                self._send_json(delete_workspace_app(self.project_root, str(body.get("rel_path") or "")))
                return

            if req_path == "/api/screens/delete":
                self._send_json(delete_screen_locator(self.project_root, str(body.get("target") or "")))
                return

            if req_path == "/api/environments/delete":
                self._send_json(delete_environment_entry(self.project_root, str(body.get("env_name") or "")))
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

            if req_path == "/api/atlassian/settings":
                self._send_json(save_connection(self.project_root, body))
                return

            if req_path == "/api/atlassian/test":
                self._send_json(check_connection(self.project_root, body))
                return

            if req_path == "/api/system/pick-folder":
                if str(self.client_address[0]) not in {"127.0.0.1", "::1", "::ffff:127.0.0.1"}:
                    raise ValueError("The folder chooser only works from the computer running QAlprit.")
                self._send_json(
                    pick_folder(
                        str(body.get("initial") or ""),
                        prompt=str(body.get("prompt") or "Choose a folder"),
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

            if req_path == "/api/system/cleanup":
                target_ids = list(body.get("target_ids") or [])
                res = execute_selective_cleanup(target_ids, active_workspace=self.project_root)
                if "active_workspace" in target_ids:
                    from qalprit.desktop import ensure_workspace

                    recreated = ensure_workspace(self.project_root)
                    self.workspace_state["project_root"] = recreated
                    self.workspace_state["results_dir"] = (recreated / "results").resolve()
                self._send_json(res)
                return

            if req_path == "/api/datasets/save":
                kind = str(body.get("kind") or "flow")
                name = str(body.get("name") or "").strip()
                if kind not in {"flow", "spec"} or not name:
                    raise ValueError("Valid 'kind' (flow|spec) and 'name' are required.")
                bundle = save_datasets(self.project_root, kind, name, body)  # type: ignore[arg-type]
                self._send_json({"ok": True, "bundle": mask_bundle_for_ui(bundle)})
                return

            if req_path == "/api/datasets/import-csv":
                kind = str(body.get("kind") or "flow")
                name = str(body.get("name") or "").strip()
                csv_text = str(body.get("csv_text") or "")
                if kind not in {"flow", "spec"} or not name:
                    raise ValueError("Valid 'kind' (flow|spec) and 'name' are required.")
                bundle = import_csv_to_datasets(self.project_root, kind, name, csv_text)  # type: ignore[arg-type]
                self._send_json({"ok": True, "bundle": mask_bundle_for_ui(bundle)})
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

            if req_path == "/api/runs/otp":
                if not BROKER.submit(str(body.get("code") or "")):
                    raise ValueError("No run is waiting for a code right now, or the code was empty.")
                self._send_json({"ok": True})
                return

            if req_path == "/api/runs/handoff":
                raw_id = body.get("id")
                if not BROKER.respond(
                    str(body.get("answer") or ""),
                    value=str(body.get("value") or ""),
                    note=str(body.get("note") or ""),
                    request_id=int(raw_id) if raw_id is not None else None,
                ):
                    raise ValueError(
                        "No run is waiting for a person right now, or the answer was incomplete. "
                        "A typed value cannot be empty."
                    )
                self._send_json({"ok": True})
                return

            if req_path == "/api/runs/stop":
                self._send_json(self.job_manager.stop())
                return

            if req_path == "/api/runs/clear":
                self._send_json(self.job_manager.clear())
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

            if req_path == "/api/emulator/show_window":
                self._send_json(
                    background_devices.show_window(
                        str(body.get("target") or "ios").lower(),
                        str(body.get("identifier") or ""),
                    )
                )
                return

            if req_path == "/api/emulator/shutdown":
                self._send_json(
                    self.emulator_manager.shutdown(
                        target=str(body.get("target") or "all"),
                        identifier=str(body.get("identifier") or ""),
                    )
                )
                return

            if req_path == "/api/recorder/start":
                raw_redact = body.get("redact_sensitive")
                plat = str(body.get("platform") or "android").lower()
                default_dev = "ios_simulator" if plat == "ios" else "android_emulator"
                self._send_json(
                    self.recorder_manager.start(
                        self.project_root,
                        platform=plat,
                        device_profile=str(body.get("device_profile") or default_dev),
                        env_name=str(body.get("env_name") or "qa"),
                        app_path_str=str(body.get("app_path") or ""),
                        screen_name=str(body.get("screen_name") or "login"),
                        flow_name=str(body.get("flow_name") or "recorded_flow"),
                        redact_sensitive=None if raw_redact is None else bool(raw_redact),
                        fresh_start=bool(body.get("fresh_start", True)),
                        start_mode=str(body.get("start_mode") or ""),
                        app_source=str(body.get("app_source") or ""),
                    )
                )
                return

            if req_path == "/api/recorder/redaction":
                self._send_json(
                    self.recorder_manager.set_redaction(
                        enabled=bool(body.get("enabled", True)),
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
                        locator_strategy=str(body.get("locator_strategy") or ""),
                        locator_value=str(body.get("locator_value") or ""),
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

            if req_path == "/api/recorder/clear":
                self._send_json(self.recorder_manager.clear(flow_name=str(body.get("flow_name") or "")))
                return

            if req_path == "/api/recorder/update":
                self._send_json(
                    self.recorder_manager.update_step(
                        int(body.get("index") if body.get("index") is not None else -1),
                        value=body.get("value"),
                        secret=body.get("secret"),
                        delete=bool(body.get("delete", False)),
                        duplicate=bool(body.get("duplicate", False)),
                        move_to=(int(body["move_to"]) if body.get("move_to") is not None else None),
                        optional=(bool(body["optional"]) if body.get("optional") is not None else None),
                        action=(str(body["action"]) if body.get("action") else None),
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
                        parameterize=bool(body.get("parameterize", False)),
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
            self._send_json({"error": friendly_error(exc)}, status=HTTPStatus.CONFLICT)
        except Exception as exc:
            # Driver and OS errors carry stack traces and doc links. Send the short version to
            # the UI and keep the type name so a bug report still says what broke.
            self._send_json(
                {"error": friendly_error(exc), "error_type": type(exc).__name__},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )


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
    BROKER.enable_web()
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
        server = ThreadingHTTPServer((host, port), handler)
    except OSError:
        if port == 0:
            raise
        server = ThreadingHTTPServer((host, 0), handler)
    server.qalprit_managers = {  # type: ignore[attr-defined]
        "runs": job_manager,
        "emulator": emulator_manager,
        "recorder": recorder_manager,
    }
    return server


def workbench_busy_work(server: ThreadingHTTPServer) -> list[str]:
    """Plain lines describing work that quitting would cut short. Empty when it is safe to quit."""
    managers = getattr(server, "qalprit_managers", {}) or {}
    busy: list[str] = []
    try:
        runs = managers.get("runs")
        st = runs.status() if runs is not None else {}
        if st.get("running"):
            busy.append("A test run is still going. Quitting stops it partway, so its report will be incomplete.")
    except Exception:
        pass
    try:
        rec = managers.get("recorder")
        steps = len(getattr(rec, "_steps", []) or []) if rec is not None else 0
        if steps:
            busy.append(f"The recorder has {steps} step{'s' if steps != 1 else ''} you have not saved.")
    except Exception:
        pass
    try:
        emu = managers.get("emulator")
        # Read the state directly: status() also queries every device, which is slow during quit.
        if emu is not None and (getattr(emu, "_state", {}) or {}).get("running"):
            busy.append("A device is still being set up or started.")
    except Exception:
        pass
    return busy


def shutdown_workbench_services(server: ThreadingHTTPServer) -> dict[str, Any]:
    """Release what the Workbench started before the process exits.

    Stops the recorder session and any test run (each stops the Appium server it
    launched), cancels a device setup or boot still in progress, and shuts down the
    devices QAlprit started in the background. Devices with a visible window, or
    started outside QAlprit, keep running.
    """
    managers = getattr(server, "qalprit_managers", {}) or {}
    summary: dict[str, Any] = {"errors": []}
    for key, action in (
        ("recorder", lambda m: m.stop()),
        ("runs", lambda m: m.stop()),
        ("emulator", lambda m: m.cancel_for_quit()),
    ):
        mgr = managers.get(key)
        if mgr is None:
            continue
        try:
            action(mgr)
        except Exception as exc:
            summary["errors"].append(f"{key}: {friendly_error(exc)}")
    try:
        summary["stopped_devices"] = background_devices.shutdown_background_devices()
    except Exception as exc:
        summary["errors"].append(f"devices: {friendly_error(exc)}")
    return summary
