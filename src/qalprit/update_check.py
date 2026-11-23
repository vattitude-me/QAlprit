"""Check PyPI for a newer QAlprit and, for a pip install, upgrade in place.

The Workbench asks at most once a day (the result is cached in settings.json),
sends no user data, and never upgrades without the user pressing Update.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from importlib import metadata
from typing import Any

from qalprit import __version__
from qalprit.paths import load_user_settings, save_user_settings

PYPI_URL = "https://pypi.org/pypi/qalprit/json"
RELEASES_URL = "https://github.com/vattitude-me/QAlprit/releases"
CHECK_INTERVAL_SEC = 24 * 60 * 60
ENABLED_KEY = "update_check_enabled"
CACHE_KEY = "update_check"

_lock = threading.Lock()
_install: dict[str, Any] = {"running": False, "status": "idle", "message": "", "log": ""}


def _version_key(text: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", str(text).split("+")[0])
    return tuple(int(p) for p in parts[:4]) or (0,)


def is_newer(latest: str, current: str) -> bool:
    return bool(latest) and _version_key(latest) > _version_key(current)


def checks_enabled() -> bool:
    if os.environ.get("QALPRIT_NO_UPDATE_CHECK"):
        return False
    return bool(load_user_settings().get(ENABLED_KEY, True))


def install_kind() -> str:
    """How this copy was installed: frozen (desktop app), editable (a source checkout) or pip."""
    if getattr(sys, "frozen", False):
        return "frozen"
    try:
        raw = metadata.distribution("qalprit").read_text("direct_url.json") or ""
        if raw and json.loads(raw).get("dir_info", {}).get("editable"):
            return "editable"
    except Exception:
        pass
    return "pip"


def _fetch_latest(timeout: float = 5.0) -> str:
    req = urllib.request.Request(PYPI_URL, headers={"User-Agent": f"qalprit-update-check/{__version__}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https URL
        data = json.loads(resp.read().decode("utf-8"))
    return str(data.get("info", {}).get("version") or "")


def check_for_update(force: bool = False) -> dict[str, Any]:
    """Return what the Workbench needs to show. Hits PyPI only when the cache is a day old or force is set."""
    enabled = checks_enabled()
    cache = load_user_settings().get(CACHE_KEY) or {}
    latest = str(cache.get("latest") or "")
    checked_at = float(cache.get("checked_at") or 0)
    error = ""
    due = time.time() - checked_at >= CHECK_INTERVAL_SEC
    if force or (enabled and due):
        try:
            latest = _fetch_latest()
            checked_at = time.time()
            save_user_settings({CACHE_KEY: {"latest": latest, "checked_at": checked_at}})
        except Exception as exc:  # offline, proxy, PyPI down
            error = f"Could not reach PyPI ({type(exc).__name__})."
    kind = install_kind()
    return {
        "enabled": enabled,
        "current": __version__,
        "latest": latest,
        "update_available": is_newer(latest, __version__),
        "checked_at": checked_at,
        "error": error,
        "install_kind": kind,
        "can_self_update": kind == "pip",
        "command": "pip install --upgrade qalprit",
        "releases_url": RELEASES_URL,
        "install": install_status(),
    }


def set_enabled(enabled: bool) -> dict[str, Any]:
    save_user_settings({ENABLED_KEY: bool(enabled)})
    return {"ok": True, "enabled": bool(enabled)}


def install_status() -> dict[str, Any]:
    with _lock:
        return dict(_install)


def start_self_update(version: str = "") -> dict[str, Any]:
    """Run pip in the background to upgrade this install. The user restarts QAlprit afterwards."""
    if install_kind() != "pip":
        raise RuntimeError("This copy of QAlprit was not installed with pip, so it cannot update itself.")
    spec = f"qalprit=={version}" if re.fullmatch(r"\d+(\.\d+){1,3}", version or "") else "qalprit"
    with _lock:
        if _install["running"]:
            return dict(_install)
        _install.update(running=True, status="running", message=f"Installing {spec}...", log="")

    def _worker() -> None:
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "--disable-pip-version-check", spec]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            out = ((proc.stdout or "") + (proc.stderr or ""))[-4000:]
            ok = proc.returncode == 0
            msg = "Updated. Close QAlprit and start it again to use the new version." if ok else "The update failed. See the log, or run the command yourself."
            with _lock:
                _install.update(running=False, status="done" if ok else "error", message=msg, log=out)
        except Exception as exc:
            with _lock:
                _install.update(running=False, status="error", message=f"The update failed: {exc}", log="")

    threading.Thread(target=_worker, daemon=True).start()
    return install_status()
