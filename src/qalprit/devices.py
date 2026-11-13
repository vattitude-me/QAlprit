"""Discover connected Android devices/emulators and iOS simulators."""

from __future__ import annotations

import json
import os
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path

from qalprit.doctor import find_android_tool


@dataclass
class DiscoveredDevice:
    platform: str
    name: str
    identifier: str
    state: str
    kind: str  # emulator | simulator | device


def list_android_devices() -> tuple[list[DiscoveredDevice], str | None]:
    """List connected adb devices and configured AVDs."""
    devices: list[DiscoveredDevice] = []
    err: str | None = None

    adb_bin = find_android_tool("adb")
    if adb_bin:
        try:
            proc = subprocess.run([adb_bin, "devices", "-l"], capture_output=True, text=True, timeout=6)
            if proc.returncode == 0:
                for line in proc.stdout.splitlines()[1:]:
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split()
                    serial = parts[0]
                    state = parts[1] if len(parts) > 1 else "unknown"
                    kind = "emulator" if serial.startswith("emulator-") else "device"
                    devices.append(
                        DiscoveredDevice(
                            platform="android",
                            name=serial,
                            identifier=serial,
                            state=state,
                            kind=kind,
                        )
                    )
            else:
                err = (proc.stderr or proc.stdout).strip()
        except Exception as exc:
            err = str(exc)

    emu_bin = find_android_tool("emulator")
    if emu_bin:
        try:
            proc = subprocess.run([emu_bin, "-list-avds"], capture_output=True, text=True, timeout=5)
            if proc.returncode == 0:
                for line in proc.stdout.splitlines():
                    avd = line.strip()
                    if avd:
                        devices.append(
                            DiscoveredDevice(
                                platform="android",
                                name=avd,
                                identifier=f"avd:{avd}",
                                state="available (AVD)",
                                kind="emulator",
                            )
                        )
        except Exception:
            pass

    return devices, err


def list_ios_simulators() -> tuple[list[DiscoveredDevice], str | None]:
    """List available iOS simulators on macOS via xcrun simctl."""
    if platform.system() != "Darwin":
        return [], "iOS simulators require macOS + Xcode"

    env = dict(os.environ)
    xcode_dev = Path("/Applications/Xcode.app/Contents/Developer")
    if xcode_dev.exists():
        sel = subprocess.run(["xcode-select", "-p"], capture_output=True, text=True)
        if sel.returncode != 0 or "CommandLineTools" in (sel.stdout or ""):
            env["DEVELOPER_DIR"] = str(xcode_dev)

    try:
        proc = subprocess.run(
            ["xcrun", "simctl", "list", "devices", "available", "--json"],
            capture_output=True,
            text=True,
            env=env,
            timeout=6,
        )
        if proc.returncode != 0:
            return [], (proc.stderr or proc.stdout).strip()
        data = json.loads(proc.stdout)
        devices: list[DiscoveredDevice] = []
        for runtime, devs in (data.get("devices") or {}).items():
            if "iOS" not in runtime:
                continue
            runtime_label = runtime.rsplit(".", 1)[-1].replace("-", " ")
            for d in devs:
                if not d.get("isAvailable", True):
                    continue
                devices.append(
                    DiscoveredDevice(
                        platform="ios",
                        name=f"{d.get('name')} ({runtime_label})",
                        identifier=str(d.get("udid")),
                        state=str(d.get("state", "Shutdown")),
                        kind="simulator",
                    )
                )
        return devices, None
    except Exception as exc:
        return [], str(exc)
