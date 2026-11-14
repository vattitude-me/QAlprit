"""Tests for QAlprit desktop bootstrap, bundled runtime discovery, and guided Workbench API."""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from qalprit.appium_manager import build_appium_env
from qalprit.desktop import ensure_workspace
from qalprit.doctor import (
    find_android_sdk,
    find_appium_entry,
    find_appium_home,
    find_java_bin,
    find_node_bin,
)
from qalprit.ui_server import collect_workspace_overview, create_ui_server
from qalprit.workbench_api import (
    RunJobManager,
    inspect_xml_payload,
    run_workspace_validation,
    save_environment_entry,
    save_flow_spec,
    save_screen_locators,
    save_test_journey,
    save_uploaded_app,
)

SAMPLE_XML = """<hierarchy rotation="0">
  <android.widget.FrameLayout bounds="[0,0][1080,2400]">
    <android.widget.EditText resource-id="com.demo:id/email" text="" hint="Email" clickable="true" enabled="true" bounds="[10,10][200,50]" />
    <android.widget.EditText resource-id="com.demo:id/password" password="true" text="" hint="Password" clickable="true" enabled="true" bounds="[10,60][200,100]" />
    <android.widget.Button resource-id="com.demo:id/login_btn" text="Sign In" content-desc="Sign In Button" clickable="true" enabled="true" bounds="[10,120][200,180]" />
  </android.widget.FrameLayout>
</hierarchy>"""


def test_ensure_workspace_bootstraps_and_preserves_existing(tmp_path: Path) -> None:
    ws = ensure_workspace(tmp_path)
    assert (ws / "apps").is_dir()
    assert (ws / "config" / "devices.yaml").is_file()
    assert (ws / "config" / "environments.yaml").is_file()
    assert (ws / "screens" / "screens.yaml").is_file()
    assert (ws / "flows").is_dir()
    assert (ws / "specs" / "smoke").is_dir()
    assert (ws / "results").is_dir()

    custom_env = "QA_USER=custom@example.com\n"
    (ws / ".env").write_text(custom_env, encoding="utf-8")
    ensure_workspace(ws)
    assert (ws / ".env").read_text(encoding="utf-8") == custom_env


def test_bundled_runtime_resolution_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rt = tmp_path / "runtime"
    is_win = os.name == "nt"
    node_rel = Path("node") / ("node.exe" if is_win else "node")
    java_rel = Path("jdk") / "bin" / ("java.exe" if is_win else "java")
    adb_rel = Path("android-sdk") / "platform-tools" / ("adb.exe" if is_win else "adb")
    appium_js = Path("appium") / "node_modules" / "appium" / "build" / "lib" / "main.js"

    for rel in (node_rel, java_rel, adb_rel, appium_js):
        target = rt / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    (rt / "appium_home").mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("QALPRIT_RUNTIME_DIR", str(rt))

    assert find_node_bin() == str(rt / node_rel)
    assert find_appium_home() == rt / "appium_home"
    cmd, _ = find_appium_entry()
    assert cmd == [str(rt / node_rel), str(rt / appium_js)]
    assert find_android_sdk() == rt / "android-sdk"
    assert find_java_bin() == str(rt / java_rel)

    env = build_appium_env({"PATH": ""})
    assert env["APPIUM_HOME"] == str(rt / "appium_home")
    assert env["ANDROID_HOME"] == str(rt / "android-sdk")
    assert env["JAVA_HOME"] == str(rt / "jdk")


