"""Backend workspace actions for the interactive QAlprit Workbench UI."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from qalprit.appium_manager import AppiumManager
from qalprit.config_loader import (
    ScreensCatalog,
    load_devices,
    load_environments,
)
from qalprit.doctor import find_android_sdk
from qalprit.driver_factory import create_appium_driver
from qalprit.env_loader import load_env
from qalprit.inspector import extract_interactive_elements, format_inspect_output
from qalprit.report.generator import generate_reports
from qalprit.results import write_results_json
from qalprit.runner import TestRunner
from qalprit.spec_loader import TestSpec, load_flows, load_specs
from qalprit.validator import validate_project

_ALLOWED_APP_EXTS = {".apk", ".ipa", ".zip"}
_MAX_UPLOAD_BYTES = 300 * 1024 * 1024
_SLUG_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def _safe_slug(raw: str, default: str = "item") -> str:
    cleaned = _SLUG_RE.sub("_", (raw or "").strip()).strip("._-")
    return cleaned or default


def _detect_apk_package(apk_path: Path) -> str:
    """Best-effort Android package detection via local aapt2/aapt if installed."""
    candidates: list[Path] = []
    for name in ("aapt2", "aapt"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    sdk = find_android_sdk()
    if sdk is not None and (sdk / "build-tools").is_dir():
        for bt_dir in sorted((sdk / "build-tools").iterdir(), reverse=True):
            for name in ("aapt2", "aapt", "aapt2.exe", "aapt.exe"):
                cand = bt_dir / name
                if cand.is_file():
                    candidates.append(cand)

    for tool in candidates:
        try:
            proc = subprocess.run(
                [str(tool), "dump", "packagename", str(apk_path)],
                capture_output=True,
                text=True,
                timeout=8,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip().splitlines()[0].strip()
        except Exception:
            continue
    return ""


def list_workspace_apps(project_root: Path) -> list[dict[str, Any]]:
    """List mobile binaries placed in workspace `apps/`."""
    apps_dir = project_root / "apps"
    if not apps_dir.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for entry in sorted(apps_dir.iterdir(), key=lambda p: p.name.lower()):
        if entry.name.startswith("."):
            continue
        ext = entry.suffix.lower()
        if entry.is_file() and ext in {".apk", ".ipa", ".zip"}:
            items.append(
                {
                    "name": entry.name,
                    "rel_path": f"apps/{entry.name}",
                    "kind": ext.lstrip("."),
                    "size_bytes": entry.stat().st_size,
                }
            )
        elif entry.is_dir() and ext == ".app":
            items.append(
                {
                    "name": entry.name,
                    "rel_path": f"apps/{entry.name}",
                    "kind": "app",
                    "size_bytes": 0,
                }
            )
    return items


def list_workspace_configs(project_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return summarized environments and devices dicts for workbench UI dropdowns."""
    envs_out: dict[str, Any] = {}
    env_path = project_root / "config" / "environments.yaml"
    if env_path.is_file():
        try:
            for name, cfg in load_environments(env_path).items():
                envs_out[name] = {
                    "android_package": cfg.android_package,
                    "ios_bundle_id": cfg.ios_bundle_id,
                    "default_timeout": cfg.default_timeout,
                }
        except Exception:
            pass

    devs_out: dict[str, Any] = {}
    dev_path = project_root / "config" / "devices.yaml"
    if dev_path.is_file():
        try:
            for name, prof in load_devices(dev_path).items():
                devs_out[name] = {
                    "platform": prof.platform,
                    "target_type": prof.target_type,
                    "device_name": prof.device_name,
                }
        except Exception:
            pass
    return envs_out, devs_out


