"""Track the virtual devices QAlprit starts, and whether their windows are hidden.

Devices run in the background by default so the emulator or simulator window
does not cover the Workbench while recording. The Workbench mirror shows the
screen instead. QAlprit remembers the devices it started hidden, so it can list
them in the top bar and shut them down when the app quits. Devices the user
started, or started with a visible window, are left alone.
"""

from __future__ import annotations

import json
import platform
import subprocess
import threading
from pathlib import Path
from typing import Any

from qalprit.devices import list_android_devices, list_ios_simulators
from qalprit.doctor import (
    _macos_xcode_env,
    find_android_tool,
    find_xcode_developer_dir,
    win_subprocess_kwargs,
)
from qalprit.paths import get_settings_path, load_user_settings

SETTING_KEY = "show_device_window"
ANDROID_HIDDEN_ARGS = ["-no-window", "-no-audio"]

_lock = threading.Lock()


def device_window_visible() -> bool:
    """True when the user wants emulator and simulator windows on screen. Default is hidden."""
    return bool(load_user_settings().get(SETTING_KEY, False))


def _registry_path() -> Path:
    return get_settings_path().parent / "started-devices.json"


def _load() -> list[dict[str, Any]]:
    path = _registry_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [d for d in data if isinstance(d, dict) and d.get("kind") in {"android", "ios"} and d.get("identifier")]


def _save(entries: list[dict[str, Any]]) -> None:
    path = _registry_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def remember_started(kind: str, identifier: str, *, name: str = "", hidden: bool) -> None:
    """Record a device QAlprit just started."""
    ident = (identifier or "").strip()
    if kind not in {"android", "ios"} or not ident:
        return
    with _lock:
        entries = [e for e in _load() if not (e["kind"] == kind and e["identifier"] == ident)]
        entries.append({"kind": kind, "identifier": ident, "name": name or ident, "hidden": bool(hidden)})
        _save(entries)


def forget(kind: str = "", identifier: str = "") -> None:
    """Drop one device, every device of a kind, or everything when both are empty."""
    with _lock:
        keep = []
        for e in _load():
            if kind and e["kind"] != kind:
                keep.append(e)
            elif identifier and e["identifier"] != identifier and e.get("name") != identifier:
                keep.append(e)
        _save(keep)


def _running_identifiers() -> tuple[set[str], set[str]]:
    android: set[str] = set()
    ios: set[str] = set()
    try:
        devs, _ = list_android_devices()
        android = {
            d.identifier
            for d in devs
            if d.kind == "emulator" and not d.identifier.startswith("avd:") and d.state == "device"
        }
    except Exception:
        pass
    if platform.system() == "Darwin":
        try:
            sims, _ = list_ios_simulators()
            ios = {s.identifier for s in sims if str(s.state or "").lower() == "booted"}
        except Exception:
            pass
    return android, ios


def started_devices(*, prune: bool = True) -> list[dict[str, Any]]:
    """Devices QAlprit started that are still running. Stopped ones are dropped from the list."""
    with _lock:
        entries = _load()
        if not entries:
            return []
        if not prune:
            return entries
        android, ios = _running_identifiers()
        alive = [
            e for e in entries
            if (e["kind"] == "android" and e["identifier"] in android)
            or (e["kind"] == "ios" and e["identifier"] in ios)
        ]
        if len(alive) != len(entries):
            _save(alive)
        return alive


def android_avd_name(adb_bin: str, serial: str) -> str:
    """The AVD name of a running emulator. The emulator console often needs an auth token, so read the system property first."""
    attempts = [
        ["shell", "getprop", "ro.boot.qemu.avd_name"],
        ["shell", "getprop", "ro.kernel.qemu.avd_name"],
        ["emu", "avd", "name"],
    ]
    for args in attempts:
        try:
            proc = subprocess.run(
                [adb_bin, "-s", serial, *args],
                capture_output=True, text=True, timeout=5, **win_subprocess_kwargs(),
            )
        except Exception:
            continue
        lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip() and ln.strip() != "OK"]
        if lines:
            return lines[0]
    return ""