def test_guided_workbench_end_to_end_pipeline(tmp_path: Path) -> None:
    ws = ensure_workspace(tmp_path)

    # Reject disallowed extension & empty upload
    with pytest.raises(ValueError, match="Unsupported app file extension"):
        save_uploaded_app(ws, "malware.exe", b"mz")
    with pytest.raises(ValueError, match="empty"):
        save_uploaded_app(ws, "empty.apk", b"")

    uploaded = save_uploaded_app(ws, "../my-demo.apk", b"PK\x03\x04fakeapk")
    assert uploaded["rel_path"] == "apps/my-demo.apk"
    assert (ws / "apps" / "my-demo.apk").is_file()

    save_environment_entry(
        ws,
        env_name="qa",
        android_package="com.demo.app",
        default_timeout=10.0,
    )

    inspected = inspect_xml_payload(ws, xml_text=SAMPLE_XML, platform="android", screen_name="login")
    assert len(inspected["elements"]) >= 3
    pw_items = [e for e in inspected["elements"] if "password" in e["key"]]
    assert pw_items and pw_items[0]["suggested_sensitive"] is True

    saved_loc = save_screen_locators(
        ws,
        screen_name="login",
        platform="android",
        entries=[
            {"key": "email", "strategy": "id", "value": "com.demo:id/email", "sensitive": False},
            {"key": "password", "strategy": "id", "value": "com.demo:id/password", "sensitive": True},
            {"key": "login_btn", "strategy": "accessibility_id", "value": "Sign In Button", "sensitive": False},
        ],
    )
    assert "login.login_btn" in saved_loc["saved_targets"]

    saved_flow = save_flow_spec(
        ws,
        name="auth/login",
        params=["email", "password"],
        steps=[
            {"type": {"target": "login.email", "value": "${email}"}},
            {"type": {"target": "login.password", "value": "${password}", "secret": True}},
            {"tap": "login.login_btn"},
        ],
    )
    assert saved_flow["name"] == "auth/login"

    saved_spec = save_test_journey(
        ws,
        {
            "id": "SMOKE-01",
            "title": "User signs in cleanly",
            "suite": "smoke",
            "platforms": ["android"],
            "reset": "clear_data",
            "steps": [
                {"run_flow": {"flow": "auth/login", "params": {"email": "${QA_USER}", "password": "${QA_PASS}"}}},
                {"screenshot": "after_login"},
            ],
        },
    )
    assert saved_spec["id"] == "SMOKE-01"

    val = run_workspace_validation(ws, suite="smoke", env_name="qa", platform="android")
    assert val["ok"] is True
    assert val["specs_count"] == 1
    assert val["flows_count"] == 1

    mgr = RunJobManager()
    dry = mgr.start(
        ws,
        ws / "results",
        {
            "platform": "android",
            "device_profile": "android_emulator",
            "env_name": "qa",
            "suite": "smoke",
            "dry_run": True,
        },
    )
    assert dry["dry_run"] is True
    assert dry["specs"][0]["id"] == "SMOKE-01"


def test_workbench_http_endpoints(tmp_path: Path) -> None:
    ws = ensure_workspace(tmp_path)
    overview = collect_workspace_overview(ws)
    assert "step_vocabulary" in overview
    assert "tap" in overview["step_vocabulary"]

    server = create_ui_server(ws, host="127.0.0.1", port=0)
    port = server.server_address[1]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    def _post(path: str, data: dict | bytes, content_type: str = "application/json") -> dict:
        raw = json.dumps(data).encode("utf-8") if isinstance(data, dict) else data
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=raw,
            headers={"Content-Type": content_type},
            method="POST",
        )
        with opener.open(req) as resp:
            return json.loads(resp.read().decode("utf-8"))

    try:
        with opener.open(f"http://127.0.0.1:{port}/") as resp:
            html = resp.read().decode("utf-8")
            assert "QAlprit Workbench" in html
            assert "QA Workflow Steps" in html
            assert 'aria-current="step"' in html

        up = _post("/api/apps/upload?filename=client-beta.apk", b"PK\x03\x04apk", "application/octet-stream")
        assert up["ok"] is True
        assert (ws / "apps" / "client-beta.apk").is_file()

        env_res = _post("/api/environments/save", {"env_name": "qa", "android_package": "com.client.beta"})
        assert env_res["ok"] is True

        xml_res = _post("/api/inspect/xml", {"xml_text": SAMPLE_XML, "platform": "android", "screen_name": "login"})
        assert xml_res["ok"] is True

        scr_res = _post(
            "/api/screens/save",
            {
                "screen_name": "login",
                "platform": "android",
                "entries": [{"key": "login_btn", "strategy": "accessibility_id", "value": "Sign In Button"}],
            },
        )
        assert scr_res["ok"] is True

        flow_res = _post(
            "/api/flows/save",
            {"name": "auth/tap_login", "params": [], "steps": [{"tap": "login.login_btn"}]},
        )
        assert flow_res["ok"] is True

        spec_res = _post(
            "/api/specs/save",
            {
                "id": "SMOKE-HTTP-1",
                "title": "HTTP API Journey",
                "suite": "smoke",
                "platforms": ["android"],
                "steps": [{"run_flow": "auth/tap_login"}],
            },
        )
        assert spec_res["ok"] is True

        val_res = _post("/api/validate", {"suite": "smoke", "env_name": "qa", "platform": "android"})
        assert val_res["ok"] is True

        dry_res = _post(
            "/api/runs/start",
            {
                "platform": "android",
                "device_profile": "android_emulator",
                "env_name": "qa",
                "suite": "smoke",
                "dry_run": True,
            },
        )
        assert dry_res["dry_run"] is True
        assert dry_res["specs"][0]["id"] == "SMOKE-HTTP-1"

        with opener.open(f"http://127.0.0.1:{port}/api/runs/status") as resp:
            status_payload = json.loads(resp.read().decode("utf-8"))
            assert status_payload["running"] is False

        with pytest.raises(urllib.error.HTTPError) as exc_info:
            _post("/api/does-not-exist", {})
        assert exc_info.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
