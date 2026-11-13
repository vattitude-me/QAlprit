"""OS-aware environment diagnostics for qalprit doctor."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass
class CheckItem:
    name: str
    status: Literal["ok", "warn", "fail", "skip"]
    detail: str
    remediation: str = ""


def find_android_sdk() -> Path | None:
    """Locate Android SDK directory across macOS, Windows, and Linux."""
    candidates: list[Path] = []
    for env_key in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        val = os.environ.get(env_key)
        if val:
            candidates.append(Path(val))

    home = Path.home()
    system = platform.system()
    if system == "Darwin":
        candidates.extend(
            [
                Path("/opt/homebrew/share/android-commandlinetools"),
                Path("/usr/local/share/android-commandlinetools"),
                home / "Library" / "Android" / "sdk",
            ]
        )
    elif system == "Windows":
        local_app = os.environ.get("LOCALAPPDATA")
        if local_app:
            candidates.append(Path(local_app) / "Android" / "Sdk")
        candidates.append(home / "AppData" / "Local" / "Android" / "Sdk")
    else:
        candidates.extend([home / "Android" / "Sdk", Path("/opt/android-sdk")])

    for path in candidates:
        if path.exists():
            return path
    return None


def find_android_tool(name: str) -> str | None:
    """Resolve an Android SDK binary (adb, emulator, avdmanager)."""
    direct = shutil.which(name)
    if direct:
        return direct
    sdk = find_android_sdk()
    if not sdk:
        return None
    suffix = ".exe" if platform.system() == "Windows" else ""
    rel_candidates = [
        sdk / "platform-tools" / f"{name}{suffix}",
        sdk / "emulator" / f"{name}{suffix}",
        sdk / "cmdline-tools" / "latest" / "bin" / f"{name}{suffix}",
        sdk / "tools" / "bin" / f"{name}{suffix}",
    ]
    for cand in rel_candidates:
        if cand.exists():
            return str(cand)
    return None


def find_java_bin() -> str | None:
    """Locate java binary, checking PATH, JAVA_HOME, and Homebrew OpenJDK on macOS."""
    direct = shutil.which("java")
    # On macOS, /usr/bin/java can be a stub when no JDK is linked
    if direct and platform.system() == "Darwin" and direct == "/usr/bin/java":
        probe = subprocess.run([direct, "-version"], capture_output=True, text=True)
        if probe.returncode == 0:
            return direct
    elif direct:
        return direct

    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        cand = Path(java_home) / "bin" / ("java.exe" if platform.system() == "Windows" else "java")
        if cand.exists():
            return str(cand)

    if platform.system() == "Darwin":
        for cand in (
            Path("/opt/homebrew/opt/openjdk@17/bin/java"),
            Path("/opt/homebrew/opt/openjdk@21/bin/java"),
            Path("/opt/homebrew/opt/openjdk/bin/java"),
        ):
            if cand.exists():
                return str(cand)
    return None


def _run_quick(cmd: list[str], env: dict[str, str] | None = None, timeout: float = 5.0) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
        )
        output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        return proc.returncode, output.strip()
    except Exception as exc:
        return 1, str(exc)


def _macos_xcode_env() -> dict[str, str]:
    env = dict(os.environ)
    xcode_dev = Path("/Applications/Xcode.app/Contents/Developer")
    if xcode_dev.exists():
        # Prefer full Xcode if xcode-select points to CommandLineTools
        rc, sel = _run_quick(["xcode-select", "-p"])
        if rc != 0 or "CommandLineTools" in sel:
            env["DEVELOPER_DIR"] = str(xcode_dev)
    return env


def run_doctor(python_min: tuple[int, int] = (3, 10)) -> list[CheckItem]:
    """Run OS-aware prerequisite checks for qalprit."""
    checks: list[CheckItem] = []
    system = platform.system()

    # 1. Python
    import sys

    py_ver = sys.version_info
    if (py_ver.major, py_ver.minor) >= python_min:
        checks.append(CheckItem("Python", "ok", f"{py_ver.major}.{py_ver.minor}.{py_ver.micro}"))
    else:
        checks.append(
            CheckItem(
                "Python",
                "fail",
                f"{py_ver.major}.{py_ver.minor}.{py_ver.micro}",
                f"Upgrade to Python {python_min[0]}.{python_min[1]}+",
            )
        )

    # 2. Node.js
    node_bin = shutil.which("node")
    if node_bin:
        rc, out = _run_quick([node_bin, "--version"])
        checks.append(CheckItem("Node.js", "ok" if rc == 0 else "warn", out or node_bin))
    else:
        checks.append(
            CheckItem("Node.js", "fail", "Not found", "Install Node.js LTS to run Appium 2")
        )

    # 3. Appium 2 + drivers
    appium_bin = shutil.which("appium")
    if appium_bin:
        rc, ver_out = _run_quick([appium_bin, "--version"])
        ver_str = ver_out.splitlines()[0] if ver_out else "unknown"
        major = 0
        m = re.match(r"(\d+)\.", ver_str.strip())
        if m:
            major = int(m.group(1))
        status: Literal["ok", "warn", "fail"] = "ok" if (rc == 0 and major >= 2) else "warn"
        checks.append(
            CheckItem(
                "Appium",
                status,
                f"v{ver_str} ({appium_bin})",
                "" if major >= 2 else "Upgrade to Appium 2: npm install -g appium",
            )
        )

        # Installed drivers
        rc_d, drv_out = _run_quick([appium_bin, "driver", "list", "--installed", "--json"])
        installed_drivers: set[str] = set()
        if rc_d == 0 and drv_out:
            try:
                parsed = json.loads(drv_out)
                if isinstance(parsed, dict):
                    installed_drivers = set(parsed.keys())
            except Exception:
                pass
        if "uiautomator2" in installed_drivers:
            checks.append(CheckItem("Appium driver: uiautomator2", "ok", "Installed"))
        else:
            checks.append(
                CheckItem(
                    "Appium driver: uiautomator2",
                    "fail",
                    "Not installed",
                    "Run: appium driver install uiautomator2",
                )
            )
        if system == "Darwin":
            if "xcuitest" in installed_drivers:
                checks.append(CheckItem("Appium driver: xcuitest", "ok", "Installed"))
            else:
                checks.append(
                    CheckItem(
                        "Appium driver: xcuitest",
                        "warn",
                        "Not installed",
                        "Run: appium driver install xcuitest",
                    )
                )
        else:
            checks.append(
                CheckItem("Appium driver: xcuitest", "skip", f"iOS unsupported on {system}")
            )
    else:
        checks.append(
            CheckItem(
                "Appium",
                "fail",
                "Not found",
                "Install Appium 2: npm install -g appium && appium driver install uiautomator2",
            )
        )

    # 4. Java (JDK 17+)
    java_bin = find_java_bin()
    if java_bin:
        rc, j_out = _run_quick([java_bin, "-version"])
        first_line = j_out.splitlines()[0] if j_out else java_bin
        m = re.search(r'"(\d+)', first_line)
        j_major = int(m.group(1)) if m else 0
        if rc == 0 and j_major >= 17:
            checks.append(CheckItem("Java (JDK)", "ok", f"{first_line} ({java_bin})"))
        else:
            checks.append(
                CheckItem(
                    "Java (JDK)",
                    "warn",
                    f"{first_line} ({java_bin})",
                    "JDK 17+ recommended for Android tooling",
                )
            )
    else:
        checks.append(
            CheckItem("Java (JDK)", "fail", "Not found", "Install JDK 17+ and set JAVA_HOME")
        )

    # 5. Android SDK & adb / emulator
    sdk = find_android_sdk()
    if sdk:
        checks.append(CheckItem("Android SDK", "ok", str(sdk)))
    else:
        checks.append(
            CheckItem(
                "Android SDK",
                "fail",
                "Not found",
                "Install Android SDK command-line tools and set ANDROID_HOME",
            )
        )

    adb_bin = find_android_tool("adb")
    if adb_bin:
        rc, adb_out = _run_quick([adb_bin, "version"])
        ver_line = adb_out.splitlines()[0] if adb_out else adb_bin
        checks.append(CheckItem("Android adb", "ok", f"{ver_line} ({adb_bin})"))
    else:
        checks.append(
            CheckItem("Android adb", "fail", "Not found", "Install Android platform-tools")
        )

    emu_bin = find_android_tool("emulator")
    if emu_bin:
        rc, avd_out = _run_quick([emu_bin, "-list-avds"])
        avds = [line.strip() for line in avd_out.splitlines() if line.strip()] if rc == 0 else []
        detail = f"{len(avds)} AVD(s): {', '.join(avds)}" if avds else "No AVDs found"
        checks.append(CheckItem("Android emulator", "ok" if avds else "warn", detail))
    else:
        checks.append(
            CheckItem("Android emulator", "warn", "Not found", "Install Android emulator package")
        )

    # 6. Xcode & iOS Simulator (macOS only)
    if system == "Darwin":
        xcode_app = Path("/Applications/Xcode.app")
        rc_sel, sel_out = _run_quick(["xcode-select", "-p"])
        if xcode_app.exists():
            xc_env = _macos_xcode_env()
            rc_sim, sim_out = _run_quick(["xcrun", "simctl", "list", "devices", "available"], env=xc_env)
            if rc_sim == 0:
                sim_count = sum(1 for ln in sim_out.splitlines() if "(" in ln and ")" in ln and "--" not in ln)
                note = "" if "Xcode.app" in sel_out else " (using DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer)"
                checks.append(
                    CheckItem("Xcode / iOS Simulator", "ok", f"{sim_count} available simulator(s){note}")
                )
            elif "license" in sim_out.lower():
                checks.append(
                    CheckItem(
                        "Xcode / iOS Simulator",
                        "fail",
                        "Xcode license agreement not accepted",
                        "Run: sudo xcodebuild -license accept && sudo xcode-select -s /Applications/Xcode.app/Contents/Developer",
                    )
                )
            else:
                checks.append(
                    CheckItem(
                        "Xcode / iOS Simulator",
                        "warn",
                        sim_out.splitlines()[0] if sim_out else "simctl unavailable",
                        "Run: sudo xcode-select -s /Applications/Xcode.app/Contents/Developer",
                    )
                )
        else:
            checks.append(
                CheckItem(
                    "Xcode / iOS Simulator",
                    "fail",
                    "Xcode.app not installed",
                    "Install Xcode from the App Store to run iOS tests",
                )
            )
    else:
        checks.append(
            CheckItem("Xcode / iOS Simulator", "skip", f"Skipped on {system} (iOS requires macOS)")
        )

    return checks
