"""Manage QAlprit primary Virtual Android Emulator & macOS iOS Simulator lifecycle."""

from __future__ import annotations

import os
import platform
import re
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from qalprit.appium_manager import build_appium_env
from qalprit.devices import list_android_devices, list_ios_simulators
from qalprit.doctor import (
    find_android_sdk,
    find_android_tool,
    find_cmdline_tool,
    find_sdk_for_avd,
    find_system_android_sdk,
    prepare_writable_android_sdk,
    win_subprocess_kwargs,
)
from qalprit.paths import (
    get_android_user_home,
    get_qalprit_avd_home,
    get_qalprit_data_dir,
    get_user_android_sdk_dir,
)

DEFAULT_AVD_NAME = "QAlprit_Pixel"
DEFAULT_DEVICE_PROFILE = "pixel_6"
_PCT_RE = re.compile(r"(\d{1,3})%")


def system_image_package() -> str:
    """Return the host-architecture Android 34 Google APIs system image package."""
    mach = platform.machine().lower()
    arch = "arm64-v8a" if mach in {"arm64", "aarch64"} else "x86_64"
    return f"system-images;android-34;google_apis;{arch}"


def _list_avds(emu_bin: str | None) -> list[str]:
    if not emu_bin:
        return []
    try:
        emu_env = dict(os.environ)
        emu_env.setdefault("ANDROID_AVD_HOME", str(get_qalprit_avd_home()))
        emu_env.setdefault("ANDROID_USER_HOME", str(get_android_user_home()))
        proc = subprocess.run(
            [emu_bin, "-list-avds"],
            capture_output=True,
            text=True,
            timeout=10,
            env=emu_env,
            **win_subprocess_kwargs(),
        )
        if proc.returncode != 0:
            return []
        return [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    except Exception:
        return []


def _has_system_image(sdk: Path | None) -> bool:
    if sdk is None:
        return False
    sys_img_root = sdk / "system-images"
    if not sys_img_root.is_dir():
        return False
    return any(sys_img_root.rglob("source.properties"))


def get_emulator_status() -> dict[str, Any]:
    """Inspect Android Emulator and macOS iOS Simulator readiness cleanly."""
    sdk = find_sdk_for_avd(DEFAULT_AVD_NAME) or find_android_sdk()
    sdkmgr = find_android_tool("sdkmanager")
    avdmgr = find_android_tool("avdmanager")
    emu_bin = find_android_tool("emulator")
    avds = _list_avds(emu_bin)
    android_devs, _ = list_android_devices()
    running_emus = [
        d.identifier
        for d in android_devs
        if d.kind == "emulator" and not d.identifier.startswith("avd:") and d.state == "device"
    ]

    ios_sims: list[dict[str, str]] = []
    ios_supported = platform.system() == "Darwin"
    if ios_supported:
        sims, _ = list_ios_simulators()
        ios_sims = [
            {"name": s.name, "identifier": s.identifier, "state": s.state}
            for s in sims
        ]

    return {
        "os": platform.system(),
        "sdk_dir": str(sdk) if sdk else "",
        "target_sdk_dir": str(get_user_android_sdk_dir()),
        "avd_dir": str(get_qalprit_avd_home()),
        "data_dir": str(get_qalprit_data_dir()),
        "system_sdk_dir": str(find_system_android_sdk() or ""),
        "sdkmanager_ready": sdkmgr is not None,
        "avdmanager_ready": avdmgr is not None,
        "emulator_installed": emu_bin is not None,
        "system_image_installed": _has_system_image(sdk),
        "system_image_package": system_image_package(),
        "default_avd": DEFAULT_AVD_NAME,
        "avds": avds,
        "running_android_emulators": running_emus,
        "ios_supported": ios_supported,
        "ios_simulators": ios_sims,
    }


def _win_quote_bat_arg(arg: str) -> str:
    """Quote an argument for Windows cmd.exe/.bat scripts so ';', '=', and spaces stay intact."""
    text = str(arg).replace('"', '\\"')
    if not text or any(ch in text for ch in ' \t"&|<>()^%;=,'):
        return f'"{text}"'
    return text


def _win_bat_command_line(cmd: list[str]) -> str:
    """Build a `cmd.exe /d /s /c "..."` string for invoking a .bat/.cmd script cleanly."""
    quoted = [_win_quote_bat_arg(cmd[0])]
    if not (quoted[0].startswith('"') and quoted[0].endswith('"')):
        quoted[0] = f'"{quoted[0]}"'
    quoted.extend(_win_quote_bat_arg(arg) for arg in cmd[1:])
    inner = " ".join(quoted)
    return f'cmd.exe /d /s /c "{inner}"'


def _stream_command(
    cmd: list[str],
    *,
    env: dict[str, str],
    stdin_text: str = "",
    on_line: Callable[[str], None] | None = None,
    cancel_event: threading.Event | None = None,
    proc_holder: list[subprocess.Popen[str]] | None = None,
) -> tuple[int, str]:
    """Run a subprocess streaming CR/LF lines cleanly with cancellation support."""
    popen_target: list[str] | str = list(cmd)
    if (
        platform.system() == "Windows"
        and cmd
        and str(cmd[0]).lower().endswith((".bat", ".cmd"))
    ):
        popen_target = _win_bat_command_line(list(cmd))

    proc = subprocess.Popen(
        popen_target,
        stdin=subprocess.PIPE if stdin_text else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
        **win_subprocess_kwargs(),
    )
    if proc_holder is not None:
        proc_holder.clear()
        proc_holder.append(proc)

    if stdin_text and proc.stdin is not None:
        try:
            proc.stdin.write(stdin_text)
            proc.stdin.flush()
            proc.stdin.close()
        except Exception:
            pass

    lines: list[str] = []
    buf: list[str] = []
    if proc.stdout is not None:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                proc.terminate()
                break
            ch = proc.stdout.read(1)
            if not ch:
                if proc.poll() is not None:
                    break
                continue
            if ch in {"\r", "\n"}:
                text = "".join(buf).strip()
                buf.clear()
                if text:
                    lines.append(text)
                    if on_line is not None:
                        on_line(text)
            else:
                buf.append(ch)
        rem = "".join(buf).strip()
        if rem:
            lines.append(rem)
            if on_line is not None:
                on_line(rem)

    rc = proc.wait()
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("Operation cancelled by user.")
    return rc, "\n".join(lines[-25:])


def setup_android_emulator_sync(
    avd_name: str = DEFAULT_AVD_NAME,
    *,
    if_missing: bool = False,
    on_progress: Callable[[str, int, str], None] | None = None,
    cancel_event: threading.Event | None = None,
    proc_holder: list[subprocess.Popen[str]] | None = None,
) -> dict[str, Any]:
    """Download Google Android Emulator + API 34 system image and create AVD."""
    def emit(phase: str, pct: int, log_line: str = "") -> None:
        if on_progress is not None:
            on_progress(phase, pct, log_line)

    if if_missing:
        current = get_emulator_status()
        if (
            current.get("emulator_installed")
            and current.get("system_image_installed")
            and avd_name in (current.get("avds") or [])
        ):
            emit(
                "Already Installed",
                100,
                f"Virtual Android phone '{avd_name}' is already installed — skipping download.",
            )
            return {
                "ok": True,
                "avd_name": avd_name,
                "sdk_dir": current.get("sdk_dir") or "",
                "system_image": current.get("system_image_package") or system_image_package(),
                "skipped": True,
            }

    target_sdk = get_user_android_sdk_dir()
    emit("Preparing SDK", 5, f"Preparing writable Android SDK at {target_sdk} (first run copies SDK tools)...")
    sdk = prepare_writable_android_sdk()
    env = build_appium_env(dict(os.environ))
    env["ANDROID_HOME"] = str(sdk)
    env["ANDROID_SDK_ROOT"] = str(sdk)
    avd_home = get_qalprit_avd_home(avd_name)
    android_user_home = get_android_user_home(avd_name)
    avd_home.mkdir(parents=True, exist_ok=True)
    android_user_home.mkdir(parents=True, exist_ok=True)
    env["ANDROID_AVD_HOME"] = str(avd_home)
    env["ANDROID_USER_HOME"] = str(android_user_home)

    sdkmgr = find_cmdline_tool("sdkmanager", sdk)
    avdmgr = find_cmdline_tool("avdmanager", sdk)
    if not sdkmgr or not avdmgr:
        raise RuntimeError(
            "Android cmdline-tools (sdkmanager / avdmanager) were not found. "
            "Reinstall QAlprit with the Android Runtime component enabled."
        )

    emit("SDK Tools Ready", 8, f"Writable SDK: {sdk}")
    pkg_img = system_image_package()
    sdk_root_arg = f"--sdk_root={sdk}"

    # 1. Accept SDK licenses
    emit("Accepting Licenses", 10, "Accepting Android SDK licenses...")
    _stream_command(
        [sdkmgr, sdk_root_arg, "--licenses"],
        env=env,
        stdin_text=("y\n" * 25),
        on_line=lambda ln: emit("Accepting Licenses", 12, ln),
        cancel_event=cancel_event,
        proc_holder=proc_holder,
    )

    # 2. Download emulator + system image
    emit(
        "Downloading Emulator & Android 34 Image",
        15,
        f"Installing emulator and {pkg_img} (~1.8 GB total)...",
    )

    def _on_dl_line(line: str) -> None:
        m = _PCT_RE.search(line)
        pct = 15
        if m:
            raw_pct = max(0, min(100, int(m.group(1))))
            pct = 15 + int(raw_pct * 0.70)
        emit("Downloading Emulator & Android 34 Image", pct, line)

    rc, tail = _stream_command(
        [sdkmgr, sdk_root_arg, "--install", "emulator", pkg_img],
        env=env,
        stdin_text=("y\n" * 10),
        on_line=_on_dl_line,
        cancel_event=cancel_event,
        proc_holder=proc_holder,
    )
    if rc != 0:
        if "javax/xml/bind" in tail or "javax.xml.bind" in tail:
            raise RuntimeError(
                f"Legacy Android sdkmanager ({sdkmgr}) is incompatible with Java 17 (missing JAXB). "
                "Update Android SDK Command-line Tools (latest) or re-run QAlprit installer with "
                "Force reinstall runtime checked. Raw error: " + tail
            )
        raise RuntimeError(f"sdkmanager install failed (exit {rc}): {tail}")

    expected_img_dir = sdk / Path(*pkg_img.split(";"))
    if not (expected_img_dir / "source.properties").is_file() and not _has_system_image(sdk):
        raise RuntimeError(
            f"Android system image '{pkg_img}' was not installed into {sdk}. "
            f"Last output: {tail}"
        )

    # 3. Create Pixel AVD
    emit("Creating Virtual Phone", 88, f"Creating AVD '{avd_name}'...")
    cmd_avd = [
        avdmgr,
        "create",
        "avd",
        "--force",
        "-n",
        avd_name,
        "-k",
        pkg_img,
        "--device",
        DEFAULT_DEVICE_PROFILE,
    ]
    rc_avd, tail_avd = _stream_command(
        cmd_avd,
        env=env,
        stdin_text="no\n",
        on_line=lambda ln: emit("Creating Virtual Phone", 92, ln),
        cancel_event=cancel_event,
        proc_holder=proc_holder,
    )
    if rc_avd != 0:
        # Retry without --device profile if device definition name differs in newer cmdline-tools
        cmd_avd_fallback = [avdmgr, "create", "avd", "--force", "-n", avd_name, "-k", pkg_img]
        rc_fb, tail_fb = _stream_command(
            cmd_avd_fallback,
            env=env,
            stdin_text="no\n",
            on_line=lambda ln: emit("Creating Virtual Phone", 95, ln),
            cancel_event=cancel_event,
            proc_holder=proc_holder,
        )
        if rc_fb != 0:
            raise RuntimeError(f"avdmanager create avd failed: {tail_fb or tail_avd}")

    emit("Ready", 100, f"Virtual Android phone '{avd_name}' is installed and ready to boot.")
    return {
        "ok": True,
        "avd_name": avd_name,
        "sdk_dir": str(sdk),
        "system_image": pkg_img,
    }


class EmulatorJobManager:
    """Thread-safe controller for installing, booting, and stopping emulators."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._active_procs: list[subprocess.Popen[str]] = []
        self._emulator_proc: subprocess.Popen[Any] | None = None
        self._started_monotonic: float | None = None
        self._state: dict[str, Any] = {
            "running": False,
            "action": "",
            "status": "idle",
            "phase": "",
            "progress_pct": 0,
            "elapsed_sec": 0.0,
            "logs": [],
            "error": "",
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            elapsed = self._state["elapsed_sec"]
            if self._state["running"] and self._started_monotonic is not None:
                elapsed = round(time.monotonic() - self._started_monotonic, 1)
            snap = {
                "running": self._state["running"],
                "action": self._state["action"],
                "status": self._state["status"],
                "phase": self._state["phase"],
                "progress_pct": self._state["progress_pct"],
                "elapsed_sec": elapsed,
                "logs": list(self._state["logs"][-80:]),
                "error": self._state["error"],
            }
        snap["env_status"] = get_emulator_status()
        return snap

    def _update(self, phase: str, pct: int, line: str = "") -> None:
        with self._lock:
            self._state["phase"] = phase
            self._state["progress_pct"] = max(0, min(100, pct))
            if line:
                self._state["logs"].append(line)

    def _begin(self, action: str, phase: str, pct: int, log_line: str) -> None:
        with self._lock:
            if self._state["running"]:
                raise RuntimeError("An emulator operation is already in progress.")
            self._cancel_event.clear()
            self._started_monotonic = time.monotonic()
            self._state = {
                "running": True,
                "action": action,
                "status": "running",
                "phase": phase,
                "progress_pct": pct,
                "elapsed_sec": 0.0,
                "logs": [log_line],
                "error": "",
            }

    def _finish(self, status: str, phase: str, pct: int, error: str = "") -> None:
        with self._lock:
            elapsed = (
                round(time.monotonic() - self._started_monotonic, 1)
                if self._started_monotonic is not None
                else 0.0
            )
            self._state["running"] = False
            self._state["status"] = status
            self._state["phase"] = phase
            self._state["progress_pct"] = pct
            self._state["elapsed_sec"] = elapsed
            self._state["error"] = error
            if error:
                self._state["logs"].append(error)

    def start_setup(self, avd_name: str = DEFAULT_AVD_NAME) -> dict[str, Any]:
        """Start background download & creation of the QAlprit Android Emulator."""
        clean_avd = (avd_name or DEFAULT_AVD_NAME).strip()
        self._begin("setup", "Preparing SDK", 4, f"Starting emulator setup for '{clean_avd}'...")

        def _worker() -> None:
            try:
                setup_android_emulator_sync(
                    avd_name=clean_avd,
                    on_progress=self._update,
                    cancel_event=self._cancel_event,
                    proc_holder=self._active_procs,
                )
                self._finish("completed", "Emulator Installed", 100)
            except InterruptedError:
                self._finish("stopped", "Stopped", self._state["progress_pct"], "Emulator setup stopped by user.")
            except Exception as exc:
                self._finish("failed", "Setup Failed", self._state["progress_pct"], str(exc))

        threading.Thread(target=_worker, daemon=True).start()
        return {"ok": True, "action": "setup", "avd_name": clean_avd}

    def start_boot_android(self, avd_name: str = DEFAULT_AVD_NAME) -> dict[str, Any]:
        """Boot the Android Emulator asynchronously and poll adb until OS boot finishes."""
        emu_bin = find_android_tool("emulator")
        adb_bin = find_android_tool("adb")
        if not emu_bin or not adb_bin:
            raise RuntimeError("Android Emulator is not installed yet. Click Setup Virtual Android Emulator first.")

        avds = _list_avds(emu_bin)
        if not avds:
            raise RuntimeError("No Android Virtual Devices found. Click Setup Virtual Android Emulator first.")
        target_avd = avd_name if avd_name in avds else avds[0]

        devs, _ = list_android_devices()
        already = [d.identifier for d in devs if d.kind == "emulator" and d.state == "device"]
        if already:
            return {"ok": True, "already_running": True, "serial": already[0]}

        self._begin("boot", "Launching Emulator", 10, f"Starting virtual device '{target_avd}'...")

        def _worker() -> None:
            env = build_appium_env(dict(os.environ))
            sdk = find_sdk_for_avd(target_avd)
            launch_emu = emu_bin
            if sdk is not None:
                env["ANDROID_HOME"] = str(sdk)
                env["ANDROID_SDK_ROOT"] = str(sdk)
                for exe_name in (("emulator.exe", "emulator") if platform.system() == "Windows" else ("emulator",)):
                    cand = sdk / "emulator" / exe_name
                    if cand.exists():
                        launch_emu = str(cand)
                        break
            env.setdefault("ANDROID_AVD_HOME", str(get_qalprit_avd_home(target_avd)))
            env.setdefault("ANDROID_USER_HOME", str(get_android_user_home(target_avd)))
            log_path = get_qalprit_data_dir() / "logs" / "emulator-boot.log"
            try:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                cmd = [
                    launch_emu,
                    "-avd",
                    target_avd,
                    "-no-boot-anim",
                    "-gpu",
                    "auto",
                    "-no-snapshot-save",
                ]
                creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                proc_kwargs: dict[str, Any] = {"creationflags": creation_flags} if (platform.system() == "Windows" and creation_flags) else {}
                log_fp = log_path.open("w", encoding="utf-8", errors="replace")
                try:
                    proc = subprocess.Popen(
                        cmd,
                        stdout=log_fp,
                        stderr=subprocess.STDOUT,
                        env=env,
                        **proc_kwargs,
                    )
                finally:
                    log_fp.close()
                with self._lock:
                    self._emulator_proc = proc

                deadline = time.monotonic() + 180.0
                pct = 15
                while time.monotonic() < deadline:
                    if self._cancel_event.is_set():
                        proc.terminate()
                        self._finish("stopped", "Stopped", pct, "Emulator boot stopped by user.")
                        return
                    if proc.poll() is not None:
                        tail = ""
                        try:
                            tail = "\n".join(
                                log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]
                            ).strip()
                        except OSError:
                            tail = ""
                        msg = f"Android emulator exited early with code {proc.returncode}."
                        if tail:
                            msg = f"{msg} Output: {tail}"
                        if "Broken AVD system path" in msg:
                            msg += (
                                " Hint: Uncheck 'Use existing system Android SDK' in Step 1 "
                                "and click Setup Virtual Android Emulator to align the system image."
                            )
                        raise RuntimeError(msg)

                    cur_devs, _ = list_android_devices()
                    online = [d.identifier for d in cur_devs if d.kind == "emulator" and d.state == "device"]
                    if online:
                        serial = online[0]
                        self._update("Waiting for Android OS", max(pct, 60), f"Connected {serial}; waiting for Android home screen...")
                        boot_chk = subprocess.run(
                            [adb_bin, "-s", serial, "shell", "getprop", "sys.boot_completed"],
                            capture_output=True,
                            text=True,
                            timeout=6,
                            env=env,
                            **win_subprocess_kwargs(),
                        )
                        if boot_chk.returncode == 0 and boot_chk.stdout.strip() == "1":
                            self._update("Booted", 100, f"Android Emulator '{target_avd}' ({serial}) is booted and ready!")
                            self._finish("completed", "Booted & Ready", 100)
                            return
                    pct = min(92, pct + 4)
                    self._update("Booting Virtual Phone", pct)
                    time.sleep(2.0)

                raise TimeoutError("Timed out waiting for Android Emulator to finish booting (180s).")
            except Exception as exc:
                self._finish("failed", "Boot Failed", self._state["progress_pct"], str(exc))

        threading.Thread(target=_worker, daemon=True).start()
        return {"ok": True, "action": "boot", "avd_name": target_avd}

    def boot_ios_simulator(self, udid: str = "") -> dict[str, Any]:
        """Boot an Apple iOS Simulator on macOS cleanly."""
        if platform.system() != "Darwin":
            raise RuntimeError("iOS Simulator requires macOS + Xcode.")
        sims, err = list_ios_simulators()
        if not sims:
            raise RuntimeError(err or "No iOS Simulators available. Install Xcode & iOS Simulator runtime.")
        target = next((s for s in sims if s.identifier == udid), sims[0])
        if target.state != "Booted":
            proc = subprocess.run(["xcrun", "simctl", "boot", target.identifier], capture_output=True, text=True, timeout=30)
            if proc.returncode != 0 and "Unable to boot device in current state: Booted" not in (proc.stderr or ""):
                raise RuntimeError((proc.stderr or proc.stdout).strip() or "Failed to boot iOS Simulator.")
        subprocess.run(["open", "-a", "Simulator"], capture_output=True, text=True, timeout=10)
        return {"ok": True, "name": target.name, "identifier": target.identifier}

    def stop(self) -> dict[str, Any]:
        """Stop active emulator setup/boot job and/or kill running Android emulators."""
        with self._lock:
            self._cancel_event.set()
            procs = list(self._active_procs)
            emu_proc = self._emulator_proc
            was_running = self._state["running"]
            if was_running:
                self._state["status"] = "stopping"
                self._state["phase"] = "Stopping"
                self._state["logs"].append("Stop requested by user...")

        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        if emu_proc is not None and emu_proc.poll() is None:
            try:
                emu_proc.terminate()
            except Exception:
                pass

        adb_bin = find_android_tool("adb")
        killed: list[str] = []
        if adb_bin:
            devs, _ = list_android_devices()
            for d in devs:
                if d.kind == "emulator" and not d.identifier.startswith("avd:"):
                    subprocess.run(
                        [adb_bin, "-s", d.identifier, "emu", "kill"],
                        capture_output=True,
                        text=True,
                        timeout=8,
                        **win_subprocess_kwargs(),
                    )
                    killed.append(d.identifier)
        return {"ok": True, "stopping": was_running, "killed_emulators": killed}
