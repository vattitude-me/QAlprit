# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for the standalone macOS QAlprit.app bundle."""

import os
import re
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).resolve().parents[1]


def _safe_copy_metadata(packages):
    items = []
    for pkg in packages:
        try:
            items.extend(copy_metadata(pkg))
        except Exception:
            pass
    return items


version = os.environ.get("QALPRIT_VERSION", "")
if not version:
    pyproject_text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'version\s*=\s*"([^"]+)"', pyproject_text)
    version = m.group(1) if m else "0.1.9"

icon_path = ROOT / "build" / "mac-icon" / "QAlprit.icns"
icon_arg = str(icon_path) if icon_path.is_file() else None

ap_datas, ap_binaries, ap_hiddenimports = collect_all("appium")
se_datas, se_binaries, se_hiddenimports = collect_all("selenium")

meta_datas = _safe_copy_metadata(
    [
        "qalprit",
        "Appium-Python-Client",
        "selenium",
        "urllib3",
        "certifi",
        "click",
        "PyYAML",
        "Jinja2",
        "MarkupSafe",
        "pydantic",
        "Pillow",
        "python-frontmatter",
    ]
)

datas = collect_data_files("qalprit") + meta_datas + ap_datas + se_datas
binaries = ap_binaries + se_binaries
hiddenimports = (
    collect_submodules("qalprit")
    + ap_hiddenimports
    + se_hiddenimports
    + [
        "appium",
        "appium.webdriver",
        "selenium",
        "click",
        "yaml",
        "frontmatter",
        "jinja2",
        "pydantic",
        "PIL",
        "PIL.Image",
        "PIL.ImageDraw",
        "PIL.ImageFilter",
    ]
)

a = Analysis(
    [str(ROOT / "src" / "qalprit" / "desktop.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="QAlprit",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=icon_arg,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="QAlprit",
)

app = BUNDLE(
    coll,
    name="QAlprit.app",
    icon=icon_arg,
    bundle_identifier="ca.vattitude.qalprit",
    info_plist={
        "CFBundleName": "QAlprit",
        "CFBundleDisplayName": "QAlprit Workbench",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "NSHighResolutionCapable": True,
        "LSBackgroundOnly": False,
    },
)
