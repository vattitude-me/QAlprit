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
