"""Recording the app open on the phone instead of the one the environment names."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from qalprit import recorder as rec_mod
from qalprit.recorder import RecorderManager
from qalprit.spec_loader import load_flows


def _write_project(root: Path, *, package: str) -> None:
    (root / "config").mkdir()
    (root / "config" / "environments.yaml").write_text(
        f"environments:\n  qa:\n    android_package: {package}\n", encoding="utf-8"
    )
    (root / "config" / "devices.yaml").write_text("devices: {}\n", encoding="utf-8")
    for d in ("screens", "flows", "specs"):
        (root / d).mkdir()
    (root / "screens" / "screens.yaml").write_text("screens: {}\n", encoding="utf-8")


class _Mgr:
    url = "http://127.0.0.1:4723"

    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    def __enter__(self) -> "_Mgr":
        return self

    def __exit__(self, *a: Any) -> None:
        return None


def _boot(monkeypatch, tmp_path: Path, front: str, **start_kwargs: Any) -> tuple[RecorderManager, list[dict]]:
    """Run the recorder's start with the boot worker inline and the device faked."""
    resets: list[dict] = []

    class _InlineThread:
        def __init__(self, target=None, **_k: Any) -> None:
            self._target = target

        def start(self) -> None:
            if self._target and self._target.__name__ == "_boot_worker":
                self._target()

    monkeypatch.setattr(rec_mod.threading, "Thread", _InlineThread)
    monkeypatch.setattr(rec_mod, "AppiumManager", _Mgr)
    monkeypatch.setattr(rec_mod, "create_appium_driver", lambda *a, **k: type("D", (), {"capabilities": {}})())
    monkeypatch.setattr(rec_mod, "_current_app", lambda driver, platform: front)
    monkeypatch.setattr(rec_mod, "apply_app_reset", lambda driver, how, **k: resets.append({"how": how, **k}) or (True, ""))
    monkeypatch.setattr(RecorderManager, "_capture_snapshot_locked", lambda self: None)
    monkeypatch.setattr(RecorderManager, "_start_android_getevent_watcher", lambda self, serial: None)
    rm = RecorderManager()
    rm.start(tmp_path, platform="android", device_profile="live:android:R58M123ABC", env_name="qa", settle_seconds=0, **start_kwargs)
    return rm, resets


def test_environment_app_is_used_by_default(monkeypatch, tmp_path: Path) -> None:
    _write_project(tmp_path, package="com.bank.app")
    rm, resets = _boot(monkeypatch, tmp_path, front="com.other.app")
    assert rm.status()["recording_app"] == "com.bank.app"
    assert resets[0]["app_id"] == "com.bank.app"


def test_foreground_source_ignores_the_environment_app(monkeypatch, tmp_path: Path) -> None:
    _write_project(tmp_path, package="com.bank.app")
    rm, resets = _boot(monkeypatch, tmp_path, front="com.other.app", app_source="foreground", app_path_str="apps/x.apk")
    st = rm.status()
    assert st["app_source"] == "foreground"
    assert st["recording_app"] == "com.other.app"
    assert resets[0]["app_id"] == "com.other.app"
    assert resets[0]["app_path"] is None, "the open app is never reinstalled from a file"


def test_foreground_recording_saves_a_launch_step_even_without_restart(monkeypatch, tmp_path: Path) -> None:
    _write_project(tmp_path, package="com.bank.app")
    rm, _ = _boot(monkeypatch, tmp_path, front="com.other.app", app_source="foreground", start_mode="as_is")
    rm._locators = {"home.go": {"screen": "home", "key": "go", "strategy": "text", "value": "Go", "sensitive": False}}
    rm._steps = [{"action": "tap", "target": "home.go", "value": "", "secret": False, "label": "Tap Go"}]
    rm.save_recorded(flow_name="other/go", suite="smoke", stop_after=False)
    flow = load_flows(tmp_path / "flows")[0]["other/go"]
    assert [s.type for s in flow.steps] == ["launch", "tap"]
    assert flow.steps[0].params["app"] == "com.other.app"


def test_foreground_source_on_the_home_screen_records_without_an_app(monkeypatch, tmp_path: Path) -> None:
    _write_project(tmp_path, package="com.bank.app")
    rm, resets = _boot(monkeypatch, tmp_path, front="com.google.android.apps.nexuslauncher", app_source="foreground")
    assert rm.status()["recording_app"] == ""
    assert not resets
    assert any("home screen" in line for line in rm.status()["logs"])