def save_uploaded_app(project_root: Path, filename: str, raw_bytes: bytes) -> dict[str, Any]:
    """Validate and save an uploaded APK/IPA/ZIP binary inside `apps/`."""
    if len(raw_bytes) == 0:
        raise ValueError("Uploaded file is empty.")
    if len(raw_bytes) > _MAX_UPLOAD_BYTES:
        raise ValueError(f"Uploaded file exceeds {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.")

    base_name = Path((filename or "").replace("\\", "/")).name
    if not base_name or base_name.startswith("."):
        raise ValueError("Invalid app filename.")
    ext = Path(base_name).suffix.lower()
    if ext not in _ALLOWED_APP_EXTS:
        raise ValueError(f"Unsupported app file extension '{ext}'. Upload an .apk, .ipa, or .zip.")

    safe_stem = _safe_slug(Path(base_name).stem, default="app")
    safe_name = f"{safe_stem}{ext}"
    apps_dir = (project_root / "apps").resolve()
    apps_dir.mkdir(parents=True, exist_ok=True)
    dest = (apps_dir / safe_name).resolve()
    if not dest.is_relative_to(apps_dir):
        raise ValueError("Resolved upload path escapes apps/ directory.")

    dest.write_bytes(raw_bytes)
    detected_pkg = _detect_apk_package(dest) if ext == ".apk" else ""
    return {
        "ok": True,
        "name": safe_name,
        "rel_path": f"apps/{safe_name}",
        "size_bytes": len(raw_bytes),
        "detected_package": detected_pkg,
    }


def save_environment_entry(
    project_root: Path,
    *,
    env_name: str,
    android_package: str = "",
    ios_bundle_id: str = "",
    default_timeout: float = 12.0,
) -> dict[str, Any]:
    """Update or add an environment entry in config/environments.yaml safely."""
    clean_name = _safe_slug(env_name, default="qa")
    env_path = project_root / "config" / "environments.yaml"
    env_path.parent.mkdir(parents=True, exist_ok=True)

    raw: dict[str, Any] = {}
    if env_path.is_file():
        loaded = yaml.safe_load(env_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            raw = loaded
    envs_block = raw.get("environments")
    if not isinstance(envs_block, dict):
        envs_block = dict(raw) if isinstance(raw, dict) else {}
        raw = {"environments": envs_block}

    prev = dict(envs_block.get(clean_name) or {})
    if android_package.strip():
        prev["android_package"] = android_package.strip()
    if ios_bundle_id.strip():
        prev["ios_bundle_id"] = ios_bundle_id.strip()
    prev["default_timeout"] = max(1.0, float(default_timeout))
    prev.setdefault("poll_interval", 0.25)
    prev.setdefault("secure_evidence", True)
    prev.setdefault("save_page_source_on_failure", True)
    envs_block[clean_name] = prev

    tmp_text = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)
    env_path.write_text(tmp_text, encoding="utf-8")
    load_environments(env_path)
    return {"ok": True, "env_name": clean_name, "config": prev}


def inspect_xml_payload(
    project_root: Path,
    *,
    xml_text: str,
    platform: str = "android",
    screen_name: str = "screen",
) -> dict[str, Any]:
    """Inspect XML hierarchy string cleanly and return ranked elements + YAML."""
    if not xml_text or not xml_text.strip():
        raise ValueError("XML hierarchy input is empty.")
    env_ctx = load_env(project_root)
    masker = env_ctx.masker
    ranked = extract_interactive_elements(xml_text, platform=platform, masker=masker)
    formatted = format_inspect_output(xml_text, screen_name=screen_name, platform=platform, masker=masker)
    return {
        "ok": True,
        "screen": _safe_slug(screen_name, "screen"),
        "platform": platform,
        "elements": ranked,
        "formatted": formatted,
    }


