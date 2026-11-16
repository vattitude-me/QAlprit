"""Desktop launcher entry point (`QAlprit.exe` / `qalprit-desktop`)."""

from __future__ import annotations

import os
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any

from qalprit import __version__
from qalprit.cli import main as cli_main
from qalprit.paths import (
    get_legacy_workspace_dir,
    get_standard_workspace_dir,
    load_user_settings,
)
from qalprit.ui_server import create_ui_server

_DEFAULT_DEVICES_YAML = """profiles:
  android_emulator:
    platform: android
    target_type: simulator
    device_name: Android Emulator
    automation_name: UiAutomator2
    auto_accept_alerts: true
    capabilities:
      appWaitActivity: "*"

  android_device:
    platform: android
    target_type: device
    device_name: Android Device
    automation_name: UiAutomator2
    auto_accept_alerts: true

  ios_simulator:
    platform: ios
    target_type: simulator
    device_name: iPhone 17 Pro
    automation_name: XCUITest
    auto_accept_alerts: true

  ios_device:
    platform: ios
    target_type: device
    device_name: iPhone Device
    automation_name: XCUITest
    auto_accept_alerts: true
"""

_DEFAULT_ENVIRONMENTS_YAML = """environments:
  dev:
    android_package: com.example.app
    ios_bundle_id: com.example.app
    default_timeout: 12
    poll_interval: 0.25
    redact_screenshots: false
    secure_evidence: true
    save_page_source_on_failure: true
    variables:
      ENV_LABEL: DEV
  qa:
    android_package: com.example.app
    ios_bundle_id: com.example.app
    default_timeout: 12
    poll_interval: 0.25
    redact_screenshots: true
    secure_evidence: true
    save_page_source_on_failure: true
    variables:
      ENV_LABEL: QA
  pre-prod:
    android_package: com.example.app
    ios_bundle_id: com.example.app
    default_timeout: 12
    poll_interval: 0.25
    redact_screenshots: true
    secure_evidence: true
    save_page_source_on_failure: true
    variables:
      ENV_LABEL: PRE-PROD
  prod:
    android_package: com.example.app
    ios_bundle_id: com.example.app
    default_timeout: 15
    poll_interval: 0.25
    redact_screenshots: true
    secure_evidence: true
    save_page_source_on_failure: true
    variables:
      ENV_LABEL: PROD
"""

_DEFAULT_SCREENS_YAML = "{}\n"

_DEFAULT_ENV_FILE = """# Local QAlprit secrets and test credentials (never share or commit real secrets).
QA_USER=qa.demo@example.com
QA_PASS=DemoPassword!2026
"""


def resolve_default_workspace() -> Path:
    """Resolve active workspace: CWD, QALPRIT_WORKSPACE, saved settings, legacy ~/QAlpritWorkspace, or ~/Documents/QAlpritWorkspace."""
    cwd = Path.cwd().resolve()
    if (cwd / "config" / "devices.yaml").is_file() and (cwd / "config" / "environments.yaml").is_file():
        return cwd
    env_ws = os.environ.get("QALPRIT_WORKSPACE")
    if env_ws:
        return Path(env_ws).expanduser().resolve()
    saved_ws = str(load_user_settings().get("workspace_dir") or "").strip()
    if saved_ws:
        return Path(saved_ws).expanduser().resolve()
    legacy_ws = get_legacy_workspace_dir()
    if legacy_ws.is_dir():
        return legacy_ws
    return get_standard_workspace_dir()


def ensure_workspace(project_root: Path | None = None) -> Path:
    """Ensure a ready-to-use QAlprit workspace folder structure exists."""
    root = (project_root or resolve_default_workspace()).resolve()
    for sub in ("apps", "config", "screens", "flows", "specs/smoke", "results"):
        (root / sub).mkdir(parents=True, exist_ok=True)

    defaults = {
        root / "config" / "devices.yaml": _DEFAULT_DEVICES_YAML,
        root / "config" / "environments.yaml": _DEFAULT_ENVIRONMENTS_YAML,
        root / "screens" / "screens.yaml": _DEFAULT_SCREENS_YAML,
        root / ".env": _DEFAULT_ENV_FILE,
    }
    for file_path, content in defaults.items():
        if not file_path.exists():
            file_path.write_text(content, encoding="utf-8")
    return root


def _env_prefers_browser() -> bool:
    return os.environ.get("QALPRIT_BROWSER", "").strip().lower() in {"1", "true", "yes"}


