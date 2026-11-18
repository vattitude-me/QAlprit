"""Guards for the desktop app's embedded browser (pywebview / WKWebView).

The macOS app once shipped with its three top menus always open. The CI build
links the app against an old macOS SDK, and WKWebView turns the Popover API off
for apps linked that way, so every `[popover]` element rendered as a visible
box. These checks keep that from coming back.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = (ROOT / "src" / "qalprit" / "ui" / "index.html").read_text(encoding="utf-8")
BUILD_MAC = (ROOT / "packaging" / "macos" / "build_mac.sh").read_text(encoding="utf-8")


def test_popover_open_state_goes_through_the_helper():
    # Only the helper may ask the browser for :popover-open.
    calls = re.findall(r"matches\(\s*[\"']:popover-open[\"']\s*\)", UI)
    assert len(calls) == 1, "use isPopOpen(el) instead of el.matches(':popover-open')"
    assert "function isPopOpen(" in UI


def test_popover_fallback_is_installed_and_styled():
    assert "installPopoverFallback" in UI
    assert "html.no-native-popover .menu-popover:not(.popover-open)" in UI


def test_every_menu_popover_has_a_fallback_target():
    # Menus are driven by popovertarget buttons, which the fallback handles.
    for menu_id in ("devicePopoverMenu", "topMoreMenu", "cardMenu"):
        assert f'id="{menu_id}"' in UI


def test_macos_build_relinks_against_a_recent_sdk():
    assert "vtool -set-build-version macos" in BUILD_MAC
    assert "vtool -show-build" in BUILD_MAC
    assert "exit 1" in BUILD_MAC.split("vtool -show-build", 1)[1]
