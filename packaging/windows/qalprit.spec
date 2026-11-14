# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for the standalone Windows QAlprit executable."""

from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parents[1]

datas = collect_data_files("qalprit")
hiddenimports = (
    collect_submodules("qalprit")
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
    binaries=[],
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
    console=True,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="QAlprit",
)
