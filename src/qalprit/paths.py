"""OS-standard directory resolution and user storage preferences for QAlprit."""

from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from typing import Any

DEFAULT_AVD_NAME = "QAlprit_Pixel"


def get_os_app_data_root() -> Path:
    """Return the OS-standard per-user application data directory for QAlprit."""
    env_dir = os.environ.get("QALPRIT_DATA_DIR", "").strip()
    if env_dir:
        return Path(env_dir).expanduser().resolve()

    home = Path.home()
    system = platform.system()
    if system == "Windows":
        local_app = os.environ.get("LOCALAPPDATA", "").strip()
        if local_app:
            try:
                real_home = Path(os.path.expanduser("~")).resolve()
                if home.resolve() == real_home:
                    return (Path(local_app) / "QAlprit").resolve()
            except OSError:
                return (Path(local_app) / "QAlprit").resolve()
        return (home / "AppData" / "Local" / "QAlprit").resolve()
    if system == "Darwin":
        return (home / "Library" / "Application Support" / "QAlprit").resolve()
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    if xdg:
        return (Path(xdg).expanduser() / "qalprit").resolve()
    return (home / ".local" / "share" / "qalprit").resolve()


def get_settings_path() -> Path:
    """Return the path to QAlprit's persistent JSON user settings."""
    override = os.environ.get("QALPRIT_SETTINGS_PATH", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (get_os_app_data_root() / "settings.json").resolve()


def load_user_settings() -> dict[str, Any]:
    """Load persisted user storage and SDK preferences cleanly."""
    path = get_settings_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_user_settings(updates: dict[str, Any]) -> dict[str, Any]:
    """Merge and persist user storage and SDK preferences into settings.json."""
    current = load_user_settings()
    for key, val in updates.items():
        if val is None or (isinstance(val, str) and not val.strip()):
            current.pop(key, None)
        else:
            current[key] = val.strip() if isinstance(val, str) else val
    path = get_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    return current


def get_qalprit_data_dir() -> Path:
    """Return the active root for QAlprit-managed SDK, AVDs, and logs."""
    env_dir = os.environ.get("QALPRIT_DATA_DIR", "").strip()
    if env_dir:
        return Path(env_dir).expanduser().resolve()
    settings = load_user_settings()
    saved = str(settings.get("data_dir") or "").strip()
    if saved:
        return Path(saved).expanduser().resolve()
    return get_os_app_data_root()


def get_standard_workspace_dir() -> Path:
    """Return the OS-standard Documents workspace location (`~/Documents/QAlpritWorkspace`)."""
    return (Path.home() / "Documents" / "QAlpritWorkspace").resolve()


def get_legacy_workspace_dir() -> Path:
    """Return the pre-v0.1.17 home root workspace path (`~/QAlpritWorkspace`)."""
    return (Path.home() / "QAlpritWorkspace").resolve()


def get_legacy_dot_qalprit_dir() -> Path:
    """Return the pre-v0.1.17 `~/.qalprit` path."""
    return (Path.home() / ".qalprit").resolve()


def _dir_has_sdk_bits(path: Path) -> bool:
    if not path.is_dir():
        return False
    return any(
        (path / sub).is_dir()
        for sub in ("emulator", "system-images", "platform-tools", "cmdline-tools")
    )


def _dir_has_real_sdk_payload(path: Path) -> bool:
    if not path.is_dir():
        return False
    return (path / "emulator").is_dir() or (path / "system-images").is_dir()


def get_user_android_sdk_dir() -> Path:
    """Return QAlprit-managed writable Android SDK path (OS AppData/Application Support, or populated legacy ~/.qalprit)."""
    env_sdk = os.environ.get("QALPRIT_SDK_DIR", "").strip()
    if env_sdk:
        return Path(env_sdk).expanduser().resolve()

    standard = (get_qalprit_data_dir() / "android-sdk").resolve()
    if _dir_has_sdk_bits(standard):
        return standard

    legacy = (get_legacy_dot_qalprit_dir() / "android-sdk").resolve()
    if _dir_has_real_sdk_payload(legacy):
        return legacy

    return standard


def _dir_has_avds(path: Path) -> bool:
    if not path.is_dir():
        return False
    try:
        return any(p.is_dir() and p.name.endswith(".avd") for p in path.iterdir())
    except OSError:
        return False


def get_qalprit_avd_home(avd_name: str = DEFAULT_AVD_NAME) -> Path:
    """Return writable AVD directory inside QAlprit app data, preserving any existing AVD."""
    env_avd = os.environ.get("ANDROID_AVD_HOME", "").strip()
    if env_avd:
        return Path(env_avd).expanduser().resolve()

    standard = (get_qalprit_data_dir() / "avd").resolve()
    if (standard / f"{avd_name}.avd").is_dir() or _dir_has_avds(standard):
        return standard

    legacy_avd = (Path.home() / ".android" / "avd").resolve()
    if (legacy_avd / f"{avd_name}.avd").is_dir():
        return legacy_avd

    return standard


def get_android_user_home(avd_name: str = DEFAULT_AVD_NAME) -> Path:
    """Return ANDROID_USER_HOME keeping Google's .android files inside QAlprit data when managed."""
    env_user_home = os.environ.get("ANDROID_USER_HOME", "").strip()
    if env_user_home:
        return Path(env_user_home).expanduser().resolve()

    legacy_avd = (Path.home() / ".android" / "avd").resolve()
    if get_qalprit_avd_home(avd_name=avd_name) == legacy_avd:
        return (Path.home() / ".android").resolve()

    return (get_qalprit_data_dir() / "android-home").resolve()