def inspect_live_screen(
    project_root: Path,
    *,
    platform: str,
    device_profile: str,
    env_name: str = "qa",
    app_path_str: str = "",
    screen_name: str = "screen",
    wait_seconds: float = 2.0,
) -> dict[str, Any]:
    """Attach to a connected device/emulator cleanly, dump XML, and rank elements."""
    val = validate_project(project_root, env_name=env_name)
    if device_profile not in val.devices:
        raise ValueError(f"Unknown device profile '{device_profile}'.")
    if env_name not in val.environments:
        raise ValueError(f"Unknown environment '{env_name}'.")
    profile = val.devices[device_profile]
    if profile.platform != platform:
        raise ValueError(f"Device '{device_profile}' is for '{profile.platform}', not '{platform}'.")
    env_cfg = val.environments[env_name]

    resolved_app: Path | None = None
    if app_path_str.strip():
        cand = Path(app_path_str.strip())
        resolved_app = cand if cand.is_absolute() else (project_root / cand)
        resolved_app = resolved_app.resolve()
        if not resolved_app.exists():
            raise ValueError(f"App path not found: {resolved_app}")

    log_path = project_root / "results" / "workbench_inspect_appium.log"
    masker = val.env_ctx.masker if val.env_ctx else None
    with AppiumManager(log_path=log_path, masker=masker) as mgr:
        driver = create_appium_driver(mgr.url, profile, env_cfg, app_path=resolved_app)
        try:
            if wait_seconds > 0:
                time.sleep(min(wait_seconds, 10.0))
            xml_text = driver.page_source
        finally:
            driver.quit()
    return inspect_xml_payload(
        project_root,
        xml_text=xml_text,
        platform=platform,
        screen_name=screen_name,
    )


