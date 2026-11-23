"""The chosen app file wins over the environment's app id, and launch errors read as one line."""

from __future__ import annotations

from pathlib import Path

from qalprit import workbench_api
from qalprit.config_loader import EnvironmentConfig
from qalprit.error_text import friendly_error
from qalprit.workbench_api import env_for_app_file

APPIUM_LAUNCH_ERROR = (
    "Message: Cannot start the 'com.bank.nonprod' application. Consider checking the driver's "
    "troubleshooting documentation. Original error: Activity name '.org.wikipedia.main.MainActivity' "
    "used to start the app doesn't exist or cannot be launched! Stacktrace: UnknownError: Cannot start "
    "at getResponseForW3CError (/opt/homebrew/lib/node_modules/appium/errors.js:829:36)"
)


def _env(**kw: str) -> EnvironmentConfig:
    return EnvironmentConfig(name="qa", **kw)


def test_app_file_id_replaces_a_different_environment_id(tmp_path: Path, monkeypatch) -> None:
    apk = tmp_path / "wiki.apk"
    apk.write_bytes(b"x")
    monkeypatch.setattr(workbench_api, "_detect_app_identifier", lambda p, k: "org.wikipedia.alpha")
    env, note = env_for_app_file(_env(android_package="com.bank.nonprod"), apk, "android")
    assert env.android_package == "org.wikipedia.alpha"
    assert "com.bank.nonprod" in note and "org.wikipedia.alpha" in note


def test_matching_or_unknown_app_file_keeps_the_environment(tmp_path: Path, monkeypatch) -> None:
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"x")
    base = _env(android_package="com.bank.nonprod")
    monkeypatch.setattr(workbench_api, "_detect_app_identifier", lambda p, k: "com.bank.nonprod")
    assert env_for_app_file(base, apk, "android") == (base, "")
    monkeypatch.setattr(workbench_api, "_detect_app_identifier", lambda p, k: "")
    assert env_for_app_file(base, apk, "android") == (base, "")
    assert env_for_app_file(base, None, "android") == (base, "")


def test_empty_environment_id_is_filled_without_a_note(tmp_path: Path, monkeypatch) -> None:
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"x")
    monkeypatch.setattr(workbench_api, "_detect_app_identifier", lambda p, k: "org.wikipedia.alpha")
    env, note = env_for_app_file(_env(), apk, "android")
    assert env.android_package == "org.wikipedia.alpha"
    assert note == ""


def test_apk_on_ios_is_left_alone(tmp_path: Path, monkeypatch) -> None:
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"x")
    monkeypatch.setattr(workbench_api, "_detect_app_identifier", lambda p, k: "org.wikipedia.alpha")
    base = _env(ios_bundle_id="com.bank.ios")
    assert env_for_app_file(base, apk, "ios") == (base, "")


def test_appium_launch_error_becomes_one_readable_line() -> None:
    msg = friendly_error(APPIUM_LAUNCH_ERROR)
    assert msg.startswith("The app could not be opened.")
    assert "Stacktrace" not in msg and "node_modules" not in msg and "Consider checking" not in msg
    assert "\n" not in msg
