"""Inspect and selectively remove QAlprit-created tool data, caches, AVDs, configs, and workspaces."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from qalprit.paths import (
    DEFAULT_AVD_NAME,
    get_legacy_dot_qalprit_dir,
    get_legacy_workspace_dir,
    get_qalprit_avd_home,
    get_qalprit_data_dir,
    get_settings_path,
    get_standard_workspace_dir,
    get_user_android_sdk_dir,
)


def _safe_path_size(path: Path) -> int:
    """Return recursive byte size of path without traversing symlinks/reparse points."""
    try:
        st = os.lstat(path)
    except OSError:
        return 0
    if not path.is_dir() or path.is_symlink():
        return int(st.st_size)
    attrs = getattr(st, "st_file_attributes", 0)
    if attrs & getattr(os, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
        return 0
    total = 0
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        total += _safe_path_size(Path(entry.path))
                    else:
                        total += int(entry.stat(follow_symlinks=False).st_size)
                except OSError:
                    continue
    except OSError:
        return 0
    return total


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for p in paths:
        rp = str(p.resolve())
        if rp not in seen:
            seen.add(rp)
            out.append(p.resolve())
    return out


def inspect_cleanup_targets(active_workspace: Path | None = None) -> dict[str, Any]:
    """Return every QAlprit-managed and legacy folder with size, classification, and safety impact."""
    ws = (active_workspace or get_standard_workspace_dir()).resolve()
    data_dir = get_qalprit_data_dir()
    sdk_dir = get_user_android_sdk_dir()
    avd_dir = get_qalprit_avd_home()
    legacy_dot = get_legacy_dot_qalprit_dir()
    legacy_ws = get_legacy_workspace_dir()
    settings_path = get_settings_path()
    legacy_android_avd = (Path.home() / ".android" / "avd").resolve()

    raw_items: list[dict[str, Any]] = []

    def _add(
        item_id: str,
        label: str,
        category: str,
        paths: list[Path],
        safe_for_new_install: bool,
        description: str,
        *,
        default_checked: bool = False,
    ) -> None:
        uniq = _dedupe_paths(paths)
        existing = [p for p in uniq if p.exists() or p.is_symlink()]
        size = sum(_safe_path_size(p) for p in existing)
        raw_items.append(
            {
                "id": item_id,
                "label": label,
                "category": category,
                "safe_for_new_install": safe_for_new_install,
                "default_checked": bool(default_checked and existing),
                "exists": bool(existing),
                "size_bytes": size,
                "paths": [str(p) for p in uniq],
                "existing_paths": [str(p) for p in existing],
                "description": description,
            }
        )

    active_uses_legacy_sdk = sdk_dir == (legacy_dot / "android-sdk").resolve()
    _add(
        "legacy_dot_qalprit",
        "Legacy .qalprit Folder (~/.qalprit)",
        "legacy",
        [legacy_dot],
        safe_for_new_install=not active_uses_legacy_sdk,
        default_checked=not active_uses_legacy_sdk,
        description=(
            "Currently supplying your active Android SDK — deleting it removes your downloaded emulator until re-setup."
            if active_uses_legacy_sdk
            else "Previous-version SDK/emulator folder (~4–5 GB). Safe to delete without affecting your OS-standard v0.1.17+ setup."
        ),
    )

    active_uses_legacy_avd = avd_dir == legacy_android_avd
    legacy_avd_paths = [
        legacy_android_avd / f"{DEFAULT_AVD_NAME}.avd",
        legacy_android_avd / f"{DEFAULT_AVD_NAME}.ini",
    ]
    _add(
        "legacy_avd_qalprit_pixel",
        f"Legacy Virtual Phone inside ~/.android/avd ({DEFAULT_AVD_NAME})",
        "legacy",
        legacy_avd_paths,
        safe_for_new_install=not active_uses_legacy_avd,
        default_checked=not active_uses_legacy_avd,
        description=(
            "Currently your active QAlprit_Pixel AVD — delete only if resetting the virtual phone."
            if active_uses_legacy_avd
            else "Leftover QAlprit_Pixel AVD from an earlier version inside ~/.android/avd. Safe to delete without touching Android Studio AVDs or NTUSER.DAT."
        ),
    )

    if legacy_ws != ws:
        _add(
            "legacy_workspace",
            "Previous Workspace Folder (~/QAlpritWorkspace)",
            "legacy",
            [legacy_ws],
            safe_for_new_install=True,
            default_checked=False,
            description=(
                "Pre-v0.1.17 workspace folder. Safe to delete for the new installation, AFTER backing up any specs/flows/reports you still want to keep."
            ),
        )

    _add(
        "caches_and_logs",
        "Logs, Appium Cache & Android Home Cache",
        "cache",
        [
            data_dir / "logs",
            data_dir / "appium_home",
            data_dir / "android-home",
            ws / ".webview",
        ],
        safe_for_new_install=True,
        default_checked=False,
        description="Regenerated automatically on launch; safe to clear to reclaim space.",
    )

    if not active_uses_legacy_sdk:
        _add(
            "active_sdk",
            "QAlprit Android SDK & Emulator (~2–5 GB)",
            "sdk",
            [sdk_dir],
            safe_for_new_install=False,
            default_checked=False,
            description="Downloaded Android Emulator & system image inside QAlprit AppData. Deleting frees ~4–5 GB (re-downloads only if you set up emulator again).",
        )

    if not active_uses_legacy_avd:
        _add(
            "active_avd",
            f"QAlprit Virtual Android Phone ({DEFAULT_AVD_NAME})",
            "avd",
            [avd_dir / f"{DEFAULT_AVD_NAME}.avd", avd_dir / f"{DEFAULT_AVD_NAME}.ini"],
            safe_for_new_install=False,
            default_checked=False,
            description="Virtual phone user-data & snapshot files managed inside QAlprit AppData.",
        )

    _add(
        "settings_config",
        "QAlprit Saved Preferences (settings.json)",
        "config",
        [settings_path],
        safe_for_new_install=False,
        default_checked=False,
        description="Saved custom workspace/SDK folder paths and system-SDK preference.",
    )

    _add(
        "active_workspace",
        "Active Test Workspace (specs, flows, screens, apps & reports)",
        "workspace",
        [ws],
        safe_for_new_install=False,
        default_checked=False,
        description="Your recorded tests, uploaded binaries, and proof reports. Keep checked ONLY if you want to erase all tests.",
    )

    return {
        "active_workspace": str(ws),
        "data_dir": str(data_dir),
        "items": raw_items,
    }


def _delete_single_path(path: Path) -> None:
    if path.is_symlink():
        path.unlink(missing_ok=True)
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=False)
        return
    if path.exists():
        path.unlink(missing_ok=True)


def execute_selective_cleanup(
    target_ids: list[str],
    active_workspace: Path | None = None,
) -> dict[str, Any]:
    """Delete only the user-selected QAlprit cleanup target IDs and report freed space."""
    cleaned_ids = [str(x).strip() for x in (target_ids or []) if str(x).strip()]
    if not cleaned_ids:
        raise ValueError("Select at least one cleanup item to delete.")

    plan = inspect_cleanup_targets(active_workspace=active_workspace)
    by_id = {item["id"]: item for item in plan["items"]}
    unknown = [tid for tid in cleaned_ids if tid not in by_id]
    if unknown:
        raise ValueError(f"Unknown cleanup target(s): {', '.join(unknown)}")

    removed_paths: list[str] = []
    freed_bytes = 0
    errors: list[str] = []

    for tid in cleaned_ids:
        item = by_id[tid]
        for raw_p in item["existing_paths"]:
            p = Path(raw_p)
            if not p.exists() and not p.is_symlink():
                continue
            size = _safe_path_size(p)
            try:
                _delete_single_path(p)
                removed_paths.append(str(p))
                freed_bytes += size
            except Exception as exc:
                errors.append(f"{p}: {exc}")

    return {
        "ok": len(errors) == 0,
        "removed_ids": cleaned_ids,
        "removed_paths": removed_paths,
        "freed_bytes": freed_bytes,
        "errors": errors,
        "remaining": inspect_cleanup_targets(active_workspace=active_workspace),
    }
