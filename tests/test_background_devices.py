"""Background devices: the started-device registry, show window, quit cleanup and run errors."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from qalprit import background_devices as bg
from qalprit.workbench_api import explain_validation_errors, update_storage_preferences


@pytest.fixture()
def settings_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "settings.json"
    monkeypatch.setenv("QALPRIT_SETTINGS_PATH", str(path))
    return path


def test_window_hidden_by_default_and_setting_turns_it_on(settings_file: Path) -> None:
    assert bg.device_window_visible() is False
    settings_file.write_text(json.dumps({"show_device_window": True}), encoding="utf-8")
    assert bg.device_window_visible() is True


def test_preferences_save_keeps_show_device_window(settings_file: Path, tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    res = update_storage_preferences(ws, {"show_device_window": True})
    assert res["settings"]["show_device_window"] is True
    res = update_storage_preferences(ws, {"show_device_window": False})
    assert res["settings"]["show_device_window"] is False
    assert bg.device_window_visible() is False


def test_registry_remembers_forgets_and_prunes(settings_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bg.remember_started("android", "emulator-5554", name="Pixel", hidden=True)
    bg.remember_started("ios", "UDID-1", name="iPhone", hidden=False)
    bg.remember_started("android", "emulator-5554", name="Pixel", hidden=True)  # no duplicate
    bg.remember_started("web", "x", hidden=True)  # ignored kind
    entries = bg.started_devices(prune=False)
    assert sorted((e["kind"], e["identifier"]) for e in entries) == [("android", "emulator-5554"), ("ios", "UDID-1")]
    assert bg.is_hidden("android", "emulator-5554")
    assert not bg.is_hidden("ios", "UDID-1")

    monkeypatch.setattr(bg, "_running_identifiers", lambda: ({"emulator-5554"}, set()))
    alive = bg.started_devices()
    assert [e["identifier"] for e in alive] == ["emulator-5554"]
    assert len(bg.started_devices(prune=False)) == 1

    bg.forget("android")
    assert bg.started_devices(prune=False) == []


def test_corrupt_registry_reads_as_empty(settings_file: Path) -> None:
    (settings_file.parent / "started-devices.json").write_text("{not json", encoding="utf-8")
    assert bg.started_devices(prune=False) == []


def test_android_window_cannot_be_shown_later(settings_file: Path) -> None:
    with pytest.raises(ValueError, match="Show device window"):
        bg.show_window("android", "emulator-5554")


def test_shutdown_only_stops_hidden_devices(settings_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bg.remember_started("android", "emulator-5554", hidden=True)
    bg.remember_started("android", "emulator-5556", hidden=False)
    monkeypatch.setattr(bg, "_running_identifiers", lambda: ({"emulator-5554", "emulator-5556"}, set()))
    monkeypatch.setattr(bg, "find_android_tool", lambda name: "/fake/adb")
    calls: list[list[str]] = []
    monkeypatch.setattr(bg.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or SimpleNamespace(returncode=0))

    res = bg.shutdown_background_devices()
    assert res == {"android": ["emulator-5554"], "ios": []}
    assert calls == [["/fake/adb", "-s", "emulator-5554", "emu", "kill"]]
    left = bg.started_devices(prune=False)
    assert [e["identifier"] for e in left] == ["emulator-5556"]


def test_workbench_shutdown_stops_every_manager(settings_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from qalprit import ui_server

    seen: list[str] = []

    class Mgr:
        def __init__(self, name: str) -> None:
            self.name = name

        def stop(self) -> None:
            seen.append(self.name)

        def cancel_for_quit(self) -> None:
            seen.append(self.name)

    class Broken:
        def stop(self) -> None:
            raise RuntimeError("boom")

    monkeypatch.setattr(ui_server.background_devices, "shutdown_background_devices", lambda: {"android": [], "ios": []})
    server = SimpleNamespace(qalprit_managers={"recorder": Broken(), "runs": Mgr("runs"), "emulator": Mgr("emulator")})
    summary = ui_server.shutdown_workbench_services(server)  # type: ignore[arg-type]
    assert seen == ["runs", "emulator"]
    assert summary["errors"] and summary["errors"][0].startswith("recorder:")
    assert summary["stopped_devices"] == {"android": [], "ios": []}


def test_missing_variables_are_grouped_into_one_clear_message() -> None:
    errs = [
        SimpleNamespace(file=Path("flows/login.md"), message="Missing environment variable '${username}'"),
        SimpleNamespace(file=Path("flows/login.md"), message="Missing environment variable '${password}'"),
        SimpleNamespace(file=Path("flows/login.md"), message="Missing environment variable '${username}'"),
        SimpleNamespace(file=Path("screens/screens.yaml"), message="Unknown locator target 'a.b'"),
        SimpleNamespace(file=Path("screens/screens.yaml"), message="Unknown locator target 'a.b'"),
    ]
    msg = explain_validation_errors(errs, "qa")
    first, second = msg.split("\n")
    assert first.startswith("login uses ${username}, ${password}, but nothing gives them a value.")
    assert "'qa' environment" in first
    assert second == "Unknown locator target 'a.b'"


def test_journey_uses_its_workflow_data_rows(tmp_path: Path) -> None:
    from qalprit.datasets import save_datasets
    from qalprit.spec_loader import load_specs
    from qalprit.workbench_api import journey_workflow_datasets

    (tmp_path / "specs" / "smoke").mkdir(parents=True)
    (tmp_path / "specs" / "smoke" / "SMK-009.md").write_text(
        "---\nid: SMK-009\ntitle: Login\nplatforms: [ios]\nsteps:\n  - run_flow: login\n---\n", encoding="utf-8"
    )
    spec = load_specs(tmp_path / "specs")[0][0]
    assert journey_workflow_datasets(tmp_path, spec) is None

    save_datasets(
        tmp_path,
        "flow",
        "login",
        {
            "variables": ["username", "password"],
            "secret_columns": ["password"],
            "rows": [
                {"name": "dataset_1", "selected": True, "values": {"username": "a", "password": "1"}},
                {"name": "dataset_2", "selected": True, "values": {"username": "b", "password": "2"}},
            ],
        },
    )
    bundle = journey_workflow_datasets(tmp_path, spec)
    assert bundle is not None
    assert [r.name for r in bundle.rows] == ["dataset_1", "dataset_2"]


def test_journey_calling_workflow_with_data_rows_validates(tmp_path: Path) -> None:
    from qalprit.datasets import save_datasets
    from qalprit.validator import validate_project

    src = Path(__file__).resolve().parents[1]
    for sub in ("config", "screens"):
        import shutil

        shutil.copytree(src / sub, tmp_path / sub)
    (tmp_path / "flows").mkdir()
    (tmp_path / "flows" / "typing.md").write_text(
        "---\nname: typing\nparams: [username]\nsteps:\n  - type: {target: android.allow_notifications, text: '${username}'}\n---\n", encoding="utf-8"
    )
    (tmp_path / "specs" / "smoke").mkdir(parents=True)
    (tmp_path / "specs" / "smoke" / "SMK-009.md").write_text(
        "---\nid: SMK-009\ntitle: Login\nsuite: smoke\nplatforms: [android]\nsteps:\n  - run_flow: typing\n---\n",
        encoding="utf-8",
    )
    env = next(iter(__import__("yaml").safe_load((src / "config" / "environments.yaml").read_text())["environments"]))

    def errors() -> list[str]:
        res = validate_project(tmp_path, env_name=env, spec_id="SMK-009", platform="android")
        return [i.message for i in res.errors]

    assert any("missing required param" in m for m in errors())
    save_datasets(
        tmp_path,
        "flow",
        "typing",
        {"variables": ["username"], "rows": [{"name": "dataset_1", "selected": True, "values": {"username": "a"}}]},
    )
    assert not [m for m in errors() if "username" in m]


def test_quit_asks_only_when_work_would_be_cut_short(settings_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from qalprit import desktop, ui_server

    released: list[bool] = []
    monkeypatch.setattr(desktop, "_release_services", lambda server: released.append(True))

    class Runs:
        running = False

        def status(self) -> dict:
            return {"running": self.running}

    runs = Runs()
    rec = SimpleNamespace(_steps=[])
    emu = SimpleNamespace(_state={"running": False})
    server = SimpleNamespace(qalprit_managers={"runs": runs, "recorder": rec, "emulator": emu})

    asked: list[str] = []
    assert ui_server.workbench_busy_work(server) == []  # type: ignore[arg-type]
    assert desktop._on_window_closing(server, ask=lambda msg: asked.append(msg) or False) is True
    assert asked == [] and released == [True]

    runs.running = True
    rec._steps = [{"action": "tap"}, {"action": "tap"}]
    emu._state["running"] = True
    busy = ui_server.workbench_busy_work(server)  # type: ignore[arg-type]
    assert len(busy) == 3 and "2 steps you have not saved" in busy[1]

    released.clear()
    assert desktop._on_window_closing(server, ask=lambda msg: asked.append(msg) or False) is False
    assert released == [] and "test run is still going" in asked[-1]
    assert desktop._on_window_closing(server, ask=lambda msg: True) is True
    assert released == [True]


def test_run_on_booted_background_simulator_opens_window_when_setting_on(
    settings_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from qalprit.config_loader import DeviceProfile
    from qalprit.devices import DiscoveredDevice
    from qalprit.driver_factory import _ensure_ios_simulator_ready

    booted = DiscoveredDevice(platform="ios", identifier="UDID-1", name="iPhone 17 (iOS 26 5)", state="Booted", kind="simulator")
    monkeypatch.setattr("qalprit.driver_factory.platform.system", lambda: "Darwin")
    monkeypatch.setattr("qalprit.driver_factory.list_ios_simulators", lambda: ([booted], None))
    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(bg, "show_window", lambda kind, ident="": shown.append((kind, ident)) or {"ok": True})
    bg.remember_started("ios", "UDID-1", name="iPhone 17", hidden=True)
    prof = DeviceProfile(platform="ios", target_type="simulator", device_name="iPhone 17", automation_name="XCUITest")

    _ensure_ios_simulator_ready(prof)
    assert shown == []

    settings_file.write_text(json.dumps({"show_device_window": True}), encoding="utf-8")
    _ensure_ios_simulator_ready(prof)
    assert shown == [("ios", "UDID-1")]


def test_windowless_emulator_found_from_process_list(monkeypatch: pytest.MonkeyPatch) -> None:
    ps_out = (
        "/sdk/emulator/qemu/darwin-aarch64/qemu-system-aarch64-headless -avd QAlprit_Pixel -delay-adb -no-window -no-audio\n"
        "/sdk/emulator/qemu/darwin-aarch64/qemu-system-aarch64 -avd Other_Pixel\n"
    )

    def fake_run(cmd, **_kw):
        out = ps_out if cmd[0] == "ps" else ("QAlprit_Pixel\n" if cmd[1:3] == ["-s", "emulator-5554"] else "Other_Pixel\n")
        return SimpleNamespace(returncode=0, stdout=out, stderr="")

    monkeypatch.setattr(bg.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(bg.subprocess, "run", fake_run)
    monkeypatch.setattr(bg, "find_android_tool", lambda _n: "adb")
    monkeypatch.setattr(bg, "_running_identifiers", lambda: ({"emulator-5554", "emulator-5556"}, set()))

    assert bg._windowless_avd_names() == {"QAlprit_Pixel"}
    assert bg.windowless_android_emulators() == [
        {"kind": "android", "identifier": "emulator-5554", "name": "QAlprit_Pixel", "hidden": True}
    ]