def _windowless_avd_names() -> set[str]:
    """AVD names of emulator processes running with -no-window, read from the process list."""
    try:
        if platform.system() == "Windows":
            cmd = ["wmic", "process", "where", "name like '%qemu%' or name like 'emulator%'", "get", "CommandLine"]
        else:
            cmd = ["ps", "-axo", "command"]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=5, **win_subprocess_kwargs()).stdout or ""
    except Exception:
        return set()
    names: set[str] = set()
    for line in out.splitlines():
        if "-no-window" not in line or ("qemu" not in line and "emulator" not in line):
            continue
        parts = line.split()
        for i, tok in enumerate(parts):
            if tok == "-avd" and i + 1 < len(parts):
                names.add(parts[i + 1])
            elif tok.startswith("@") and len(tok) > 1:
                names.add(tok[1:])
    return names


def windowless_android_emulators() -> list[dict[str, Any]]:
    """Running Android emulators that have no window, whoever started them.

    The started-device registry misses emulators left over from an earlier session,
    so this checks the emulator processes directly.
    """
    names = _windowless_avd_names()
    if not names:
        return []
    adb_bin = find_android_tool("adb")
    if not adb_bin:
        return []
    android, _ = _running_identifiers()
    found = []
    for serial in sorted(android):
        name = android_avd_name(adb_bin, serial)
        if name in names:
            found.append({"kind": "android", "identifier": serial, "name": name, "hidden": True})
    return found


def is_hidden(kind: str, identifier: str) -> bool:
    return any(
        e["kind"] == kind and e["identifier"] == identifier and e.get("hidden")
        for e in started_devices(prune=False)
    )


def open_simulator_window() -> bool:
    """Bring the iOS Simulator window up. Returns False when no simulator app could be opened."""
    if platform.system() != "Darwin":
        return False
    xc_env = _macos_xcode_env()
    try:
        proc = subprocess.run(["open", "-a", "Simulator"], capture_output=True, text=True, env=xc_env, timeout=10)
        if proc.returncode == 0:
            return True
        xcode_dev = find_xcode_developer_dir(xc_env)
        if xcode_dev is not None:
            device_hub = xcode_dev.parent / "Applications" / "DeviceHub.app"
            if device_hub.exists():
                hub = subprocess.run(["open", "-a", str(device_hub)], capture_output=True, text=True, timeout=10)
                return hub.returncode == 0
    except Exception:
        return False
    return False


def show_window(kind: str, identifier: str = "") -> dict[str, Any]:
    """Show the window for a background device, where the platform allows it."""
    if kind == "ios":
        if not open_simulator_window():
            raise RuntimeError("Could not open the Simulator app. Check that Xcode is installed.")
        with _lock:
            entries = _load()
            for e in entries:
                if e["kind"] == "ios" and (not identifier or e["identifier"] == identifier):
                    e["hidden"] = False
            _save(entries)
        return {"ok": True, "kind": "ios"}
    raise ValueError(
        "An Android emulator started in the background cannot show its window. "
        "Turn on 'Show device window' in Settings, stop the emulator, then start it again."
    )


def shutdown_background_devices() -> dict[str, list[str]]:
    """Shut down the hidden devices QAlprit started. Called when the Workbench quits."""
    hidden = [e for e in started_devices() if e.get("hidden")]
    stopped_android: list[str] = []
    stopped_ios: list[str] = []
    adb_bin = find_android_tool("adb") if any(e["kind"] == "android" for e in hidden) else None
    xc_env = _macos_xcode_env() if any(e["kind"] == "ios" for e in hidden) else None
    for e in hidden:
        ident = e["identifier"]
        try:
            if e["kind"] == "android" and adb_bin:
                subprocess.run(
                    [adb_bin, "-s", ident, "emu", "kill"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    **win_subprocess_kwargs(),
                )
                stopped_android.append(ident)
            elif e["kind"] == "ios" and xc_env is not None:
                subprocess.run(["xcrun", "simctl", "shutdown", ident], capture_output=True, text=True, env=xc_env, timeout=20)
                stopped_ios.append(ident)
        except Exception:
            continue
    with _lock:
        done = set(stopped_android) | set(stopped_ios)
        _save([e for e in _load() if e["identifier"] not in done])
    return {"android": stopped_android, "ios": stopped_ios}