def save_screen_locators(
    project_root: Path,
    *,
    screen_name: str,
    platform: str,
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Merge inspected locators for a screen into screens/screens.yaml after validation."""
    clean_screen = _safe_slug(screen_name, "screen")
    if not entries:
        raise ValueError("Select or provide at least one locator entry to save.")

    screens_path = project_root / "screens" / "screens.yaml"
    screens_path.parent.mkdir(parents=True, exist_ok=True)
    raw: dict[str, Any] = {}
    if screens_path.is_file():
        loaded = yaml.safe_load(screens_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            raw = loaded

    screen_block = dict(raw.get(clean_screen) or {})
    saved_keys: list[str] = []
    for item in entries:
        key = _safe_slug(str(item.get("key") or ""), "element")
        strategy = str(item.get("strategy") or "").strip()
        value = str(item.get("value") or "").strip()
        sensitive = bool(item.get("sensitive", False))
        if not strategy or not value:
            continue
        existing = screen_block.get(key)
        plat_payload: dict[str, Any] = {strategy: value}
        if sensitive:
            plat_payload["sensitive"] = True
        if isinstance(existing, dict) and ("android" in existing or "ios" in existing):
            updated = dict(existing)
            updated[platform] = plat_payload
            if sensitive:
                updated["sensitive"] = True
            screen_block[key] = updated
        else:
            screen_block[key] = {platform: plat_payload}
        saved_keys.append(f"{clean_screen}.{key}")

    if not saved_keys:
        raise ValueError("No valid strategy/value entries to save.")
    raw[clean_screen] = screen_block
    # Validate whole catalog cleanly before writing
    ScreensCatalog(raw)
    screens_path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return {"ok": True, "screen": clean_screen, "saved_targets": saved_keys}


def _dump_frontmatter_md(metadata: dict[str, Any], body: str) -> str:
    yaml_text = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True).strip()
    body_text = (body or "").strip()
    return f"---\n{yaml_text}\n---\n\n{body_text}\n"


def save_flow_spec(
    project_root: Path,
    *,
    name: str,
    params: list[str],
    steps: list[Any],
    notes: str = "",
) -> dict[str, Any]:
    """Save a reusable workflow (`flows/<name>.md`) and validate it parses cleanly."""
    clean_parts = [_safe_slug(part, "flow") for part in (name or "").replace("\\", "/").split("/") if part.strip()]
    if not clean_parts:
        raise ValueError("Flow name is required (for example `auth/login`).")
    rel_name = "/".join(clean_parts)
    if not steps:
        raise ValueError("Workflow must include at least one step.")

    flows_dir = (project_root / "flows").resolve()
    flows_dir.mkdir(parents=True, exist_ok=True)
    dest = (flows_dir / f"{rel_name}.md").resolve()
    if not dest.is_relative_to(flows_dir):
        raise ValueError("Flow path escapes flows/ directory.")
    dest.parent.mkdir(parents=True, exist_ok=True)

    meta: dict[str, Any] = {
        "name": rel_name,
        "params": [str(p).strip() for p in params if str(p).strip()],
        "steps": steps,
    }
    dest.write_text(_dump_frontmatter_md(meta, notes or f"Reusable workflow `{rel_name}`."), encoding="utf-8")
    flows, errors = load_flows(flows_dir)
    if rel_name not in flows or errors:
        raise ValueError(f"Flow validation failed: {'; '.join(errors) or 'unknown parse error'}")
    return {"ok": True, "name": rel_name, "source_path": dest.relative_to(project_root).as_posix()}


def save_test_journey(project_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Save an end-to-end test journey (`specs/<suite>/<id>.md`) and verify it loads."""
    spec_id = _safe_slug(str(payload.get("id") or ""), "TEST-001")
    title = str(payload.get("title") or "").strip()
    if not title:
        raise ValueError("Journey title is required.")
    suite = _safe_slug(str(payload.get("suite") or "smoke"), "smoke")
    steps = list(payload.get("steps") or [])
    if not steps:
        raise ValueError("Journey must contain at least one main step.")

    specs_dir = (project_root / "specs").resolve()
    suite_dir = (specs_dir / suite).resolve()
    if not suite_dir.is_relative_to(specs_dir):
        raise ValueError("Suite path escapes specs/ directory.")
    suite_dir.mkdir(parents=True, exist_ok=True)
    dest = (suite_dir / f"{spec_id}.md").resolve()

    meta: dict[str, Any] = {
        "id": spec_id,
        "title": title,
        "tags": [str(t).strip() for t in (payload.get("tags") or [suite]) if str(t).strip()],
        "platforms": list(payload.get("platforms") or ["android"]),
        "reset": str(payload.get("reset") or "clear_data"),
    }
    if payload.get("retries") is not None and str(payload.get("retries")).strip() != "":
        meta["retries"] = int(payload["retries"])
    if payload.get("setup"):
        meta["setup"] = list(payload["setup"])
    meta["steps"] = steps
    if payload.get("teardown"):
        meta["teardown"] = list(payload["teardown"])
    if payload.get("expected"):
        meta["expected"] = str(payload["expected"]).strip()

    dest.write_text(
        _dump_frontmatter_md(meta, str(payload.get("notes") or f"# {spec_id}: {title}")),
        encoding="utf-8",
    )
    specs, errors = load_specs(specs_dir)
    if not any(s.id == spec_id for s in specs) or errors:
        raise ValueError(f"Journey parse error: {'; '.join(errors) or 'failed to load'}")
    return {"ok": True, "id": spec_id, "suite": suite, "source_path": dest.relative_to(project_root).as_posix()}


def run_workspace_validation(
    project_root: Path,
    *,
    suite: str | None = None,
    env_name: str | None = None,
    platform: str | None = None,
    app_path_str: str = "",
) -> dict[str, Any]:
    """Run static validation over specs/flows/screens/env and serialize issues."""
    resolved_app: Path | None = None
    if app_path_str.strip():
        cand = Path(app_path_str.strip())
        resolved_app = cand if cand.is_absolute() else (project_root / cand)
    res = validate_project(
        project_root,
        env_name=env_name or None,
        suite=suite or None,
        platform=platform or None,
        app_path=resolved_app,
    )
    return {
        "ok": res.ok,
        "specs_count": len(res.specs),
        "flows_count": len(res.flows),
        "errors": [{"location": i.location, "message": i.message} for i in res.errors],
        "warnings": [{"location": i.location, "message": i.message} for i in res.warnings],
    }


class RunJobManager:
    """Thread-safe coordinator for runs started from the QAlprit Workbench UI."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {
            "running": False,
            "run_id": "",
            "status": "idle",
            "logs": [],
            "report_url": "",
            "summary": None,
            "error": "",
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self._state["running"],
                "run_id": self._state["run_id"],
                "status": self._state["status"],
                "logs": list(self._state["logs"]),
                "report_url": self._state["report_url"],
                "summary": self._state["summary"],
                "error": self._state["error"],
            }

    def _append_log(self, line: str) -> None:
        with self._lock:
            self._state["logs"].append(line)

    def start(self, project_root: Path, results_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
        platform = str(payload.get("platform") or "android")
        device_profile = str(payload.get("device_profile") or "android_emulator")
        env_name = str(payload.get("env_name") or "qa")
        suite = str(payload.get("suite") or "").strip() or None
        retries = max(0, int(payload.get("retries") or 0))
        dry_run = bool(payload.get("dry_run", False))
        app_raw = str(payload.get("app_path") or "").strip()

        resolved_app: Path | None = None
        if app_raw:
            cand = Path(app_raw)
            resolved_app = cand if cand.is_absolute() else (project_root / cand)
            resolved_app = resolved_app.resolve()
        elif not dry_run:
            raise ValueError("Select or upload an app (.apk/.app/.ipa) before starting a live run.")

        val = validate_project(
            project_root,
            env_name=env_name,
            suite=suite,
            platform=platform,
            app_path=resolved_app if not dry_run else None,
        )
        if not val.ok:
            raise ValueError("; ".join(e.message for e in val.errors[:5]))
        if device_profile not in val.devices:
            raise ValueError(f"Unknown device profile '{device_profile}'.")
        profile = val.devices[device_profile]
        if profile.platform != platform:
            raise ValueError(f"Device profile '{device_profile}' is for '{profile.platform}', not '{platform}'.")
        env_cfg = val.environments[env_name]
        specs: list[TestSpec] = list(val.specs)
        if not specs:
            raise ValueError("No matching test journeys found to run.")

        if dry_run:
            return {
                "dry_run": True,
                "platform": platform,
                "device_profile": device_profile,
                "env_name": env_name,
                "specs": [
                    {
                        "id": s.id,
                        "title": s.title,
                        "suite": s.suite,
                        "reset": s.reset,
                        "steps": len(s.steps),
                    }
                    for s in specs
                ],
            }

        with self._lock:
            if self._state["running"]:
                raise RuntimeError("A test run is already in progress. Wait for completion before starting another.")
            run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + f"_{platform}_{env_name}"
            self._state = {
                "running": True,
                "run_id": run_id,
                "status": "running",
                "logs": [f"[{run_id}] Validated {len(specs)} journey(s). Starting Appium session..."],
                "report_url": "",
                "summary": None,
                "error": "",
            }

        def _worker() -> None:
            run_dir = results_dir / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            appium_log_path = run_dir / "appium.log"
            masker = val.env_ctx.masker if val.env_ctx else None
            try:
                with AppiumManager(log_path=appium_log_path, masker=masker) as mgr:
                    self._append_log(f"[{run_id}] Appium ready at {mgr.url}. Executing tests...")
                    runner = TestRunner(
                        driver_factory=lambda auto_accept: create_appium_driver(
                            mgr.url,
                            profile,
                            env_cfg,
                            app_path=resolved_app,
                            auto_accept_alerts=auto_accept,
                        ),
                        profile=profile,
                        profile_name=device_profile,
                        env_name=env_name,
                        env_cfg=env_cfg,
                        env_ctx=val.env_ctx,  # type: ignore[arg-type]
                        screens=val.screens,  # type: ignore[arg-type]
                        flows=val.flows,
                        run_dir=run_dir,
                        run_id=run_id,
                        app_path=resolved_app,
                        suite=suite or "all",
                        tags=[],
                        retries=retries,
                    )
                    run_result = runner.run(
                        specs,
                        appium_log_rel="appium.log" if appium_log_path.exists() else None,
                    )
                write_results_json(run_result, run_dir / "results.json", masker=masker)
                generate_reports(run_result, run_dir, masker=masker)
                summary = run_result.summary.model_dump()
                self._append_log(
                    f"[{run_id}] Finished: {summary['passed']} passed, {summary['failed']} failed, {summary['skipped']} skipped."
                )
                with self._lock:
                    self._state["running"] = False
                    self._state["status"] = "completed"
                    self._state["summary"] = summary
                    self._state["report_url"] = f"/results/{run_id}/report.html"
            except Exception as exc:
                msg = masker.mask(str(exc)) if masker else str(exc)
                self._append_log(f"[{run_id}] ERROR: {msg}")
                with self._lock:
                    self._state["running"] = False
                    self._state["status"] = "error"
                    self._state["error"] = msg

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        return {"dry_run": False, "started": True, "run_id": run_id}
