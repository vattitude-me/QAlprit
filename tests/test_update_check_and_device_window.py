from __future__ import annotations

import time
from pathlib import Path

import pytest

from qalprit import update_check
from qalprit.workbench_api import RunJobManager


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QALPRIT_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.delenv("QALPRIT_NO_UPDATE_CHECK", raising=False)


def test_is_newer_compares_numbers_not_text() -> None:
    assert update_check.is_newer("0.1.100", "0.1.45")
    assert not update_check.is_newer("0.1.45", "0.1.45")
    assert not update_check.is_newer("", "0.1.45")


def test_check_asks_pypi_once_a_day(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def fake_fetch(timeout: float = 5.0) -> str:
        calls.append(1)
        return "99.0.0"

    monkeypatch.setattr(update_check, "_fetch_latest", fake_fetch)
    first = update_check.check_for_update()
    assert first["update_available"] and first["latest"] == "99.0.0"
    update_check.check_for_update()
    assert len(calls) == 1, "the cached answer is used within a day"
    update_check.check_for_update(force=True)
    assert len(calls) == 2


def test_check_off_does_not_call_pypi_but_check_now_does(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(update_check, "_fetch_latest", lambda timeout=5.0: calls.append(1) or "0.0.1")
    update_check.set_enabled(False)
    info = update_check.check_for_update()
    assert info["enabled"] is False and calls == []
    update_check.check_for_update(force=True)
    assert calls == [1]


def test_offline_check_reports_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(timeout: float = 5.0) -> str:
        raise OSError("offline")

    monkeypatch.setattr(update_check, "_fetch_latest", boom)
    info = update_check.check_for_update(force=True)
    assert info["update_available"] is False
    assert "PyPI" in info["error"]


def test_self_update_refused_outside_pip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(update_check, "install_kind", lambda: "frozen")
    with pytest.raises(RuntimeError):
        update_check.start_self_update("1.0.0")


def test_latest_screenshot_picks_newest_and_skips_reference(tmp_path: Path) -> None:
    mgr = RunJobManager()
    run_id = "20261123_120000_android_qa"
    shots = tmp_path / run_id / "screenshots" / "MDA-001" / "attempt_1"
    shots.mkdir(parents=True)
    older = shots / "01_launch.png"
    older.write_bytes(b"a")
    newer = shots / "02_tap.png"
    newer.write_bytes(b"b")
    ref = shots / "03_tap_REF.png"
    ref.write_bytes(b"r")
    now = time.time()
    import os

    os.utime(older, (now - 10, now - 10))
    os.utime(newer, (now - 5, now - 5))
    os.utime(ref, (now, now))
    assert mgr.latest_screenshot() is None
    with mgr._lock:
        mgr._state["run_id"] = run_id
        mgr._results_dir = tmp_path
    assert mgr.latest_screenshot() == newer


def test_restart_android_with_window_turns_setting_on_and_boots_same_avd(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess
    from types import SimpleNamespace

    from qalprit import background_devices
    from qalprit import emulator_manager as em

    running = [SimpleNamespace(identifier="emulator-5554", kind="emulator", state="device", name="emulator-5554")]
    monkeypatch.setattr(em, "find_android_tool", lambda name: "/fake/adb")
    monkeypatch.setattr(em, "list_android_devices", lambda: (list(running), ""))
    monkeypatch.setattr(
        em.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="Work_Pixel\r\nOK\r\n", stderr=""),
    )
    background_devices.remember_started("android", "emulator-5554", name="emulator-5554", hidden=True)
    mgr = em.EmulatorJobManager()
    stopped: list[str] = []

    def fake_shutdown(target: str = "all", identifier: str = "") -> dict:
        stopped.append(identifier)
        running.clear()
        return {}

    booted: list[str] = []
    monkeypatch.setattr(mgr, "shutdown", fake_shutdown)
    monkeypatch.setattr(mgr, "start_boot_android", lambda avd_name="": booted.append(avd_name) or {"ok": True})

    res = mgr.restart_android_with_window("emulator-5554")
    assert stopped == ["emulator-5554"]
    assert booted == ["Work_Pixel"]
    assert res["restarted"] is True
    assert background_devices.device_window_visible() is True
