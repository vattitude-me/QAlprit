"""Desktop launcher entry point (`QAlprit.exe` / `qalprit-desktop`)."""

from __future__ import annotations

import os
import sys
import webbrowser
from pathlib import Path

from qalprit import __version__
from qalprit.cli import main as cli_main
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
"""

_DEFAULT_ENVIRONMENTS_YAML = """environments:
  qa:
    android_package: com.example.app
    ios_bundle_id: com.example.app
    default_timeout: 12
    poll_interval: 0.25
    secure_evidence: true
    save_page_source_on_failure: true
    variables:
      ENV_LABEL: QA
"""

_DEFAULT_SCREENS_YAML = "{}\n"

_DEFAULT_ENV_FILE = """# Local QAlprit secrets and test credentials (never share or commit real secrets).
QA_USER=qa.demo@example.com
QA_PASS=DemoPassword!2026
"""


def resolve_default_workspace() -> Path:
    """Return current directory if configured, otherwise ~/QAlpritWorkspace."""
    cwd = Path.cwd().resolve()
    if (cwd / "config" / "devices.yaml").is_file() and (cwd / "config" / "environments.yaml").is_file():
        return cwd
    env_ws = os.environ.get("QALPRIT_WORKSPACE")
    if env_ws:
        return Path(env_ws).expanduser().resolve()
    return (Path.home() / "QAlpritWorkspace").resolve()


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


def launch_workbench(
    project_root: Path | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    """Bootstrap workspace if needed, start the QAlprit Workbench, and open it in browser."""
    workspace = ensure_workspace(project_root)
    server = create_ui_server(workspace, host=host, port=port)
    bound_host, bound_port = server.server_address[:2]
    url = f"http://{bound_host}:{bound_port}"

    print("=" * 64)
    print(f"QAlprit Workbench v{__version__} — Mobile QA. Bugs caught with proof.")
    print("=" * 64)
    print(f"Workspace : {workspace}")
    print(f"UI URL    : {url}")
    print("Keep this window open while using QAlprit. Press Ctrl+C to quit.")
    print("-" * 64)

    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down QAlprit Workbench...")
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> None:
    """Entry point for desktop bundles (`QAlprit.exe`) and `qalprit-desktop`."""
    args = sys.argv[1:] if argv is None else list(argv)
    if args:
        cli_main(args=args, prog_name="qalprit")
        return
    launch_workbench()


if __name__ == "__main__":
    main()
