# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for the standalone Windows QAlprit executable."""

from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parents[1]

icon_path = ROOT / "build" / "win-icon" / "QAlprit.ico"
icon_arg = str(icon_path) if icon_path.is_file() else None

wv_datas, wv_binaries, wv_hiddenimports = collect_all("webview")
pn_datas, pn_binaries, pn_hiddenimports = collect_all("pythonnet")
cl_datas, cl_binaries, cl_hiddenimports = collect_all("clr_loader")

datas = collect_data_files("qalprit") + wv_datas + pn_datas + cl_datas
binaries = wv_binaries + pn_binaries + cl_binaries
hiddenimports = (
    collect_submodules("qalprit")
    + wv_hiddenimports
    + pn_hiddenimports
    + cl_hiddenimports
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
        "webview.platforms.edgechromium",
        "clr",
        "System",
        "System.IO",
        "Microsoft.Web.WebView2.Core",
        "Microsoft.Web.WebView2.WinForms",
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
    excludes=[
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "cefpython3",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe_gui = EXE(
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

exe_cli = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="qalprit-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    icon=icon_arg,
)

coll = COLLECT(
    exe_gui,
    exe_cli,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="QAlprit",
)
