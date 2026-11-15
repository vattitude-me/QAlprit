"""Backend workspace actions for the interactive QAlprit Workbench UI."""

from __future__ import annotations

import os
import plistlib
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import frontmatter
import yaml

from qalprit.appium_manager import AppiumManager
from qalprit.config_loader import (
    DeviceProfile,
    ScreensCatalog,
    load_devices,
    load_environments,
)
from qalprit.doctor import find_android_sdk, win_subprocess_kwargs
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


def _read_axml_len8(buf: bytes, offset: int) -> tuple[int, int]:
    first = buf[offset]
    if first & 0x80:
        second = buf[offset + 1]
        return ((first & 0x7F) << 8) | second, offset + 2
    return first, offset + 1


def _read_axml_len16(buf: bytes, offset: int) -> tuple[int, int]:
    first = struct.unpack_from("<H", buf, offset)[0]
    if first & 0x8000:
        second = struct.unpack_from("<H", buf, offset + 2)[0]
        return ((first & 0x7FFF) << 16) | second, offset + 4
    return first, offset + 2


def _parse_android_manifest_package(data: bytes) -> str:
    """Extract the Android package ID from binary AndroidManifest.xml (AXML)."""
    if len(data) < 16 or struct.unpack_from("<H", data, 0)[0] != 0x0003:
        return ""

    pos = struct.unpack_from("<H", data, 2)[0]
    strings: list[str] = []
    while pos + 8 <= len(data):
        chunk_type, hdr_size, chunk_size = struct.unpack_from("<HHI", data, pos)
        if chunk_size < 8 or pos + chunk_size > len(data):
            break
        if chunk_type == 0x0001 and hdr_size >= 28:  # RES_STRING_POOL_TYPE
            str_count, _style_count, flags, strings_start = struct.unpack_from("<IIII", data, pos + 8)
            is_utf8 = bool(flags & 0x0100)
            offsets_base = pos + hdr_size
            pool_base = pos + strings_start
            for i in range(str_count):
                if offsets_base + (i + 1) * 4 > pos + chunk_size:
                    break
                rel_off = struct.unpack_from("<I", data, offsets_base + i * 4)[0]
                s_off = pool_base + rel_off
                if s_off >= pos + chunk_size:
                    strings.append("")
                    continue
                if is_utf8:
                    _, cur = _read_axml_len8(data, s_off)
                    byte_len, cur = _read_axml_len8(data, cur)
                    strings.append(data[cur : cur + byte_len].decode("utf-8", errors="replace"))
                else:
                    char_len, cur = _read_axml_len16(data, s_off)
                    strings.append(data[cur : cur + char_len * 2].decode("utf-16-le", errors="replace"))
        elif chunk_type == 0x0102 and hdr_size >= 16 and pos + hdr_size + 20 <= pos + chunk_size:
            name_idx = struct.unpack_from("<I", data, pos + hdr_size + 4)[0]
            attr_start, attr_size, attr_count = struct.unpack_from("<HHH", data, pos + hdr_size + 8)
            tag_name = strings[name_idx] if name_idx < len(strings) else ""
            if tag_name == "manifest" and attr_size >= 20:
                attr_pos = pos + hdr_size + attr_start
                for i in range(attr_count):
                    cur_attr = attr_pos + i * attr_size
                    if cur_attr + 20 > pos + chunk_size:
                        break
                    attr_name_idx, raw_val_idx = struct.unpack_from("<II", data, cur_attr + 4)
                    data_type, attr_data = struct.unpack_from("<BI", data, cur_attr + 15)
                    attr_name = strings[attr_name_idx] if attr_name_idx < len(strings) else ""
                    if attr_name == "package":
                        if raw_val_idx < len(strings):
                            return strings[raw_val_idx]
                        if data_type == 3 and attr_data < len(strings):
                            return strings[attr_data]
        pos += chunk_size
    return ""


