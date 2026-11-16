"""Tests for CSV dynamic variable datasets, batch execution, and redesigned workbench APIs."""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from pathlib import Path

import pytest
from click.testing import CliRunner

from qalprit.cli import main as cli_main
from qalprit.datasets import (
    export_csv_datasets,
    import_csv_to_datasets,
    load_datasets,
    mask_bundle_for_ui,
    parse_csv_datasets,
)
from qalprit.desktop import ensure_workspace
from qalprit.inspector import extract_interactive_elements
from qalprit.ui_server import collect_workspace_overview, create_ui_server
from qalprit.validator import validate_project
from qalprit.workbench_api import (
    RunJobManager,
    save_environment_entry,
    save_flow_spec,
    save_screen_locators,
    save_test_journey,
)
from tests.fake_driver import FakeDriver, FakeElement

SAMPLE_CSV = """dataset,username,password,expected,selected
valid_user,standard_user,${QA_PASS},Home visible,true
locked_user,locked_out_user,SuperSecretLocked99,Error: locked out,true
empty_password,standard_user,,Error: Password is required,true
admin_user,admin,AdminPass!42,Home visible,false
"""


def test_csv_dataset_parsing_masking_and_persistence(tmp_path: Path) -> None:
    ws = ensure_workspace(tmp_path / "ds_ws")
    parsed = parse_csv_datasets(SAMPLE_CSV, target_kind="flow", target_name="mda/login")
    assert parsed.variables == ["username", "password"]
    assert parsed.secret_columns == ["password"]
    assert [r.name for r in parsed.rows] == [
        "valid_user",
        "locked_user",
        "empty_password",
        "admin_user",
    ]
    assert parsed.rows[3].selected is False

    saved = import_csv_to_datasets(ws, "flow", "mda/login", SAMPLE_CSV)
    assert (ws / "datasets" / "flows" / "mda" / "login.csv").is_file()
    assert len(saved.rows) == 4

    reloaded = load_datasets(ws, "flow", "mda/login")
    assert reloaded.secret_columns == ["password"]
    assert "SuperSecretLocked99" not in export_csv_datasets(parse_csv_datasets("dataset,username\nx,y\n", target_kind="flow", target_name="a"))

    ui_bundle = mask_bundle_for_ui(reloaded)
    # Raw secret literal must NEVER leak to UI payload; env ref stays visible as env_ref
    serialized = json.dumps(ui_bundle)
    assert "SuperSecretLocked99" not in serialized
    assert "AdminPass!42" not in serialized
    assert ui_bundle["rows"][0]["cells"]["password"]["kind"] == "env_ref"
    assert ui_bundle["rows"][0]["cells"]["password"]["display"] == "${QA_PASS}"
    assert ui_bundle["rows"][1]["cells"]["password"]["kind"] == "secret_literal"
    assert ui_bundle["rows"][2]["cells"]["password"]["kind"] == "empty"


