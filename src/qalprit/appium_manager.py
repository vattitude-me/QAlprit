"""Managed Appium 2 server subprocess lifecycle and readiness checks."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import IO

from qalprit.doctor import find_android_sdk, find_java_bin


def find_free_port(preferred: int = 4723) -> int:
    """Return preferred port if available, otherwise allocate an ephemeral free port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def check_appium_status(appium_url: str, timeout: float = 2.0) -> bool:
    """Return True if Appium server /status reports ready."""
    url = appium_url.rstrip("/") + "/status"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return False
            payload = json.loads(resp.read().decode("utf-8"))
            value = payload.get("value") or {}
            return bool(value.get("ready", True))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return False


def build_appium_env(base_env: dict[str, str] | None = None) -> dict[str, str]:
    """Build subprocess environment with ANDROID_HOME and JAVA_HOME auto-detected."""
    env = dict(os.environ if base_env is None else base_env)
    extra_path_dirs: list[str] = []

    sdk = find_android_sdk()
    if sdk is not None:
        env.setdefault("ANDROID_HOME", str(sdk))
        env.setdefault("ANDROID_SDK_ROOT", str(sdk))
        for sub in ("platform-tools", "emulator"):
            sub_dir = sdk / sub
            if sub_dir.exists():
                extra_path_dirs.append(str(sub_dir))

    java_bin = find_java_bin()
    if java_bin is not None:
        java_path = Path(java_bin).resolve()
        # bin/java -> JDK Home
        jdk_home = java_path.parent.parent
        if jdk_home.exists():
            env.setdefault("JAVA_HOME", str(jdk_home))
            extra_path_dirs.append(str(jdk_home / "bin"))

    if extra_path_dirs:
        cur_path = env.get("PATH", "")
        env["PATH"] = os.pathsep.join(extra_path_dirs + ([cur_path] if cur_path else []))
    return env


def resolve_appium_command() -> list[str]:
    """Locate Appium 2 binary across macOS and Windows."""
    appium_bin = shutil.which("appium")
    if appium_bin:
        return [appium_bin]
    npx_bin = shutil.which("npx")
    if npx_bin:
        return [npx_bin, "--no-install", "appium"]
    raise FileNotFoundError(
        "Appium 2 executable not found on PATH. Install with `npm install -g appium` "
        "and run `qalprit doctor`."
    )


class AppiumManager:
    """Context manager that starts/stops local Appium 2 or validates --appium-url."""

    def __init__(
        self,
        log_path: Path,
        appium_url: str | None = None,
        startup_timeout: float = 30.0,
        host: str = "127.0.0.1",
        port: int | None = None,
    ) -> None:
        self.log_path = log_path
        self.external_url = appium_url
        self.startup_timeout = startup_timeout
        self.host = host
        self.port = port
        self._proc: subprocess.Popen[bytes] | None = None
        self._log_file: IO[bytes] | None = None
        self.url: str = appium_url or ""

    def __enter__(self) -> AppiumManager:
        if self.external_url:
            if not check_appium_status(self.external_url, timeout=3.0):
                raise RuntimeError(
                    f"External Appium server at {self.external_url} is not reachable or not ready."
                )
            self.url = self.external_url.rstrip("/")
            return self

        chosen_port = self.port or find_free_port(4723)
        self.url = f"http://{self.host}:{chosen_port}"
        cmd = resolve_appium_command() + [
            "server",
            "--address",
            self.host,
            "--port",
            str(chosen_port),
            "--log-timestamp",
        ]

        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_file = self.log_path.open("wb")
        env = build_appium_env()
        self._proc = subprocess.Popen(
            cmd,
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
            env=env,
        )

        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                self._close_log()
                tail = ""
                if self.log_path.exists():
                    tail = self.log_path.read_text(encoding="utf-8", errors="replace")[-1500:]
                raise RuntimeError(
                    f"Appium server exited unexpectedly with code {self._proc.returncode}.\n{tail}"
                )
            if check_appium_status(self.url, timeout=1.0):
                return self
            time.sleep(0.4)

        self.__exit__(None, None, None)
        raise TimeoutError(
            f"Appium server did not become ready within {self.startup_timeout}s. "
            f"See {self.log_path}"
        )

    def _close_log(self) -> None:
        if self._log_file is not None:
            try:
                self._log_file.flush()
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._proc is not None:
            if self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=8.0)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    self._proc.wait(timeout=5.0)
            self._proc = None
        self._close_log()