def _detect_apk_package(apk_path: Path) -> str:
    """Detect Android package ID directly from APK binary manifest or aapt2/aapt."""
    try:
        with zipfile.ZipFile(apk_path, "r") as zf:
            if "AndroidManifest.xml" in zf.namelist():
                pkg = _parse_android_manifest_package(zf.read("AndroidManifest.xml"))
                if pkg:
                    return pkg
    except Exception:
        pass

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
                **win_subprocess_kwargs(),
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip().splitlines()[0].strip()
        except Exception:
            continue
    return ""


_APP_META_CACHE: dict[tuple[str, int, int], str] = {}


def _detect_ios_bundle_id(app_path: Path) -> str:
    """Extract CFBundleIdentifier from .app, .ipa, or zipped .app bundle."""
    try:
        if app_path.is_dir():
            plist_file = app_path / "Info.plist"
            if plist_file.is_file():
                data = plistlib.loads(plist_file.read_bytes())
                return str(data.get("CFBundleIdentifier") or "").strip()
            return ""
        with zipfile.ZipFile(app_path, "r") as zf:
            plists = [
                name
                for name in zf.namelist()
                if name.endswith("/Info.plist") and ".app/" in name
            ]
            if not plists:
                return ""
            # Prefer shallowest .app/Info.plist (the main bundle rather than PlugIns/Frameworks)
            plists.sort(key=lambda n: (n.count("/"), len(n)))
            data = plistlib.loads(zf.read(plists[0]))
            return str(data.get("CFBundleIdentifier") or "").strip()
    except Exception:
        return ""


