"""Tests for AppiumManager env injection and status checks."""

from __future__ import annotations

from pathlib import Path

from qalprit.appium_manager import AppiumManager, build_appium_env, check_appium_status


def test_build_appium_env_preserves_existing_and_populates_defaults() -> None:
    env = build_appium_env({"PATH": "/usr/bin"})
    assert "/usr/bin" in env["PATH"]
    # On machines with Android SDK / JDK installed, ANDROID_HOME / JAVA_HOME are populated
    if "ANDROID_HOME" in env:
        assert Path(env["ANDROID_HOME"]).exists()


def test_check_appium_status_returns_false_on_unreachable_port() -> None:
    assert check_appium_status("http://127.0.0.1:59999", timeout=0.2) is False


def test_appium_manager_external_url_failure(tmp_path: Path) -> None:
    mgr = AppiumManager(
        log_path=tmp_path / "appium.log",
        appium_url="http://127.0.0.1:59999",
    )
    try:
        mgr.__enter__()
        assert False, "Expected RuntimeError for unreachable Appium URL"
    except RuntimeError as exc:
        assert "not reachable" in str(exc)