def _launch_native_window(url: str, workspace: Path) -> bool:
    """Open the QAlprit Workbench in a native WebView app window if available."""
    if _env_prefers_browser():
        return False
    try:
        import webview  # type: ignore[import-not-found]
    except ImportError:
        return False

    try:
        settings = getattr(webview, "settings", None)
        if isinstance(settings, dict):
            settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True

        storage_dir = workspace / ".webview"
        storage_dir.mkdir(parents=True, exist_ok=True)
        webview.create_window(
            f"QAlprit Workbench v{__version__}",
            url,
            width=1360,
            height=880,
            min_size=(960, 640),
        )
        start_kwargs: dict[str, Any] = {
            "private_mode": False,
            "storage_path": str(storage_dir),
        }
        if sys.platform == "win32":
            start_kwargs["gui"] = "edgechromium"
        webview.start(**start_kwargs)
        return True
    except Exception as exc:
        print(f"[QAlprit] Native app window unavailable ({exc}); falling back to browser.")
        return False


def _open_browser_fallback(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:
        pass


def launch_workbench(
    project_root: Path | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
    native_window: bool = True,
) -> None:
    """Bootstrap workspace if needed, start the QAlprit Workbench, and open it."""
    workspace = ensure_workspace(project_root)
    server = create_ui_server(workspace, host=host, port=port)
    bound_host, bound_port = server.server_address[:2]
    url = f"http://{bound_host}:{bound_port}"

    print("=" * 64)
    print(f"QAlprit Workbench v{__version__} — Mobile QA. Bugs caught with proof.")
    print("=" * 64)
    print(f"Workspace : {workspace}")
    print(f"UI URL    : {url}")
    print("Close the app window (or press Ctrl+C in terminal) to quit.")
    print("-" * 64)

    if not open_browser or not native_window:
        if open_browser:
            _open_browser_fallback(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down QAlprit Workbench...")
        finally:
            server.server_close()
        return

    stop_event = threading.Event()
    server.timeout = 0.2

    def _serve_loop() -> None:
        while not stop_event.is_set():
            try:
                server.handle_request()
            except OSError:
                if stop_event.is_set():
                    break
                raise

    server_thread = threading.Thread(target=_serve_loop, daemon=True)
    server_thread.start()
    try:
        opened_native = _launch_native_window(url, workspace)
        if not opened_native:
            _open_browser_fallback(url)
            while server_thread.is_alive():
                server_thread.join(timeout=0.5)
    except KeyboardInterrupt:
        print("\nShutting down QAlprit Workbench...")
    finally:
        stop_event.set()
        server_thread.join(timeout=2.0)
        server.server_close()


def check_desktop_webview_runtime() -> str:
    """Verify native WebView runtime modules are importable without creating a window."""
    import webview  # type: ignore[import-not-found]

    if sys.platform == "darwin":
        import Cocoa  # type: ignore[import-not-found]  # noqa: F401
        import WebKit  # type: ignore[import-not-found]  # noqa: F401
        import webview.platforms.cocoa  # type: ignore[import-not-found]  # noqa: F401

        return f"Desktop WebView OK (macOS Cocoa/WebKit, pywebview={getattr(webview, '__version__', 'installed')})"
    if sys.platform == "win32":
        import webview.platforms.edgechromium  # type: ignore[import-not-found]  # noqa: F401

        return f"Desktop WebView OK (Windows EdgeChromium, pywebview={getattr(webview, '__version__', 'installed')})"
    return f"Desktop WebView OK ({sys.platform})"


def main(argv: list[str] | None = None) -> None:
    """Entry point for desktop bundles (`QAlprit.exe` / `QAlprit.app`) and `qalprit-desktop`."""
    raw_args = sys.argv[1:] if argv is None else list(argv)
    # macOS LaunchServices passes -psn_0_... when double-clicking a .app bundle.
    args = [arg for arg in raw_args if not arg.startswith("-psn_")]

    if sys.stdout is None or sys.stderr is None:
        log_dir = ensure_workspace()
        log_file = (log_dir / "desktop.log").open("a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = log_file
        if sys.stderr is None:
            sys.stderr = log_file

    if args == ["--check-desktop"]:
        print(check_desktop_webview_runtime())
        return

    if args == ["--browser"]:
        launch_workbench(native_window=False)
        return

    if args:
        cli_main(args=args, prog_name="qalprit")
        return
    launch_workbench()


if __name__ == "__main__":
    main()