def _detect_app_identifier(app_path: Path, kind: str) -> str:
    """Detect Android package ID or iOS bundle ID, caching by path/size/mtime."""
    try:
        st = app_path.stat()
        cache_key = (str(app_path.resolve()), st.st_size, st.st_mtime_ns)
    except OSError:
        cache_key = None
    if cache_key is not None and cache_key in _APP_META_CACHE:
        return _APP_META_CACHE[cache_key]

    ident = ""
    if kind == "apk":
        ident = _detect_apk_package(app_path)
    elif kind in {"ipa", "zip", "app"}:
        ident = _detect_ios_bundle_id(app_path)
    if cache_key is not None:
        _APP_META_CACHE[cache_key] = ident
    return ident


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
            kind = ext.lstrip(".")
            items.append(
                {
                    "name": entry.name,
                    "rel_path": f"apps/{entry.name}",
                    "kind": kind,
                    "size_bytes": entry.stat().st_size,
                    "detected_package": _detect_app_identifier(entry, kind),
                }
            )
        elif entry.is_dir() and ext == ".app":
            items.append(
                {
                    "name": entry.name,
                    "rel_path": f"apps/{entry.name}",
                    "kind": "app",
                    "size_bytes": 0,
                    "detected_package": _detect_app_identifier(entry, "app"),
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
    detected_pkg = _detect_app_identifier(dest, ext.lstrip("."))
    return {
        "ok": True,
        "name": safe_name,
        "rel_path": f"apps/{safe_name}",
        "kind": ext.lstrip("."),
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


def _resolve_device_profile(
    devices: dict[str, DeviceProfile],
    device_profile: str,
    platform: str,
) -> DeviceProfile:
    """Resolve a named YAML device profile or a live USB/emulator identifier (`live:<plat>:<serial>`)."""
    if device_profile.startswith("live:"):
        parts = device_profile.split(":", 2)
        if len(parts) == 3 and parts[1] in {"android", "ios"} and parts[2].strip():
            live_plat = parts[1]
            serial = parts[2].strip()
        elif len(parts) >= 2 and parts[1].strip():
            live_plat = platform if platform in {"android", "ios"} else "android"
            serial = parts[1].strip()
        else:
            raise ValueError(f"Invalid live device identifier '{device_profile}'.")
        if live_plat != platform:
            raise ValueError(f"Selected device '{serial}' is for '{live_plat}', not '{platform}'.")
        is_emu = serial.startswith("emulator-") or (live_plat == "ios" and len(serial) == 36 and "-" in serial)
        return DeviceProfile(
            platform="android" if live_plat == "android" else "ios",
            target_type="simulator" if is_emu else "device",
            device_name=serial,
            udid=serial,
            automation_name="UiAutomator2" if live_plat == "android" else "XCUITest",
            auto_accept_alerts=True,
            capabilities={"appWaitActivity": "*"} if live_plat == "android" else {},
        )

    if device_profile not in devices:
        raise ValueError(f"Unknown device profile '{device_profile}'.")
    profile = devices[device_profile]
    if profile.platform != platform:
        raise ValueError(f"Device '{device_profile}' is for '{profile.platform}', not '{platform}'.")
    return profile


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
    if env_name not in val.environments:
        raise ValueError(f"Unknown environment '{env_name}'.")
    profile = _resolve_device_profile(val.devices, device_profile, platform)
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


def _suite_id_prefix(suite: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9]", "", (suite or "").upper())
    mapping = {
        "SMOKE": "SMK",
        "REGRESSION": "REG",
        "AUTH": "AUTH",
        "CHECKOUT": "CHK",
        "SANITY": "SAN",
    }
    if clean in mapping:
        return mapping[clean]
    if len(clean) >= 3:
        return clean[:4]
    return clean or "TEST"


def next_journey_id(project_root: Path, suite: str = "smoke") -> str:
    """Return the next unused automated Test Journey ID for a suite (e.g. SMK-001, SMK-002)."""
    prefix = _suite_id_prefix(suite)
    specs, _ = load_specs(project_root / "specs")
    pat = re.compile(rf"^{re.escape(prefix)}-(\d+)$", re.IGNORECASE)
    used_nums: set[int] = set()
    used_ids = {s.id.upper() for s in specs}
    for s in specs:
        m = pat.match(s.id.strip())
        if m:
            used_nums.add(int(m.group(1)))
    num = (max(used_nums) + 1) if used_nums else 1
    while f"{prefix}-{num:03d}" in used_ids:
        num += 1
    return f"{prefix}-{num:03d}"


def upload_flow_markdown(project_root: Path, filename: str, content_text: str) -> dict[str, Any]:
    """Import an existing workflow `.md` file into `flows/` and validate it parses cleanly."""
    raw_name = Path(filename or "imported_flow.md").stem
    text = (content_text or "").strip()
    if not text:
        raise ValueError("Uploaded workflow file is empty.")
    post = frontmatter.loads(text)
    meta = dict(post.metadata or {})
    flow_name_raw = str(meta.get("name") or raw_name).strip()
    clean_parts = [_safe_slug(part, "flow") for part in flow_name_raw.replace("\\", "/").split("/") if part.strip()]
    if not clean_parts:
        clean_parts = [_safe_slug(raw_name, "imported_flow")]
    rel_name = "/".join(clean_parts)
    meta["name"] = rel_name
    if "steps" not in meta or not meta["steps"]:
        raise ValueError("Workflow markdown frontmatter must include a non-empty 'steps' list.")

    flows_dir = (project_root / "flows").resolve()
    flows_dir.mkdir(parents=True, exist_ok=True)
    dest = (flows_dir / f"{rel_name}.md").resolve()
    if not dest.is_relative_to(flows_dir):
        raise ValueError("Workflow path escapes flows/ directory.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    prev_text = dest.read_text(encoding="utf-8") if dest.exists() else None
    dest.write_text(_dump_frontmatter_md(meta, post.content or f"Imported workflow `{rel_name}`."), encoding="utf-8")
    flows, errors = load_flows(flows_dir)
    if rel_name not in flows or errors:
        if prev_text is not None:
            dest.write_text(prev_text, encoding="utf-8")
        else:
            dest.unlink(missing_ok=True)
        raise ValueError(f"Invalid workflow file: {'; '.join(errors) or 'parse error'}")
    return {"ok": True, "name": rel_name, "source_path": dest.relative_to(project_root).as_posix()}


def duplicate_flow_spec(project_root: Path, name: str, new_name: str = "") -> dict[str, Any]:
    """Duplicate an existing workflow `flows/<name>.md` to a new unique workflow name."""
    flows_dir = (project_root / "flows").resolve()
    flows, _ = load_flows(flows_dir)
    if name not in flows:
        raise ValueError(f"Workflow '{name}' was not found.")
    src_flow = flows[name]
    target_raw = (new_name or "").strip()
    if not target_raw:
        candidate = f"{name}_copy"
        idx = 2
        while candidate in flows:
            candidate = f"{name}_copy_{idx}"
            idx += 1
        target_raw = candidate

    clean_parts = [_safe_slug(part, "flow") for part in target_raw.replace("\\", "/").split("/") if part.strip()]
    if not clean_parts:
        raise ValueError("New workflow name is required.")
    rel_name = "/".join(clean_parts)
    if rel_name in flows:
        raise ValueError(f"Workflow '{rel_name}' already exists.")

    post = frontmatter.loads(src_flow.source_path.read_text(encoding="utf-8"))
    meta = dict(post.metadata or {})
    meta["name"] = rel_name
    dest = (flows_dir / f"{rel_name}.md").resolve()
    if not dest.is_relative_to(flows_dir):
        raise ValueError("Workflow path escapes flows/ directory.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_dump_frontmatter_md(meta, post.content or f"Duplicated from `{name}`."), encoding="utf-8")
    return {"ok": True, "name": rel_name, "source_path": dest.relative_to(project_root).as_posix()}


def delete_flow_spec(project_root: Path, name: str) -> dict[str, Any]:
    """Delete an existing workflow file inside `flows/` safely."""
    flows_dir = (project_root / "flows").resolve()
    flows, _ = load_flows(flows_dir)
    target_path: Path | None = None
    if name in flows:
        target_path = flows[name].source_path.resolve()
    else:
        cand = (flows_dir / f"{name}.md").resolve()
        if cand.is_file():
            target_path = cand
    if target_path is None or not target_path.is_file():
        raise ValueError(f"Workflow '{name}' was not found.")
    if not target_path.is_relative_to(flows_dir):
        raise ValueError("Refusing to delete file outside flows/ directory.")
    rel_path = target_path.relative_to(project_root).as_posix()
    target_path.unlink()
    return {"ok": True, "name": name, "deleted_path": rel_path}


def upload_spec_markdown(
    project_root: Path,
    filename: str,
    content_text: str,
    suite: str = "",
) -> dict[str, Any]:
    """Import an existing test journey `.md` file into `specs/<suite>/` and validate it."""
    raw_stem = Path(filename or "TEST-001.md").stem
    text = (content_text or "").strip()
    if not text:
        raise ValueError("Uploaded test journey file is empty.")
    post = frontmatter.loads(text)
    meta = dict(post.metadata or {})
    spec_id = _safe_slug(str(meta.get("id") or raw_stem), "TEST-001")
    meta["id"] = spec_id
    if not str(meta.get("title") or "").strip():
        meta["title"] = raw_stem.replace("_", " ").replace("-", " ").strip() or spec_id
    if "steps" not in meta or not meta["steps"]:
        raise ValueError("Journey markdown frontmatter must include a non-empty 'steps' list.")

    target_suite = _safe_slug(suite or "smoke", "smoke")
    specs_dir = (project_root / "specs").resolve()
    suite_dir = (specs_dir / target_suite).resolve()
    if not suite_dir.is_relative_to(specs_dir):
        raise ValueError("Suite path escapes specs/ directory.")
    suite_dir.mkdir(parents=True, exist_ok=True)
    dest = (suite_dir / f"{spec_id}.md").resolve()
    if not dest.is_relative_to(specs_dir):
        raise ValueError("Spec path escapes specs/ directory.")
    prev_text = dest.read_text(encoding="utf-8") if dest.exists() else None
    dest.write_text(_dump_frontmatter_md(meta, post.content or f"# {spec_id}: {meta['title']}"), encoding="utf-8")
    specs, errors = load_specs(specs_dir)
    if not any(s.id == spec_id for s in specs) or errors:
        if prev_text is not None:
            dest.write_text(prev_text, encoding="utf-8")
        else:
            dest.unlink(missing_ok=True)
        raise ValueError(f"Invalid test journey file: {'; '.join(errors) or 'parse error'}")
    return {"ok": True, "id": spec_id, "suite": target_suite, "source_path": dest.relative_to(project_root).as_posix()}


def duplicate_test_journey(
    project_root: Path,
    source_path: str,
    new_id: str = "",
    suite: str = "",
) -> dict[str, Any]:
    """Duplicate an existing test journey `.md` under `specs/` with a fresh Journey ID."""
    root = project_root.resolve()
    specs_dir = (root / "specs").resolve()
    src = (root / (source_path or "").lstrip("/")).resolve()
    if not src.is_relative_to(specs_dir) or not src.is_file():
        raise ValueError(f"Test journey file not found: {source_path}")

    post = frontmatter.loads(src.read_text(encoding="utf-8"))
    meta = dict(post.metadata or {})
    orig_id = str(meta.get("id") or src.stem)
    target_suite = _safe_slug(suite or src.parent.name or "smoke", "smoke")
    if target_suite == "specs":
        target_suite = "smoke"

    target_id = _safe_slug(new_id, "") if (new_id or "").strip() else next_journey_id(root, target_suite)
    meta["id"] = target_id
    if meta.get("title"):
        meta["title"] = f"{meta['title']} (Copy)"

    suite_dir = (specs_dir / target_suite).resolve()
    suite_dir.mkdir(parents=True, exist_ok=True)
    dest = (suite_dir / f"{target_id}.md").resolve()
    if not dest.is_relative_to(specs_dir):
        raise ValueError("Destination escapes specs/ directory.")
    if dest.exists():
        raise ValueError(f"Test journey '{target_id}' already exists.")
    dest.write_text(
        _dump_frontmatter_md(meta, post.content or f"# {target_id} (copied from {orig_id})"),
        encoding="utf-8",
    )
    return {"ok": True, "id": target_id, "suite": target_suite, "source_path": dest.relative_to(root).as_posix()}


def delete_test_journey(project_root: Path, source_path: str) -> dict[str, Any]:
    """Delete an existing test journey `.md` file inside `specs/` safely."""
    root = project_root.resolve()
    specs_dir = (root / "specs").resolve()
    target = (root / (source_path or "").lstrip("/")).resolve()
    if not target.is_relative_to(specs_dir):
        raise ValueError("Refusing to delete file outside specs/ directory.")
    if not target.is_file():
        raise ValueError(f"Test journey file not found: {source_path}")
    rel = target.relative_to(root).as_posix()
    target.unlink()
    return {"ok": True, "deleted_path": rel}


def reveal_in_file_manager(project_root: Path, rel_path: str = "") -> dict[str, Any]:
    """Open the workspace directory, QAlprit data directory, or a workspace file in Explorer / Finder."""
    root = project_root.resolve()
    raw = (rel_path or "").strip()
    if raw == "@data_dir":
        from qalprit.paths import get_qalprit_data_dir

        target = get_qalprit_data_dir()
    else:
        cleaned = raw.lstrip("/\\")
        target = (root / cleaned).resolve() if cleaned else root
        if not target.is_relative_to(root):
            raise ValueError("Path escapes workspace directory.")
    if not target.exists():
        target.mkdir(parents=True, exist_ok=True) if not target.suffix else target.parent.mkdir(parents=True, exist_ok=True)

    if sys.platform == "win32":
        if target.is_file():
            subprocess.Popen(["explorer", "/select,", str(target)])
        else:
            subprocess.Popen(["explorer", str(target)])
    elif sys.platform == "darwin":
        if target.is_file():
            subprocess.Popen(["open", "-R", str(target)])
        else:
            subprocess.Popen(["open", str(target)])
    else:
        dir_target = target if target.is_dir() else target.parent
        subprocess.Popen(["xdg-open", str(dir_target)])

    return {"ok": True, "opened_path": str(target)}


def update_storage_preferences(current_workspace: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Save user-approved storage directories / system SDK preference and ensure workspace folders."""
    from qalprit.desktop import ensure_workspace
    from qalprit.doctor import discover_system_software
    from qalprit.paths import save_user_settings

    updates: dict[str, Any] = {}
    if "data_dir" in payload:
        raw_data = str(payload.get("data_dir") or "").strip()
        if raw_data:
            data_path = Path(raw_data).expanduser().resolve()
            data_path.mkdir(parents=True, exist_ok=True)
            updates["data_dir"] = str(data_path)
        else:
            updates["data_dir"] = ""

    new_workspace = current_workspace.resolve()
    if "workspace_dir" in payload:
        raw_ws = str(payload.get("workspace_dir") or "").strip()
        if raw_ws:
            new_workspace = ensure_workspace(Path(raw_ws).expanduser().resolve())
            updates["workspace_dir"] = str(new_workspace)
        else:
            updates["workspace_dir"] = ""

    if "prefer_existing_system_sdk" in payload:
        updates["prefer_existing_system_sdk"] = bool(payload.get("prefer_existing_system_sdk"))

    saved = save_user_settings(updates)
    return {
        "ok": True,
        "settings": saved,
        "workspace_dir": str(new_workspace),
        "preflight": discover_system_software(new_workspace),
    }


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
        self._cancel_event = threading.Event()
        self._active_mgr: AppiumManager | None = None
        self._started_monotonic: float | None = None
        self._state: dict[str, Any] = {
            "running": False,
            "run_id": "",
            "status": "idle",
            "phase": "Idle",
            "progress_pct": 0,
            "elapsed_sec": 0.0,
            "logs": [],
            "report_url": "",
            "summary": None,
            "error": "",
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            elapsed = self._state["elapsed_sec"]
            if self._state["running"] and self._started_monotonic is not None:
                elapsed = round(time.monotonic() - self._started_monotonic, 1)
            return {
                "running": self._state["running"],
                "run_id": self._state["run_id"],
                "status": self._state["status"],
                "phase": self._state["phase"],
                "progress_pct": self._state["progress_pct"],
                "elapsed_sec": elapsed,
                "logs": list(self._state["logs"]),
                "report_url": self._state["report_url"],
                "summary": self._state["summary"],
                "error": self._state["error"],
            }

    def _append_log(self, line: str) -> None:
        with self._lock:
            self._state["logs"].append(line)

    def _set_progress(self, phase: str, pct: int) -> None:
        with self._lock:
            self._state["phase"] = phase
            self._state["progress_pct"] = max(0, min(100, pct))

    def stop(self) -> dict[str, Any]:
        """Request cancellation of the active test run and stop Appium if active."""
        with self._lock:
            if not self._state["running"]:
                return {"ok": True, "running": False, "status": self._state["status"]}
            self._cancel_event.set()
            self._state["status"] = "stopping"
            self._state["phase"] = "Stopping"
            run_id = self._state["run_id"]
            self._state["logs"].append(f"[{run_id}] Stop requested by user — halting test run...")
            mgr = self._active_mgr

        if mgr is not None:
            try:
                mgr.__exit__(None, None, None)
            except Exception:
                pass
        return {"ok": True, "running": True, "status": "stopping"}

    def start(self, project_root: Path, results_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
        platform = str(payload.get("platform") or "android")
        device_profile = str(payload.get("device_profile") or "android_emulator")
        env_name = str(payload.get("env_name") or "qa")
        suite = str(payload.get("suite") or "").strip() or None
        spec_id = str(payload.get("spec_id") or "").strip() or None
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
            spec_id=spec_id,
        )
        if not val.ok:
            raise ValueError("; ".join(e.message for e in val.errors[:5]))
        profile = _resolve_device_profile(val.devices, device_profile, platform)
        env_cfg = val.environments[env_name]
        specs: list[TestSpec] = list(val.specs)
        if not specs:
            if spec_id:
                raise ValueError(f"Journey '{spec_id}' did not match platform '{platform}' or suite '{suite or 'all'}'.")
            raise ValueError("No matching test journeys found to run.")

        if dry_run:
            return {
                "dry_run": True,
                "platform": platform,
                "device_profile": device_profile,
                "env_name": env_name,
                "spec_id": spec_id or "",
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
            self._cancel_event.clear()
            self._active_mgr = None
            self._started_monotonic = time.monotonic()
            self._state = {
                "running": True,
                "run_id": run_id,
                "status": "running",
                "phase": "Starting Appium",
                "progress_pct": 15,
                "elapsed_sec": 0.0,
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
                mgr = AppiumManager(log_path=appium_log_path, masker=masker)
                with self._lock:
                    self._active_mgr = mgr
                with mgr:
                    if self._cancel_event.is_set():
                        raise InterruptedError("Stopped by user.")
                    self._set_progress("Executing Tests", 45)
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
                if self._cancel_event.is_set():
                    raise InterruptedError("Stopped by user.")
                self._set_progress("Generating Report", 88)
                write_results_json(run_result, run_dir / "results.json", masker=masker)
                generate_reports(run_result, run_dir, masker=masker)
                summary = run_result.summary.model_dump()
                self._append_log(
                    f"[{run_id}] Finished: {summary['passed']} passed, {summary['failed']} failed, {summary['skipped']} skipped."
                )
                with self._lock:
                    elapsed = round(time.monotonic() - (self._started_monotonic or time.monotonic()), 1)
                    self._state["running"] = False
                    self._state["status"] = "completed"
                    self._state["phase"] = "Completed"
                    self._state["progress_pct"] = 100
                    self._state["elapsed_sec"] = elapsed
                    self._state["summary"] = summary
                    self._state["report_url"] = f"/results/{run_id}/report.html"
            except InterruptedError:
                self._append_log(f"[{run_id}] Stopped by user.")
                with self._lock:
                    elapsed = round(time.monotonic() - (self._started_monotonic or time.monotonic()), 1)
                    self._state["running"] = False
                    self._state["status"] = "stopped"
                    self._state["phase"] = "Stopped"
                    self._state["elapsed_sec"] = elapsed
            except Exception as exc:
                was_stopped = self._cancel_event.is_set()
                msg = masker.mask(str(exc)) if masker else str(exc)
                self._append_log(
                    f"[{run_id}] Stopped by user." if was_stopped else f"[{run_id}] ERROR: {msg}"
                )
                with self._lock:
                    elapsed = round(time.monotonic() - (self._started_monotonic or time.monotonic()), 1)
                    self._state["running"] = False
                    self._state["elapsed_sec"] = elapsed
                    if was_stopped:
                        self._state["status"] = "stopped"
                        self._state["phase"] = "Stopped"
                    else:
                        self._state["status"] = "error"
                        self._state["phase"] = "Error"
                        self._state["error"] = msg
            finally:
                with self._lock:
                    self._active_mgr = None

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        return {"dry_run": False, "started": True, "run_id": run_id}
