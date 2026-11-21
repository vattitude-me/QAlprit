"""Android emulator camera image injection helpers.

Supports feeding custom still images (e.g., ID cards and selfies) into the
Android emulator's front and back cameras via ``-camera-back imagefile:<path>``
and ``-camera-front imagefile:<path>``, with a best-effort ``virtualscene``
poster fallback when an emulator is already running in virtualscene mode.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

from PIL import Image

from qalprit.devices import find_android_tool
from qalprit.paths import get_qalprit_data_dir

VALID_CAMERAS: frozenset[str] = frozenset({"front", "back"})


def get_emulator_camera_dir() -> Path:
    """Return directory storing Android emulator front/back camera image slots."""
    return (get_qalprit_data_dir() / "emulator-camera").resolve()


def get_camera_slot_path(camera: str) -> Path:
    """Return the canonical PNG slot path for ``front`` or ``back`` camera."""
    cam = camera.strip().lower()
    if cam not in VALID_CAMERAS:
        raise ValueError(f"Camera must be 'front' or 'back', got {camera!r}")
    return get_emulator_camera_dir() / f"{cam}.png"


def _write_placeholder_png(path: Path) -> None:
    """Write a minimal neutral placeholder PNG if the slot file does not exist."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp.png")
    img = Image.new("RGB", (640, 480), color=(32, 36, 44))
    img.save(tmp_path, format="PNG")
    os.replace(tmp_path, path)


def ensure_camera_slots() -> tuple[Path, Path]:
    """Ensure ``back.png`` and ``front.png`` camera slots exist and return ``(back, front)``."""
    back_path = get_camera_slot_path("back")
    front_path = get_camera_slot_path("front")
    if not back_path.is_file():
        _write_placeholder_png(back_path)
    if not front_path.is_file():
        _write_placeholder_png(front_path)
    return back_path, front_path


def get_android_camera_args() -> list[str]:
    """Return Android emulator CLI args wiring back camera to virtualscene poster."""
    back_path, _front_path = ensure_camera_slots()
    return [
        "-camera-back",
        "virtualscene",
        "-virtualscene-poster",
        f"wall={back_path}",
    ]


def get_android_camera_args_string() -> str:
    """Return shell-quoted Android emulator camera args suitable for ``appium:avdArgs``."""
    return " ".join(shlex.quote(arg) for arg in get_android_camera_args())


def set_android_camera_image(
    source_path: Path,
    camera: str = "back",
    *,
    udid: str | None = None,
) -> Path:
    """Copy/convert ``source_path`` into the emulator camera slot for ``camera``.

    Also sends ``adb emu virtualscene poster wall <slot>`` as a best-effort
    fallback in case the target emulator was booted in ``virtualscene`` mode.
    """
    cam = camera.strip().lower()
    if cam not in VALID_CAMERAS:
        raise ValueError(f"Camera must be 'front' or 'back', got {camera!r}")

    src = Path(source_path).expanduser().resolve()
    if not src.is_file():
        raise FileNotFoundError(f"Camera image file not found: {src}")

    slot_path = get_camera_slot_path(cam)
    slot_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = slot_path.with_suffix(".tmp.png")
    with Image.open(src) as img:
        rgb = img.convert("RGB")
        rgb.save(tmp_path, format="PNG")
    os.replace(tmp_path, slot_path)

    # Best-effort fallback if the emulator was launched externally with virtualscene.
    adb_bin = find_android_tool("adb")
    if adb_bin:
        cmd = [adb_bin]
        if udid and udid.startswith("emulator-"):
            cmd.extend(["-s", udid])
        cmd.extend(["emu", "virtualscene", "poster", "wall", str(slot_path)])
        try:
            subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except Exception:
            pass

    return slot_path