def test_validator_allows_dynamic_variables_and_datasets_http_api(tmp_path: Path) -> None:
    ws = ensure_workspace(tmp_path / "val_ws")
    save_environment_entry(ws, env_name="qa", android_package="com.demo.app")
    save_screen_locators(
        ws,
        screen_name="login",
        platform="android",
        entries=[
            {"key": "username", "strategy": "id", "value": "com.demo:id/user"},
            {"key": "password", "strategy": "id", "value": "com.demo:id/pass", "sensitive": True},
            {"key": "submit", "strategy": "id", "value": "com.demo:id/submit"},
            {"key": "banner", "strategy": "id", "value": "com.demo:id/banner"},
        ],
    )
    save_flow_spec(
        ws,
        name="mda/login",
        params=["username", "password"],
        steps=[
            {"type": {"target": "login.username", "value": "${username}"}},
            {"type": {"target": "login.password", "value": "${password}", "secret": True}},
            {"tap": "login.submit"},
        ],
    )
    save_test_journey(
        ws,
        {
            "id": "SMK-001",
            "title": "Smoke login with dynamic CSV variables",
            "suite": "smoke",
            "platforms": ["android"],
            "params": ["username", "password", "expected"],
            "steps": [
                {"run_flow": {"flow": "mda/login", "params": {"username": "${username}", "password": "${password}"}}},
                {"assert_text": {"target": "login.banner", "contains": "${expected}"}},
            ],
        },
    )
    import_csv_to_datasets(ws, "spec", "SMK-001", SAMPLE_CSV)

    val = validate_project(ws, env_name="qa", suite="smoke", platform="android")
    assert val.ok is True, [e.format() for e in val.errors]

    # Verify HTTP dataset endpoints and overview payload masking
    server = create_ui_server(ws, host="127.0.0.1", port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/overview") as r:
            ov = json.loads(r.read().decode("utf-8"))
        assert "SuperSecretLocked99" not in json.dumps(ov)
        spec_ov = next(s for s in ov["specs"] if s["id"] == "SMK-001")
        assert len(spec_ov["datasets"]["rows"]) == 4

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/datasets?kind=spec&name=SMK-001") as r:
            ds_payload = json.loads(r.read().decode("utf-8"))
        assert ds_payload["variables"] == ["username", "password"]

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/datasets/export?kind=spec&name=SMK-001") as r:
            csv_out = r.read().decode("utf-8")
        assert "valid_user" in csv_out
    finally:
        server.shutdown()
        server.server_close()


def test_batch_dataset_run_executes_all_selected_rows_and_masks_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import qalprit.workbench_api as wb_mod

    ws = ensure_workspace(tmp_path / "batch_ws")
    (ws / ".env").write_text("QA_PASS=EnvRealSecret!99\n", encoding="utf-8")
    save_environment_entry(ws, env_name="qa", android_package="com.demo.app", default_timeout=2.0)
    save_screen_locators(
        ws,
        screen_name="login",
        platform="android",
        entries=[
            {"key": "username", "strategy": "id", "value": "com.demo:id/user"},
            {"key": "password", "strategy": "id", "value": "com.demo:id/pass", "sensitive": True},
            {"key": "submit", "strategy": "id", "value": "com.demo:id/submit"},
            {"key": "error", "strategy": "id", "value": "com.demo:id/error"},
        ],
    )
    save_flow_spec(
        ws,
        name="mda/login",
        params=["username", "password"],
        steps=[
            {"type": {"target": "login.username", "value": "${username}"}},
            {"type": {"target": "login.password", "value": "${password}", "secret": True}},
            {"tap": "login.submit"},
            {"assert_text": {"target": "login.error", "contains": "${expected}"}},
        ],
    )
    import_csv_to_datasets(
        ws,
        "flow",
        "mda/login",
        (
            "dataset,username,password,expected,selected\n"
            "valid_user,standard_user,${QA_PASS},Welcome,true\n"
            "locked_user,locked_out_user,LockedSecret77,Error: locked out,true\n"
            "skipped_row,admin,AdminSecret88,Welcome,false\n"
        ),
    )

    typed_values: list[str] = []

    class _RecordingFakeDriver(FakeDriver):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self._current_user = ""

        def find_elements(self, by: str, value: str):
            if value == "com.demo:id/user":
                el = FakeElement("user")
                orig_send = el.send_keys

                def _capture(txt: str) -> None:
                    self._current_user = txt
                    typed_values.append(txt)
                    orig_send(txt)

                el.send_keys = _capture  # type: ignore[method-assign]
                return [el]
            if value == "com.demo:id/pass":
                el = FakeElement("pass")
                orig_send = el.send_keys

                def _capture_pw(txt: str) -> None:
                    typed_values.append(txt)
                    orig_send(txt)

                el.send_keys = _capture_pw  # type: ignore[method-assign]
                return [el]
            if value == "com.demo:id/submit":
                return [FakeElement("submit")]
            if value == "com.demo:id/error":
                # Simulate locked_user failing expectation!
                msg = "Welcome" if self._current_user == "standard_user" else "Error: unknown"
                return [FakeElement("error", text=msg)]
            return []

        def execute_script(self, script: str, args=None):
            return True

    class _FakeAppiumMgr:
        url = "http://127.0.0.1:4723"

        def __init__(self, **_kw) -> None:
            pass

        def __enter__(self) -> "_FakeAppiumMgr":
            return self

        def __exit__(self, *_args) -> None:
            return

    monkeypatch.setattr(wb_mod, "AppiumManager", _FakeAppiumMgr)
    monkeypatch.setattr(wb_mod, "create_appium_driver", lambda *_a, **_kw: _RecordingFakeDriver())

    fake_apk = ws / "apps" / "mda.apk"
    fake_apk.write_bytes(b"PK\x03\x04apk")

    mgr = RunJobManager()
    res = mgr.start(
        ws,
        ws / "results",
        {
            "platform": "android",
            "device_profile": "android_emulator",
            "env_name": "qa",
            "flow_name": "mda/login",
            "app_path": "apps/mda.apk",
            "run_datasets": True,
        },
    )
    assert res["started"] is True
    for _ in range(100):
        st = mgr.status()
        if not st["running"]:
            break
        time.sleep(0.05)

    assert st["status"] == "completed"
    assert st["summary"]["total"] == 2
    assert st["summary"]["passed"] == 1
    assert st["summary"]["failed"] == 1
    assert "EnvRealSecret!99" in typed_values
    assert "LockedSecret77" in typed_values
    assert "AdminSecret88" not in typed_values

    # Secrets must be redacted in overview, batch.json, and child results.json
    ov = collect_workspace_overview(ws)
    assert len(ov["batches"]) == 1
    assert len(ov["runs"]) == 2
    ov_str = json.dumps(ov)
    assert "EnvRealSecret!99" not in ov_str
    assert "LockedSecret77" not in ov_str

    batch_dir = ws / "results" / ov["batches"][0]["dir_name"]
    assert (batch_dir / "batch_report.html").is_file()
    assert "LockedSecret77" not in (batch_dir / "batch_report.html").read_text(encoding="utf-8")


def test_cli_validate_and_run_dry_run_with_csv_data(tmp_path: Path) -> None:
    ws = ensure_workspace(tmp_path / "cli_ds_ws")
    save_environment_entry(ws, env_name="qa", android_package="com.demo.app")
    save_screen_locators(
        ws,
        screen_name="login",
        platform="android",
        entries=[{"key": "username", "strategy": "id", "value": "com.demo:id/user"}],
    )
    save_test_journey(
        ws,
        {
            "id": "SMK-010",
            "title": "Smoke test with CLI CSV",
            "suite": "smoke",
            "platforms": ["android"],
            "steps": [{"type": {"target": "login.username", "value": "${username}"}}],
        },
    )
    csv_file = ws / "users.csv"
    csv_file.write_text("dataset,username,expected\nrow1,alice,ok\nrow2,bob,ok\n", encoding="utf-8")

    runner = CliRunner()
    v_res = runner.invoke(
        cli_main,
        ["validate", "--suite", "smoke", "--env", "qa", "--platform", "android", "--data", str(csv_file), "--project-root", str(ws)],
    )
    assert v_res.exit_code == 0, v_res.output

    r_res = runner.invoke(
        cli_main,
        [
            "run",
            "--platform",
            "android",
            "--device",
            "android_emulator",
            "--env",
            "qa",
            "--suite",
            "smoke",
            "--data",
            str(csv_file),
            "--dry-run",
            "--project-root",
            str(ws),
        ],
    )
    assert r_res.exit_code == 0, r_res.output
    assert "datasets=2" in r_res.output
    assert "dataset=row1" in r_res.output


def test_inspector_includes_redacted_xml_snippet() -> None:
    xml = (
        '<hierarchy rotation="0">'
        '<android.widget.EditText resource-id="com.demo:id/password" password="true" text="MySecret123" bounds="[10,20][300,80]" />'
        '<android.widget.TextView resource-id="com.demo:id/err" text="Password is required" bounds="[42,611][318,640]" />'
        "</hierarchy>"
    )
    elems = extract_interactive_elements(xml, platform="android")
    by_id = {e["recommended_value"]: e for e in elems}
    assert "MySecret123" not in by_id["com.demo:id/password"]["xml_snippet"]
    assert 'password="true"' in by_id["com.demo:id/password"]["xml_snippet"]
    assert 'text="Password is required"' in by_id["com.demo:id/err"]["xml_snippet"]


def test_ui_redesign_layout_ios_boot_and_api_contracts() -> None:
    ui_html = (Path(__file__).resolve().parents[1] / "src" / "qalprit" / "ui" / "index.html").read_text(encoding="utf-8")

    # 1. Topbar overflow & device popover menus positioned via JS/CSS (no duplicate Workflows library)
    menu_start = ui_html.index('<div id="topMoreMenu"')
    menu_end = ui_html.index("<!-- LEFT SIDEBAR -->")
    top_more_block = ui_html[menu_start:menu_end]
    assert "Workflows library" not in top_more_block
    assert 'id="devicePopoverMenu"' in ui_html
    assert 'id="btnTopBootDevice"' in ui_html
    assert 'id="btnTopShutdownDevice"' in ui_html
    assert 'id="btnOpenFlowsModal" data-nav="workflows"' in ui_html
    assert "anchorPopover" in ui_html

    # 2. Workflows is default Workbench landing view; 1-time Setup & Devices is nested inside Settings
    assert 'data-nav="workflows" aria-current="step"' in ui_html
    settings_start = ui_html.index('id="view-settings"')
    settings_block = ui_html[settings_start:]
    assert 'data-settings-tab="env"' in settings_block
    assert 'data-settings-tab="devices"' in settings_block
    assert 'data-settings-tab="storage"' in settings_block
    assert 'id="envSaveForm"' in settings_block
    assert 'id="view-setup"' in settings_block
    assert 'id="storagePreflightCard"' in settings_block

    # 3. Record bar exposes Platform, Device, Shutdown, App launcher, and Flow name (auto-boots on Start Recording)
    rec_start = ui_html.index('id="recStartForm"')
    rec_end = ui_html.index("</form>", rec_start)
    rec_toolbar = ui_html[rec_start:rec_end]
    assert 'id="recPlatform"' in rec_toolbar
    assert 'id="recDeviceSelect"' in rec_toolbar
    assert 'id="btnRecShutdownDevice"' in rec_toolbar
    assert 'id="recAppSelect"' in rec_toolbar
    assert 'id="recFlowNameInput"' in rec_toolbar
    assert 'id="btnRecHideKeyboard"' in ui_html
    # One Hide keyboard control. The duplicate inside the type bar was removed.
    assert 'id="btnRecHideKeyboard"' in ui_html
    assert 'id="btnRecTypeHideKb"' not in ui_html
    assert "resolveSelectedDeviceForAction" in ui_html
    assert "else if (!starting)" in ui_html

    # 4. iOS Simulator + Android AVD boot & shutdown controls unwrap env_status/running
    assert "const env = st.env_status || st;" in ui_html
    assert "st.running ?? st.busy" in ui_html
    assert "data-boot-avd=" in ui_html
    assert "data-boot-ios=" in ui_html
    assert "data-shutdown-ios=" in ui_html
    assert "data-shutdown-android=" in ui_html

    # 5. Settings preflight, cleanup, and recorder save keys align with backend schemas
    assert "pf.paths || pf" in ui_html
    assert "sw.label || sw.name" in ui_html
    assert "it.existing_paths" in ui_html
    assert "res.removed_ids || res.deleted" in ui_html
    assert "res.flow && res.flow.name" in ui_html

    # 6. Grid tracks constrain 1fr with minmax(0, 1fr) and resizable clamp bounds so Workflows/Journeys never clip horizontally
    assert "grid-template-columns: clamp(200px, var(--rail-w, 272px), 480px) 10px minmax(0, 1fr);" in ui_html
    assert "grid-template-columns: minmax(0, 1fr) 10px clamp(240px, var(--ds-right-w, 300px), 460px);" in ui_html


def test_ios_default_device_profile_and_emulator_shutdown(tmp_path: Path, monkeypatch) -> None:
    import json
    import threading
    import urllib.request
    from qalprit.desktop import ensure_workspace
    from qalprit.devices import DiscoveredDevice
    from qalprit.emulator_manager import EmulatorJobManager
    from qalprit.ui_server import create_ui_server
    from qalprit.workbench_api import _resolve_device_profile, list_workspace_configs, save_screen_locators, save_test_journey

    # Default ios profile resolution avoids falling back to android_emulator when devices.yaml lacks ios_simulator
    _, profiles = list_workspace_configs(ensure_workspace(tmp_path / "ws"))
    stripped = {k: v for k, v in profiles.items() if k != "ios_simulator"}
    ios_prof = _resolve_device_profile(stripped, "ios_simulator", "ios")
    assert ios_prof.platform == "ios"

    ws = (tmp_path / "srv").resolve()
    ensure_workspace(ws)
    save_screen_locators(
        ws,
        screen_name="login",
        platform="ios",
        entries=[{"key": "btn", "strategy": "id", "value": "loginBtn"}],
    )
    save_test_journey(
        ws,
        {"id": "SMK-001", "title": "iOS Dry", "suite": "smoke", "platforms": ["ios"], "steps": [{"tap": "login.btn"}]},
    )

    # Mock simctl shutdown + adb emu kill cleanly
    calls: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        calls.append([str(c) for c in cmd])

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""

        return _R()

    monkeypatch.setattr("qalprit.emulator_manager.platform.system", lambda: "Darwin")
    monkeypatch.setattr("qalprit.emulator_manager.find_android_tool", lambda name: "/fake/adb" if name == "adb" else "")
    monkeypatch.setattr(
        "qalprit.emulator_manager.list_android_devices",
        lambda: ([DiscoveredDevice(platform="android", identifier="emulator-5554", name="Pixel", state="device", kind="emulator")], ""),
    )
    monkeypatch.setattr(
        "qalprit.emulator_manager.list_ios_simulators",
        lambda: ([DiscoveredDevice(platform="ios", identifier="UUID-1234", name="iPhone 17 Pro", state="Booted", kind="simulator")], ""),
    )
    monkeypatch.setattr("qalprit.emulator_manager.subprocess.run", fake_run)

    mgr = EmulatorJobManager()
    shut_res = mgr.shutdown("all")
    assert shut_res["killed_android"] == ["emulator-5554"]
    assert shut_res["shutdown_ios"] == ["UUID-1234"]
    assert ["/fake/adb", "-s", "emulator-5554", "emu", "kill"] in calls
    assert ["xcrun", "simctl", "shutdown", "UUID-1234"] in calls

    # HTTP /api/runs/start with platform=ios and omitted device_profile defaults to ios_simulator (never android_emulator!)
    server = create_ui_server(ws, host="127.0.0.1", port=0)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/runs/start",
            data=json.dumps({"platform": "ios", "env_name": "qa", "suite": "smoke", "dry_run": True}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            run_res = json.loads(resp.read().decode("utf-8"))
        assert run_res["platform"] == "ios"

        shut_req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/emulator/shutdown",
            data=json.dumps({"target": "ios", "identifier": "UUID-1234"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(shut_req) as resp:
            http_shut = json.loads(resp.read().decode("utf-8"))
        assert http_shut["ok"] is True
        assert http_shut["shutdown_ios"] == ["UUID-1234"]
    finally:
        server.shutdown()
        server.server_close()


def test_ios_auto_boot_in_driver_factory_and_recorder_stop_clears_preview(monkeypatch) -> None:
    from qalprit.config_loader import DeviceProfile
    from qalprit.devices import DiscoveredDevice
    from qalprit.driver_factory import _ensure_ios_simulator_ready, _extract_ios_version_from_name
    from qalprit.recorder import RecorderManager

    assert _extract_ios_version_from_name("iPhone 17 Pro (iOS 26 5)") == "26.5"

    cmds: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        cmds.append([str(c) for c in cmd])

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""

        return _R()

    monkeypatch.setattr("qalprit.driver_factory.platform.system", lambda: "Darwin")
    monkeypatch.setattr("qalprit.driver_factory._xcode_has_legacy_simulator_app", lambda: True)
    monkeypatch.setattr(
        "qalprit.driver_factory.list_ios_simulators",
        lambda: (
            [DiscoveredDevice(platform="ios", identifier="SIM-UUID-1", name="iPhone 17 Pro (iOS 26 5)", state="Shutdown", kind="simulator")],
            None,
        ),
    )
    monkeypatch.setattr("qalprit.driver_factory.subprocess.run", fake_run)

    prof = DeviceProfile(platform="ios", target_type="simulator", device_name="iPhone 17 Pro", automation_name="XCUITest")
    udid, ver, dev_name = _ensure_ios_simulator_ready(prof)
    assert udid == "SIM-UUID-1"
    assert ver == "26.5"
    assert dev_name == "iPhone 17 Pro"
    assert ["xcrun", "simctl", "boot", "SIM-UUID-1"] in cmds

    # RecorderManager.stop() clears live preview screenshot & overlays while keeping steps for saving
    rm = RecorderManager()
    rm._active = True
    rm._screenshot_png = b"fake-png"
    rm._elements = [{"key": "btn"}]
    rm._steps = [{"action": "tap", "target": "flow.btn", "value": "", "secret": False, "label": "Tap"}]
    rm.stop()
    st = rm.status()
    assert st["active"] is False
    assert st["has_screenshot"] is False
    assert st["elements"] == []
    assert len(st["steps"]) == 1


def test_inspector_ignores_ios_keyboard_overlays_and_marks_cta_clickable() -> None:
    xml = """<XCUIElementTypeApplication name="MyDemoApp">
      <XCUIElementTypeWindow>
        <XCUIElementTypeOther name="Login button" label="Login" accessible="true" x="16" y="600" width="361" height="45" enabled="true" visible="true"/>
        <XCUIElementTypeKeyboard x="0" y="540" width="393" height="312" enabled="true" visible="true">
          <XCUIElementTypeKey name="a" label="a" x="0" y="560" width="40" height="45" enabled="true" visible="true"/>
          <XCUIElementTypeButton name="Return" label="return" x="300" y="740" width="90" height="45" enabled="true" visible="true"/>
        </XCUIElementTypeKeyboard>
      </XCUIElementTypeWindow>
    </XCUIElementTypeApplication>"""
    elems = extract_interactive_elements(xml, platform="ios")
    keys = [e["key"] for e in elems]
    assert "login_btn" in keys
    assert all(k not in keys for k in ("a", "return", "keyboard"))
    login = next(e for e in elems if e["key"] == "login_btn")
    assert login["clickable"] is True

    ui_html = (Path(__file__).resolve().parents[1] / "src" / "qalprit" / "ui" / "index.html").read_text(encoding="utf-8")
    assert 'flowTab: "steps"' in ui_html
    assert 'specTab: "steps"' in ui_html
    assert 'id="btnRailRecordNew"' in ui_html
    assert 'id="btnWorkflowRecordNew"' in ui_html
    assert 'class="split-handle"' in ui_html
    assert 'grid-template-columns: 24px minmax(112px, auto) minmax(0, 1fr) auto;' in ui_html
    assert 'hide-on-record' in ui_html
    assert 'id="btnRecStepMoveUp"' in ui_html
    assert 'id="btnRecStepMoveDown"' in ui_html
    assert 'id="btnRecStepDup"' in ui_html
    assert 'if (val.startsWith("avd:"))' in ui_html
    assert 'const needsValue = ["type", "assert_text", "wait", "swipe"].includes(act);' in ui_html


def test_avd_profile_resolution_and_recorder_step_reorder_duplicate(tmp_path: Path) -> None:
    import json
    import threading
    import urllib.request
    from qalprit.desktop import ensure_workspace
    from qalprit.ui_server import create_ui_server
    from qalprit.workbench_api import _resolve_device_profile

    avd_prof = _resolve_device_profile({}, "avd:mbtest", "android")
    assert avd_prof.platform == "android"
    assert avd_prof.target_type == "simulator"
    assert avd_prof.avd == "mbtest"

    ws = ensure_workspace(tmp_path / "rec_edit_ws")
    server = create_ui_server(ws, host="127.0.0.1", port=0)
    port = server.server_address[1]
    rm = server.RequestHandlerClass.keywords["recorder_manager"]  # type: ignore[attr-defined]
    rm._active = True
    rm._steps = [
        {"action": "tap", "target": "login.user", "value": "", "secret": False, "label": "Tap user"},
        {"action": "type", "target": "login.pass", "value": "p", "secret": True, "label": "Type pass"},
        {"action": "tap", "target": "login.btn", "value": "", "secret": False, "label": "Tap login"},
    ]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        def _post(path: str, payload: dict):
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}{path}",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read().decode("utf-8"))

        moved = _post("/api/recorder/update", {"index": 2, "move_to": 0})
        assert moved["selected_index"] == 0
        assert [s["target"] for s in moved["steps"]] == ["login.btn", "login.user", "login.pass"]

        duped = _post("/api/recorder/update", {"index": 0, "duplicate": True})
        assert duped["selected_index"] == 1
        assert [s["target"] for s in duped["steps"]] == ["login.btn", "login.btn", "login.user", "login.pass"]
    finally:
        server.shutdown()
        server.server_close()

