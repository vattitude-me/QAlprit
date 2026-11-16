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


def test_desktop_main_ignores_macos_launchservices_psn_arg(monkeypatch: pytest.MonkeyPatch) -> None:
    import qalprit.desktop as desktop_mod

    called = {"workbench": 0, "cli": 0}
    monkeypatch.setattr(desktop_mod, "launch_workbench", lambda *a, **kw: called.__setitem__("workbench", called["workbench"] + 1))
    monkeypatch.setattr(desktop_mod, "cli_main", lambda *a, **kw: called.__setitem__("cli", called["cli"] + 1))

    desktop_mod.main(["-psn_0_987654"])
    assert called["workbench"] == 1
    assert called["cli"] == 0

    desktop_mod.main(["doctor", "--json-output"])
    assert called["cli"] == 1


def test_desktop_main_browser_flag_disables_native_window(monkeypatch: pytest.MonkeyPatch) -> None:
    import qalprit.desktop as desktop_mod

    recorded: list[dict[str, object]] = []
    monkeypatch.setattr(desktop_mod, "launch_workbench", lambda *a, **kw: recorded.append(dict(kw)))
    desktop_mod.main(["--browser"])
    assert recorded == [{"native_window": False}]


def test_desktop_main_check_desktop_verifies_webview(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import sys
    import types
    import qalprit.desktop as desktop_mod

    fake_webview = types.ModuleType("webview")
    fake_webview.__version__ = "6.2.1"  # type: ignore[attr-defined]
    fake_platforms = types.ModuleType("webview.platforms")
    fake_cocoa = types.ModuleType("webview.platforms.cocoa")
    fake_edge = types.ModuleType("webview.platforms.edgechromium")
    monkeypatch.setitem(sys.modules, "webview", fake_webview)
    monkeypatch.setitem(sys.modules, "webview.platforms", fake_platforms)
    monkeypatch.setitem(sys.modules, "webview.platforms.cocoa", fake_cocoa)
    monkeypatch.setitem(sys.modules, "webview.platforms.edgechromium", fake_edge)
    if sys.platform == "darwin":
        monkeypatch.setitem(sys.modules, "Cocoa", types.ModuleType("Cocoa"))
        monkeypatch.setitem(sys.modules, "WebKit", types.ModuleType("WebKit"))

    desktop_mod.main(["--check-desktop"])
    out = capsys.readouterr().out
    assert "Desktop WebView OK" in out


def test_build_appium_env_sets_xcode_developer_dir_on_darwin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from qalprit.appium_manager import build_appium_env

    fake_dev = tmp_path / "Xcode.app" / "Contents" / "Developer"
    fake_dev.mkdir(parents=True)
    monkeypatch.setattr("qalprit.appium_manager.platform.system", lambda: "Darwin")
    monkeypatch.setattr("qalprit.appium_manager.find_xcode_developer_dir", lambda _env=None: fake_dev)

    env = build_appium_env({"PATH": "/usr/bin:/bin", "DEVELOPER_DIR": "/Library/Developer/CommandLineTools"})
    assert env["DEVELOPER_DIR"] == str(fake_dev)


def test_launch_workbench_native_window_serves_and_stops_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys
    import types
    import webbrowser
    import qalprit.desktop as desktop_mod

    ws = ensure_workspace(tmp_path)
    events: dict[str, object] = {"create": None, "start": None, "http_status": None, "browser": 0}
    fake_webview = types.ModuleType("webview")
    fake_webview.settings = {}  # type: ignore[attr-defined]

    def _create_window(title: str, url: str, **kwargs: object) -> None:
        events["create"] = {"title": title, "url": url, **kwargs}

    def _start(**kwargs: object) -> None:
        events["start"] = kwargs
        url = events["create"]["url"]  # type: ignore[index]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"{url}/api/overview", timeout=5) as resp:
            events["http_status"] = resp.status

    fake_webview.create_window = _create_window  # type: ignore[attr-defined]
    fake_webview.start = _start  # type: ignore[attr-defined]

    monkeypatch.delenv("QALPRIT_BROWSER", raising=False)
    monkeypatch.setitem(sys.modules, "webview", fake_webview)
    monkeypatch.setattr(webbrowser, "open", lambda _url: events.__setitem__("browser", int(events["browser"]) + 1))

    desktop_mod.launch_workbench(ws, port=0, open_browser=True, native_window=True)

    assert events["http_status"] == 200
    assert events["browser"] == 0
    assert fake_webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] is True  # type: ignore[index]
    assert events["start"]["private_mode"] is False  # type: ignore[index]
    assert events["start"]["storage_path"] == str(ws / ".webview")  # type: ignore[index]
    assert (ws / ".webview").is_dir()


def test_native_window_falls_back_when_webview_errors_or_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys
    import types
    import qalprit.desktop as desktop_mod

    fake_webview = types.ModuleType("webview")
    fake_webview.settings = {}  # type: ignore[attr-defined]
    fake_webview.create_window = lambda *a, **kw: None  # type: ignore[attr-defined]

    def _boom(**_kwargs: object) -> None:
        raise RuntimeError("WebView2 runtime missing")

    fake_webview.start = _boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "webview", fake_webview)

    monkeypatch.setenv("QALPRIT_BROWSER", "1")
    assert desktop_mod._launch_native_window("http://127.0.0.1:8765", tmp_path) is False

    monkeypatch.delenv("QALPRIT_BROWSER", raising=False)
    assert desktop_mod._launch_native_window("http://127.0.0.1:8765", tmp_path) is False


def test_ensure_portable_appium_home_rewrites_stale_runner_paths(tmp_path: Path) -> None:
    import yaml
    from qalprit.doctor import ensure_portable_appium_home

    appium_home = tmp_path / "appium_home"
    driver_dir = appium_home / "node_modules" / "appium-uiautomator2-driver"
    driver_dir.mkdir(parents=True)
    ext_file = appium_home / "node_modules" / ".cache" / "appium" / "extensions.yaml"
    ext_file.parent.mkdir(parents=True)
    ext_file.write_text(
        yaml.safe_dump(
            {
                "drivers": {
                    "uiautomator2": {
                        "pkgName": "appium-uiautomator2-driver",
                        "installPath": r"D:\a\QAlprit\QAlprit\build\win-runtime\appium_home\node_modules\appium-uiautomator2-driver",
                    }
                },
                "plugins": {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    resolved = ensure_portable_appium_home(appium_home)
    assert resolved == appium_home
    updated = yaml.safe_load(ext_file.read_text(encoding="utf-8"))
    assert updated["drivers"]["uiautomator2"]["installPath"] == str(driver_dir.resolve())


def test_ensure_portable_appium_home_preserves_macos_app_bundle_seal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yaml
    from qalprit.doctor import ensure_portable_appium_home

    data_dir = tmp_path / "app_data"
    monkeypatch.setenv("QALPRIT_DATA_DIR", str(data_dir))

    app_v1_home = tmp_path / "QAlprit.app" / "Contents" / "Resources" / "runtime" / "appium_home"
    driver_v1 = app_v1_home / "node_modules" / "appium-xcuitest-driver"
    driver_v1.mkdir(parents=True)
    ext_v1 = app_v1_home / "node_modules" / ".cache" / "appium" / "extensions.yaml"
    ext_v1.parent.mkdir(parents=True)
    original_yaml = yaml.safe_dump(
        {
            "drivers": {
                "xcuitest": {
                    "pkgName": "appium-xcuitest-driver",
                    "installPath": "/Users/runner/work/QAlprit/QAlprit/build/mac-runtime-arm64/appium_home/node_modules/appium-xcuitest-driver",
                }
            },
            "plugins": {},
        },
        sort_keys=False,
    )
    ext_v1.write_text(original_yaml, encoding="utf-8")

    resolved_v1 = ensure_portable_appium_home(app_v1_home)
    assert resolved_v1 == (data_dir / "appium_home").resolve()
    assert ext_v1.read_text(encoding="utf-8") == original_yaml
    assert (resolved_v1 / "node_modules" / "appium-xcuitest-driver").resolve() == driver_v1.resolve()
    fallback_yaml_v1 = yaml.safe_load(
        (resolved_v1 / "node_modules" / ".cache" / "appium" / "extensions.yaml").read_text(encoding="utf-8")
    )
    assert fallback_yaml_v1["drivers"]["xcuitest"]["installPath"] == str(driver_v1.resolve())

    # Simulate upgrading QAlprit.app to a different location; stale symlinks must refresh cleanly
    app_v2_home = tmp_path / "Upgraded" / "QAlprit.app" / "Contents" / "Resources" / "runtime" / "appium_home"
    driver_v2 = app_v2_home / "node_modules" / "appium-xcuitest-driver"
    driver_v2.mkdir(parents=True)
    ext_v2 = app_v2_home / "node_modules" / ".cache" / "appium" / "extensions.yaml"
    ext_v2.parent.mkdir(parents=True)
    ext_v2.write_text(original_yaml, encoding="utf-8")

    resolved_v2 = ensure_portable_appium_home(app_v2_home)
    assert resolved_v2 == (data_dir / "appium_home").resolve()
    assert ext_v2.read_text(encoding="utf-8") == original_yaml
    assert (resolved_v2 / "node_modules" / "appium-xcuitest-driver").resolve() == driver_v2.resolve()
    fallback_yaml_v2 = yaml.safe_load(
        (resolved_v2 / "node_modules" / ".cache" / "appium" / "extensions.yaml").read_text(encoding="utf-8")
    )
    assert fallback_yaml_v2["drivers"]["xcuitest"]["installPath"] == str(driver_v2.resolve())


def test_save_uploaded_app_extracts_package_from_binary_android_manifest(tmp_path: Path) -> None:
    import io
    import struct
    import zipfile

    ws = ensure_workspace(tmp_path)
    strings = ["manifest", "package", "com.acme.sdk5"]
    pool_blob = bytearray()
    offsets: list[int] = []
    for s in strings:
        offsets.append(len(pool_blob))
        enc = s.encode("utf-8")
        pool_blob.extend(bytes([len(s), len(enc)]))
        pool_blob.extend(enc)
        pool_blob.append(0)
    while len(pool_blob) % 4:
        pool_blob.append(0)

    sp_hdr_size = 28
    strings_start = sp_hdr_size + 4 * len(strings)
    sp_chunk = (
        struct.pack("<HHIIIIII", 0x0001, sp_hdr_size, strings_start + len(pool_blob), len(strings), 0, 0x0100, strings_start, 0)
        + b"".join(struct.pack("<I", off) for off in offsets)
        + bytes(pool_blob)
    )
    attr = struct.pack("<IIIHBBI", 0xFFFFFFFF, 1, 2, 8, 0, 3, 2)
    node_hdr = struct.pack("<HHII", 0x0102, 16, 16 + 20 + len(attr), 1) + struct.pack("<I", 0xFFFFFFFF)
    attr_ext = struct.pack("<IIHHHHHH", 0xFFFFFFFF, 0, 20, 20, 1, 0, 0, 0)
    start_chunk = node_hdr + attr_ext + attr
    axml = struct.pack("<HHI", 0x0003, 8, 8 + len(sp_chunk) + len(start_chunk)) + sp_chunk + start_chunk

    apk_buf = io.BytesIO()
    with zipfile.ZipFile(apk_buf, "w") as zf:
        zf.writestr("AndroidManifest.xml", axml)

    uploaded = save_uploaded_app(ws, "sdk5-mobile.apk", apk_buf.getvalue())
    assert uploaded["detected_package"] == "com.acme.sdk5"


def test_run_job_manager_stop_transitions_idle_and_active() -> None:
    mgr = RunJobManager()
    assert mgr.stop() == {"ok": True, "running": False, "status": "idle"}

    exited: list[bool] = []

    class _FakeAppium:
        def __exit__(self, *_args: object) -> None:
            exited.append(True)

    with mgr._lock:
        mgr._state["running"] = True
        mgr._state["status"] = "running"
        mgr._state["run_id"] = "RUN-TEST"
        mgr._active_mgr = _FakeAppium()  # type: ignore[assignment]

    res = mgr.stop()
    assert res == {"ok": True, "running": True, "status": "stopping"}
    assert exited == [True]
    st = mgr.status()
    assert st["status"] == "stopping"
    assert st["phase"] == "Stopping"


def test_resolve_live_device_profile_handles_usb_and_emulator() -> None:
    from qalprit.workbench_api import _resolve_device_profile

    usb = _resolve_device_profile({}, "live:android:47031FDAP004CK", "android")
    assert usb.platform == "android"
    assert usb.target_type == "device"
    assert usb.udid == "47031FDAP004CK"
    assert usb.automation_name == "UiAutomator2"

    emu = _resolve_device_profile({}, "live:android:emulator-5554", "android")
    assert emu.target_type == "simulator"
    assert emu.udid == "emulator-5554"

    with pytest.raises(ValueError, match="not 'ios'"):
        _resolve_device_profile({}, "live:android:47031FDAP004CK", "ios")


def test_doctor_checks_appium_python_client_metadata() -> None:
    from qalprit.doctor import run_doctor

    checks = {c.name: c for c in run_doctor()}
    assert "Appium Python client" in checks
    assert checks["Appium Python client"].status == "ok"
    assert checks["Appium Python client"].detail.startswith("v")


def test_find_android_tool_and_appium_env_include_build_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import platform
    import qalprit.doctor as doctor_mod
    from qalprit.appium_manager import build_appium_env
    from qalprit.doctor import find_android_tool

    sdk = tmp_path / "android-sdk"
    pt = sdk / "platform-tools"
    bt_old = sdk / "build-tools" / "34.0.0"
    bt_new = sdk / "build-tools" / "35.0.1"
    for d in (pt, bt_old, bt_new):
        d.mkdir(parents=True)
    suffix = ".exe" if platform.system() == "Windows" else ""
    (bt_old / f"aapt2{suffix}").write_text("old", encoding="utf-8")
    (bt_new / f"aapt2{suffix}").write_text("new", encoding="utf-8")

    monkeypatch.setattr(doctor_mod.shutil, "which", lambda _name: None)
    monkeypatch.setattr(doctor_mod, "find_android_sdk", lambda: sdk)
    monkeypatch.setattr("qalprit.appium_manager.find_android_sdk", lambda: sdk)

    resolved = find_android_tool("aapt2")
    assert resolved == str(bt_new / f"aapt2{suffix}")

    env = build_appium_env({})
    assert str(pt) in env["PATH"]
    assert str(bt_new) in env["PATH"]


def test_prepare_writable_android_sdk_and_find_sdkmanager(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import qalprit.doctor as doctor_mod

    fake_home = tmp_path / "home"
    rt = tmp_path / "runtime"
    ct_bin = rt / "android-sdk" / "cmdline-tools" / "latest" / "bin"
    pt_dir = rt / "android-sdk" / "platform-tools"
    ct_bin.mkdir(parents=True)
    pt_dir.mkdir(parents=True)
    (ct_bin / "sdkmanager").write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    (pt_dir / "adb").write_text("#!/bin/sh\necho ok\n", encoding="utf-8")

    monkeypatch.setattr(Path, "home", lambda: fake_home)
    for var in ("QALPRIT_DATA_DIR", "QALPRIT_SDK_DIR", "QALPRIT_SETTINGS_PATH", "ANDROID_HOME", "ANDROID_SDK_ROOT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("QALPRIT_RUNTIME_DIR", str(rt))
    monkeypatch.setattr(doctor_mod.shutil, "which", lambda _n: None)

    assert doctor_mod.find_android_tool("sdkmanager") == str(ct_bin / "sdkmanager")

    user_sdk = doctor_mod.prepare_writable_android_sdk()
    assert user_sdk == (doctor_mod.get_os_app_data_root() / "android-sdk").resolve()
    assert (user_sdk / "cmdline-tools" / "latest" / "bin" / "sdkmanager").exists()

    # Once emulator dir exists under standard app data android-sdk, find_android_sdk prefers it cleanly
    (user_sdk / "emulator").mkdir(parents=True)
    assert doctor_mod.find_android_sdk() == user_sdk


def test_emulator_setup_sync_and_http_status_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json
    import threading
    import urllib.request
    import qalprit.emulator_manager as emu_mod
    from qalprit.ui_server import create_ui_server

    fake_sdk = tmp_path / "sdk"
    fake_sdk.mkdir()
    monkeypatch.setattr(emu_mod, "prepare_writable_android_sdk", lambda: fake_sdk)
    monkeypatch.setattr(emu_mod, "build_appium_env", lambda e=None: dict(e or {}))
    monkeypatch.setattr(
        emu_mod,
        "find_android_tool",
        lambda name: f"/fake/{name}" if name in {"sdkmanager", "avdmanager"} else None,
    )

    recorded_cmds: list[list[str]] = []

    def _fake_stream(cmd: list[str], **_kwargs: object) -> tuple[int, str]:
        recorded_cmds.append(list(cmd))
        on_line = _kwargs.get("on_line")
        if "--install" in cmd:
            pkg = cmd[-1]
            img_dir = fake_sdk / Path(*pkg.split(";"))
            img_dir.mkdir(parents=True, exist_ok=True)
            (img_dir / "source.properties").write_text("Pkg.Revision=14\n", encoding="utf-8")
            if callable(on_line):
                on_line("[======         ] 50% Downloading emulator...")
        return 0, "done"

    monkeypatch.setattr(emu_mod, "_stream_command", _fake_stream)

    progress_events: list[tuple[str, int]] = []
    res = emu_mod.setup_android_emulator_sync(
        "QAlprit_Pixel",
        on_progress=lambda ph, pct, _msg: progress_events.append((ph, pct)),
    )
    assert res["ok"] is True
    assert res["avd_name"] == "QAlprit_Pixel"
    assert any("--licenses" in c for c in recorded_cmds)
    assert any("--install" in c and "emulator" in c for c in recorded_cmds)
    assert any("create" in c and "QAlprit_Pixel" in c for c in recorded_cmds)
    assert any(pct == 100 for _, pct in progress_events)

    # HTTP /api/emulator/status endpoint
    ws = ensure_workspace(tmp_path / "ws")
    server = create_ui_server(ws, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/emulator/status") as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        assert "env_status" in payload
        assert payload["env_status"]["default_avd"] == "QAlprit_Pixel"
    finally:
        server.shutdown()
        server.server_close()


def test_windows_sdk_sync_copies_real_dirs_and_heals_reparse_points(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import qalprit.doctor as doctor_mod

    src = tmp_path / "bundled" / "platform-tools"
    src.mkdir(parents=True)
    (src / "adb.exe").write_text("adb", encoding="utf-8")

    dst = tmp_path / "user-sdk" / "platform-tools"
    dst.parent.mkdir(parents=True)
    # Simulate an existing stale symlink/reparse point left by v0.1.11
    stale_target = tmp_path / "old-bundled" / "platform-tools"
    stale_target.mkdir(parents=True)
    dst.symlink_to(stale_target)

    monkeypatch.setattr(doctor_mod.platform, "system", lambda: "Windows")
    ran_subprocess = []
    monkeypatch.setattr(doctor_mod.subprocess, "run", lambda *a, **kw: ran_subprocess.append(a))

    doctor_mod._link_or_copy_dir(src, dst)
    assert ran_subprocess == []  # Never invokes mklink /J
    assert not dst.is_symlink()
    assert (dst / "adb.exe").read_text(encoding="utf-8") == "adb"


def test_stream_command_wraps_bat_files_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    import qalprit.emulator_manager as emu_mod

    recorded: list[list[str] | str] = []

    class _FakeProc:
        stdout = None
        stdin = None

        def poll(self) -> int:
            return 0

        def wait(self) -> int:
            return 0

    def _fake_popen(cmd: list[str] | str, **_kwargs: object) -> _FakeProc:
        recorded.append(cmd)
        return _FakeProc()

    monkeypatch.setattr(emu_mod.platform, "system", lambda: "Windows")
    monkeypatch.setattr(emu_mod.subprocess, "Popen", _fake_popen)
    rc, _ = emu_mod._stream_command(
        [
            r"C:\Users\First Last\.qalprit\android-sdk\cmdline-tools\latest\bin\sdkmanager.bat",
            r"--sdk_root=C:\Users\First Last\.qalprit\android-sdk",
            "--install",
            "emulator",
            "system-images;android-34;google_apis;x86_64",
        ],
        env={},
    )
    assert rc == 0
    assert isinstance(recorded[0], str)
    assert recorded[0].startswith('cmd.exe /d /s /c "')
    assert r'"--sdk_root=C:\Users\First Last\.qalprit\android-sdk"' in recorded[0]
    assert '"system-images;android-34;google_apis;x86_64"' in recorded[0]


def test_setup_android_emulator_fails_early_when_system_image_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import qalprit.emulator_manager as emu_mod

    fake_sdk = tmp_path / "sdk"
    fake_sdk.mkdir()
    monkeypatch.setattr(emu_mod, "prepare_writable_android_sdk", lambda: fake_sdk)
    monkeypatch.setattr(emu_mod, "build_appium_env", lambda e=None: dict(e or {}))
    monkeypatch.setattr(
        emu_mod,
        "find_android_tool",
        lambda name: f"/fake/{name}" if name in {"sdkmanager", "avdmanager"} else None,
    )

    called: list[list[str]] = []

    def _fake_stream_no_img(cmd: list[str], **_kwargs: object) -> tuple[int, str]:
        called.append(list(cmd))
        return 0, "Warning: Package x86_64 not found."

    monkeypatch.setattr(emu_mod, "_stream_command", _fake_stream_no_img)

    with pytest.raises(RuntimeError, match="was not installed"):
        emu_mod.setup_android_emulator_sync("QAlprit_Pixel")
    assert not any("avdmanager" in c[0] for c in called)


def test_workspace_apps_detect_android_and_ios_packages_and_ui_layout(tmp_path: Path) -> None:
    import io
    import plistlib
    import zipfile
    from qalprit.workbench_api import list_workspace_apps

    ws = ensure_workspace(tmp_path)
    ios_buf = io.BytesIO()
    with zipfile.ZipFile(ios_buf, "w") as zf:
        zf.writestr(
            "Payload/DemoBank.app/Info.plist",
            plistlib.dumps({"CFBundleIdentifier": "com.demobank.ios"}),
        )
    up = save_uploaded_app(ws, "DemoBank.ipa", ios_buf.getvalue())
    assert up["detected_package"] == "com.demobank.ios"

    apps = list_workspace_apps(ws)
    assert len(apps) == 1
    assert apps[0]["detected_package"] == "com.demobank.ios"

    overview = collect_workspace_overview(ws)
    assert "host_os" in overview and overview["host_os"]

    ui_html = (Path(__file__).resolve().parents[1] / "src" / "qalprit" / "ui" / "index.html").read_text(
        encoding="utf-8"
    )
    assert "[hidden] { display: none !important; }" in ui_html
    assert ui_html.index("Step 1A") < ui_html.index("Step 1B") < ui_html.index("Step 1C")
    assert 'id="activeAppSelect"' in ui_html
    assert 'id="recStartForm"' in ui_html


def test_recorder_hit_test_diff_and_save_flow_and_spec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io
    import json
    import threading
    import time
    import urllib.request
    from PIL import Image
    import qalprit.recorder as rec_mod
    from qalprit.ui_server import create_ui_server
    from qalprit.validator import validate_project

    ws = ensure_workspace(tmp_path / "rec_ws")

    buf = io.BytesIO()
    Image.new("RGB", (1080, 2400), color=(30, 41, 59)).save(buf, format="PNG")
    fake_png = buf.getvalue()

    login_xml = (
        '<hierarchy>'
        '<android.widget.EditText resource-id="com.demo:id/et_01" hint="Email" text="" clickable="true" bounds="[60,300][1000,420]"/>'
        '<android.widget.Button resource-id="com.demo:id/btn_go" text="Sign In" clickable="true" bounds="[60,500][1000,620]"/>'
        '</hierarchy>'
    )
    home_xml = (
        '<hierarchy>'
        '<android.widget.TextView resource-id="com.demo:id/welcome" text="Welcome Home" bounds="[60,200][900,300]"/>'
        '</hierarchy>'
    )

    actions_log: list[tuple[str, str]] = []

    class _FakeEl:
        def __init__(self, name: str) -> None:
            self.name = name

        def click(self) -> None:
            actions_log.append(("click", self.name))

        def clear(self) -> None:
            actions_log.append(("clear", self.name))

        def send_keys(self, val: str) -> None:
            actions_log.append(("send_keys", val))

    class _FakeDriver:
        def __init__(self) -> None:
            self.xml = login_xml
            self.capabilities = {"deviceUDID": "emulator-5554"}

        @property
        def page_source(self) -> str:
            return self.xml

        def find_element(self, _strat: str, val: str) -> _FakeEl:
            if val == "com.demo:id/btn_go":
                self.xml = home_xml
            return _FakeEl(val)

        def get_screenshot_as_png(self) -> bytes:
            return fake_png

        def get_window_size(self) -> dict[str, int]:
            return {"width": 1080, "height": 2400}

        def quit(self) -> None:
            return

    class _FakeAppiumMgr:
        url = "http://127.0.0.1:4723"

        def __init__(self, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> "_FakeAppiumMgr":
            return self

        def __exit__(self, *_args: object) -> None:
            return

    monkeypatch.setattr(rec_mod, "AppiumManager", _FakeAppiumMgr)
    monkeypatch.setattr(rec_mod, "create_appium_driver", lambda *_a, **_kw: _FakeDriver())
    monkeypatch.setattr(rec_mod.RecorderManager, "_start_android_getevent_watcher", lambda *_a: None)

    server = create_ui_server(ws, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def _post(path: str, payload: dict[str, object]) -> dict[str, object]:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read().decode("utf-8"))

    try:
        _post("/api/recorder/start", {
            "platform": "android",
            "device_profile": "android_emulator",
            "env_name": "qa",
            "screen_name": "login",
        })
        for _ in range(100):
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/recorder/status") as r:
                st = json.loads(r.read().decode("utf-8"))
            if not st.get("starting"):
                break
            time.sleep(0.05)
        assert st["error"] == ""
        assert st["active"] is True
        assert st["elements"][0]["key"] == "email_input"

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/recorder/screenshot") as r:
            assert r.status == 200
            assert r.read()[:8] == b"\x89PNG\r\n\x1a\n"

        # 1. Type into Email input using coordinates inside [60,300][1000,420]
        st1 = _post("/api/recorder/action", {
            "action": "type",
            "x": 200,
            "y": 350,
            "value": "qa@example.com",
            "screen_name": "login",
        })
        assert len(st1["steps"]) == 1

        # 2. Tap Sign In button using coordinates inside [60,500][1000,620]
        st2 = _post("/api/recorder/action", {
            "action": "tap",
            "x": 300,
            "y": 550,
            "screen_name": "login",
        })
        assert len(st2["steps"]) == 2
        assert st2["elements"][0]["key"] == "welcome_home"

        # 3. Assert Welcome Home visible on home screen
        st3 = _post("/api/recorder/action", {
            "action": "assert_visible",
            "element_index": 0,
            "screen_name": "home",
        })
        assert len(st3["steps"]) == 3

        # 4. Save workflow + repeatable test journey and validate workspace cleanly
        saved = _post("/api/recorder/save", {
            "flow_name": "auth/recorded_login",
            "suite": "smoke",
            "test_id": "REC-101",
            "test_title": "Recorded login test",
            "create_test": True,
            "stop_after": True,
        })
        assert saved["ok"] is True
        assert saved["flow"]["name"] == "auth/recorded_login"
        assert saved["spec"]["id"] == "REC-101"

        val = validate_project(ws, env_name="qa", platform="android")
        assert val.ok is True, f"Expected recorded project validation to pass: {val.errors}"
    finally:
        server.shutdown()
        server.server_close()

    # Check direct-emulator changed editable input detection helper
    changed = rec_mod.detect_changed_editable_inputs(
        [{"recommended_strategy": "id", "recommended_value": "com.demo:id/email", "editable": True, "text": "", "hint": "Email"}],
        [{"recommended_strategy": "id", "recommended_value": "com.demo:id/email", "editable": True, "text": "alice@demo.com", "hint": "Email"}],
    )
    assert len(changed) == 1 and changed[0][1] == "alice@demo.com"


def test_recorder_scales_ios_retina_rects_redacts_and_swipes_in_points(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io
    import time
    from PIL import Image
    import qalprit.recorder as rec_mod

    ws = ensure_workspace(tmp_path / "ios_ret_ws")
    # 3x Retina iPhone screenshot (1206x2622 px) vs XCUITest logical points (402x874 pt)
    buf = io.BytesIO()
    Image.new("RGB", (1206, 2622), color=(240, 240, 240)).save(buf, format="PNG")
    retina_png = buf.getvalue()

    ios_xml = (
        '<AppiumAUT>'
        '<XCUIElementTypeApplication name="My Demo App" x="0" y="0" width="402" height="874">'
        '<XCUIElementTypeSecureTextField name="Password" x="20" y="300" width="362" height="44" enabled="true" visible="true" />'
        '<XCUIElementTypeButton name="Add To Cart" label="Add To Cart" x="60" y="750" width="282" height="50" enabled="true" visible="true" />'
        '</XCUIElementTypeApplication>'
        '</AppiumAUT>'
    )
    swipes: list[tuple[int, int, int, int]] = []
    clicks: list[str] = []

    class _FakeEl:
        def __init__(self, val: str) -> None:
            self.val = val

        def click(self) -> None:
            clicks.append(self.val)

    class _FakeDriver:
        capabilities = {"udid": "B8AF4FAF-1864-4D6D-B67F-D17F7822F4A8"}

        @property
        def page_source(self) -> str:
            return ios_xml

        def find_element(self, _strat: str, val: str) -> _FakeEl:
            return _FakeEl(val)

        def get_screenshot_as_png(self) -> bytes:
            return retina_png

        def get_window_size(self) -> dict[str, int]:
            return {"width": 402, "height": 874}

        def swipe(self, sx: int, sy: int, ex: int, ey: int, _dur: int = 300) -> None:
            swipes.append((sx, sy, ex, ey))

        def quit(self) -> None:
            return

    class _FakeAppiumMgr:
        url = "http://127.0.0.1:4723"

        def __init__(self, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> "_FakeAppiumMgr":
            return self

        def __exit__(self, *_args: object) -> None:
            return

    monkeypatch.setattr(rec_mod, "AppiumManager", _FakeAppiumMgr)
    monkeypatch.setattr(rec_mod, "create_appium_driver", lambda *_a, **_kw: _FakeDriver())

    mgr = rec_mod.RecorderManager()
    mgr.start(ws, platform="ios", device_profile="ios_simulator", env_name="qa", redact_sensitive=True)
    try:
        deadline = time.monotonic() + 5.0
        while not mgr.status()["active"] and time.monotonic() < deadline:
            time.sleep(0.05)
        st = mgr.status()
        assert st["active"] is True
        assert st["screen_width"] == 1206
        assert st["screen_height"] == 2622

        by_val = {el["recommended_value"]: el for el in st["elements"]}
        assert by_val["Add To Cart"]["rect"] == [180, 2250, 1026, 2400]
        assert by_val["Password"]["rect"] == [60, 900, 1146, 1032]

        # Live mirror PNG redaction must cover Retina pixels (600, 960), not 1x corner (200, 320)
        with Image.open(io.BytesIO(mgr.screenshot_bytes())) as im:
            assert im.getpixel((600, 960))[:3] == (15, 23, 42)
            assert im.getpixel((200, 320))[:3] == (240, 240, 240)

        # Clicking button pixel coordinates (600, 2325) must hit-test & tap Add To Cart
        st_tap = mgr.perform_action(action="tap", x=600, y=2325, screen_name="product")
        assert clicks == ["Add To Cart"]
        assert st_tap["steps"][-1]["action"] == "tap"

        # Swipes must use logical iOS window points (402x874), never raw Retina pixels
        mgr.perform_action(action="swipe", direction="up")
        assert swipes == [(201, int(874 * 0.72), 201, int(874 * 0.28))]
    finally:
        mgr.stop()


def test_overview_reflects_external_flow_md_edits_and_parse_warnings(tmp_path: Path) -> None:
    import json
    import threading
    import urllib.request
    from qalprit.ui_server import create_ui_server

    ws = ensure_workspace(tmp_path / "reload_ws")
    flow_file = ws / "flows" / "quick_nav.md"
    flow_file.write_text(
        "---\nname: quick_nav\nsteps:\n  - back\n---\n\n# quick_nav\n",
        encoding="utf-8",
    )

    server = create_ui_server(ws, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as r:
            html = r.read().decode("utf-8")
        assert 'id="btnReloadWorkspace"' in html
        assert 'data-reload-workspace="flows"' in html
        assert "scheduleSilentWorkspaceReload" in html

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/overview") as r:
            assert r.headers.get("Cache-Control") == "no-store"
            ov1 = json.loads(r.read().decode("utf-8"))
        assert ov1["flows"][0]["step_count"] == 1

        # Modify flows/quick_nav.md externally on disk without restarting server
        flow_file.write_text(
            "---\nname: quick_nav\nsteps:\n  - back\n  - wait: 1\n  - screenshot: done\n---\n\n# quick_nav\n",
            encoding="utf-8",
        )
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/overview") as r:
            ov2 = json.loads(r.read().decode("utf-8"))
        assert ov2["flows"][0]["step_count"] == 3
        assert ov2["parse_warnings"] == []

        # Introduce a YAML syntax error externally and verify parse_warnings surfaces it
        bad_flow = ws / "flows" / "broken.md"
        bad_flow.write_text("---\nname: [unclosed\n---\n", encoding="utf-8")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/overview") as r:
            ov3 = json.loads(r.read().decode("utf-8"))
        assert any("broken.md" in w for w in ov3["parse_warnings"])
    finally:
        server.shutdown()
        server.server_close()


def test_emulator_setup_if_missing_skips_and_installer_has_upgrade_checks(monkeypatch) -> None:
    import qalprit.emulator_manager as em

    monkeypatch.setattr(
        em,
        "get_emulator_status",
        lambda: {
            "os": "Windows",
            "sdk_dir": "C:/Users/demo/.qalprit/android-sdk",
            "sdkmanager_ready": True,
            "avdmanager_ready": True,
            "emulator_installed": True,
            "system_image_installed": True,
            "system_image_package": em.system_image_package(),
            "default_avd": em.DEFAULT_AVD_NAME,
            "avds": [em.DEFAULT_AVD_NAME],
            "running_android_emulators": [],
            "ios_supported": False,
            "ios_simulators": [],
        },
    )
    res = em.setup_android_emulator_sync(if_missing=True)
    assert res["ok"] is True
    assert res["skipped"] is True

    iss = Path(__file__).resolve().parents[1] / "packaging" / "windows" / "installer.iss"
    iss_text = iss.read_text(encoding="utf-8")
    assert "reinstall_runtime" in iss_text
    assert "NeedsNodeRuntime" in iss_text
    assert "NeedsEmulatorSetup" in iss_text
    assert "--if-missing" in iss_text


def test_streamlined_ui_and_workflow_spec_crud_endpoints(tmp_path: Path, monkeypatch) -> None:
    import json
    import subprocess
    import threading
    import urllib.request
    from qalprit.ui_server import create_ui_server

    ws = ensure_workspace(tmp_path / "crud_ws")
    popens: list[list[str]] = []

    class _DummyProc:
        pass

    def _fake_popen(cmd, *args, **kwargs):
        popens.append(list(cmd))
        return _DummyProc()

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)

    server = create_ui_server(ws, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def _post_json(path: str, payload: dict) -> dict:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read().decode("utf-8"))

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as r:
            html = r.read().decode("utf-8")
        assert "Record App Flow" in html
        assert 'id="btnOpenFlowsModal"' in html
        assert 'id="recAssertBar"' in html
        assert 'id="flowUploadStep2Form"' in html
        assert 'id="specUploadInput"' in html
        assert 'id="btnAutoSpecId"' in html
        assert 'id="runSpecFilter"' in html

        # Auto-ID starts at SMK-001
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/specs/next-id?suite=smoke") as r:
            nid1 = json.loads(r.read().decode("utf-8"))
        assert nid1["id"] == "SMK-001"

        # Upload workflow markdown -> duplicate -> delete
        up_flow = _post_json(
            "/api/flows/upload",
            {
                "filename": "login_error_check.md",
                "content_text": "---\nname: login_error_check\nsteps:\n  - back\n---\n# Flow\n",
            },
        )
        assert up_flow["ok"] is True
        assert up_flow["name"] == "login_error_check"

        dup_flow = _post_json(
            "/api/flows/duplicate",
            {"name": "login_error_check", "new_name": "login_error_check_v2"},
        )
        assert dup_flow["ok"] is True
        assert (ws / "flows" / "login_error_check_v2.md").is_file()

        del_flow = _post_json("/api/flows/delete", {"name": "login_error_check_v2"})
        assert del_flow["ok"] is True
        assert not (ws / "flows" / "login_error_check_v2.md").exists()

        # Upload journey markdown -> next-id increments -> duplicate -> delete
        up_spec = _post_json(
            "/api/specs/upload",
            {
                "filename": "SMK-001.md",
                "suite": "smoke",
                "content_text": "---\nid: SMK-001\ntitle: Missing Password Error\nsteps:\n  - back\n---\n# SMK-001\n",
            },
        )
        assert up_spec["ok"] is True
        assert up_spec["id"] == "SMK-001"

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/specs/next-id?suite=smoke") as r:
            nid2 = json.loads(r.read().decode("utf-8"))
        assert nid2["id"] == "SMK-002"

        dup_spec = _post_json(
            "/api/specs/duplicate",
            {"source_path": up_spec["source_path"], "suite": "smoke", "new_id": "SMK-002"},
        )
        assert dup_spec["ok"] is True
        assert (ws / "specs" / "smoke" / "SMK-002.md").is_file()

        del_spec = _post_json("/api/specs/delete", {"source_path": dup_spec["source_path"]})
        assert del_spec["ok"] is True
        assert not (ws / "specs" / "smoke" / "SMK-002.md").exists()

        # Even if an unrelated spec in 'smoke' references a missing flow, running spec_id='SMK-001' succeeds
        (ws / "specs" / "smoke" / "SMK-999.md").write_text(
            "---\nid: SMK-999\ntitle: Broken unrelated test\nsteps:\n  - run_flow: mda_auth_login\n---\n",
            encoding="utf-8",
        )
        dry = _post_json(
            "/api/runs/start",
            {
                "platform": "android",
                "device_profile": "android_emulator",
                "env_name": "qa",
                "suite": "smoke",
                "spec_id": "SMK-001",
                "dry_run": True,
            },
        )
        assert dry["dry_run"] is True
        assert [s["id"] for s in dry["specs"]] == ["SMK-001"]

        # Reveal workspace folder calls platform file manager command safely
        rev = _post_json("/api/workspace/reveal", {"rel_path": "specs"})
        assert rev["ok"] is True
        assert popens
    finally:
        server.shutdown()
        server.server_close()


def test_os_standard_paths_and_legacy_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import qalprit.desktop as desktop_mod
    import qalprit.paths as paths_mod

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    for var in (
        "QALPRIT_DATA_DIR",
        "QALPRIT_SDK_DIR",
        "QALPRIT_SETTINGS_PATH",
        "QALPRIT_WORKSPACE",
        "ANDROID_AVD_HOME",
        "ANDROID_USER_HOME",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)

    # Fresh install uses OS standard folders instead of polluting ~ with .qalprit / .android / QAlpritWorkspace
    ws_std = desktop_mod.resolve_default_workspace()
    assert ws_std == (fake_home / "Documents" / "QAlpritWorkspace").resolve()
    avd_std = paths_mod.get_qalprit_avd_home()
    assert avd_std == (paths_mod.get_os_app_data_root() / "avd").resolve()
    assert paths_mod.get_android_user_home() == (paths_mod.get_os_app_data_root() / "android-home").resolve()

    # Existing legacy ~/QAlpritWorkspace and ~/.qalprit/android-sdk are preserved automatically
    legacy_ws = (fake_home / "QAlpritWorkspace").resolve()
    legacy_ws.mkdir()
    assert desktop_mod.resolve_default_workspace() == legacy_ws

    legacy_sdk = (fake_home / ".qalprit" / "android-sdk" / "emulator").resolve()
    legacy_sdk.mkdir(parents=True)
    assert paths_mod.get_user_android_sdk_dir() == (fake_home / ".qalprit" / "android-sdk").resolve()

    iss_text = (Path(__file__).resolve().parents[1] / "packaging" / "windows" / "installer.iss").read_text(encoding="utf-8")
    assert "ScanPage" in iss_text
    assert "StoragePage" in iss_text
    assert "SaveQAlpritSettings" in iss_text
    assert "{userdocs}\\QAlpritWorkspace" in iss_text


def test_system_preflight_and_storage_preferences_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json
    import threading
    import urllib.request
    from qalprit.ui_server import create_ui_server

    ws1 = ensure_workspace(tmp_path / "ws1")
    ws2 = (tmp_path / "ws2").resolve()
    custom_data = (tmp_path / "qalprit_data").resolve()
    settings_file = (tmp_path / "settings.json").resolve()
    monkeypatch.setenv("QALPRIT_SETTINGS_PATH", str(settings_file))
    monkeypatch.delenv("QALPRIT_DATA_DIR", raising=False)

    server = create_ui_server(ws1, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as r:
            html = r.read().decode("utf-8")
        assert 'id="storagePreflightCard"' in html
        assert 'id="prefWorkspaceDirInput"' in html
        assert 'id="prefDataDirInput"' in html
        assert 'id="prefReuseSystemSdk"' in html

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/system/preflight") as r:
            pf = json.loads(r.read().decode("utf-8"))
        ids = {item["id"] for item in pf["software"]}
        assert {"node", "python", "java", "appium", "android_sdk", "android_avd"}.issubset(ids)

        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/system/preferences",
            data=json.dumps({
                "workspace_dir": str(ws2),
                "data_dir": str(custom_data),
                "prefer_existing_system_sdk": True,
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as r:
            saved = json.loads(r.read().decode("utf-8"))
        assert saved["ok"] is True
        assert saved["workspace_dir"] == str(ws2)
        assert custom_data.is_dir()
        assert settings_file.is_file()

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/overview") as r:
            ov = json.loads(r.read().decode("utf-8"))
        assert ov["project_root"] == str(ws2)
    finally:
        server.shutdown()
        server.server_close()


def test_selective_cleanup_plan_execution_and_uninstaller_controls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json
    import threading
    import urllib.request
    from qalprit.ui_server import create_ui_server

    fake_home = tmp_path / "home"
    active_data = tmp_path / "app_data" / "QAlprit"
    active_ws = ensure_workspace(fake_home / "Documents" / "QAlpritWorkspace")
    settings_file = active_data / "settings.json"
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.setenv("QALPRIT_DATA_DIR", str(active_data))
    monkeypatch.setenv("QALPRIT_SETTINGS_PATH", str(settings_file))
    monkeypatch.delenv("QALPRIT_SDK_DIR", raising=False)
    monkeypatch.delenv("ANDROID_AVD_HOME", raising=False)

    # Populate active SDK + active AVD so legacy folders are flagged safe leftovers
    (active_data / "android-sdk" / "emulator").mkdir(parents=True)
    (active_data / "android-sdk" / "emulator" / "emu.bin").write_bytes(b"12345")
    (active_data / "avd" / "QAlprit_Pixel.avd").mkdir(parents=True)
    (active_data / "avd" / "QAlprit_Pixel.avd" / "config.ini").write_text("hw=1\n", encoding="utf-8")

    # Populate legacy leftovers + unrelated Android Studio AVD (must never be touched!)
    legacy_dot = fake_home / ".qalprit" / "android-sdk"
    legacy_dot.mkdir(parents=True)
    (legacy_dot / "old.bin").write_bytes(b"abcdefghij")
    legacy_pixel = fake_home / ".android" / "avd" / "QAlprit_Pixel.avd"
    legacy_pixel.mkdir(parents=True)
    (legacy_pixel / "config.ini").write_text("legacy\n", encoding="utf-8")
    (fake_home / ".android" / "avd" / "QAlprit_Pixel.ini").write_text("path=...\n", encoding="utf-8")
    studio_avd = fake_home / ".android" / "avd" / "Pixel_Studio_7.avd"
    studio_avd.mkdir(parents=True)
    (studio_avd / "config.ini").write_text("keepme\n", encoding="utf-8")

    server = create_ui_server(active_ws, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as r:
            html = r.read().decode("utf-8")
        assert 'id="cleanupTargetsBody"' in html
        assert 'id="btnCleanupSelected"' in html

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/system/cleanup") as r:
            plan = json.loads(r.read().decode("utf-8"))
        by_id = {it["id"]: it for it in plan["items"]}
        assert by_id["legacy_dot_qalprit"]["safe_for_new_install"] is True
        assert by_id["legacy_dot_qalprit"]["exists"] is True
        assert by_id["legacy_avd_qalprit_pixel"]["safe_for_new_install"] is True
        assert by_id["active_sdk"]["safe_for_new_install"] is False
        assert by_id["active_workspace"]["safe_for_new_install"] is False

        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/system/cleanup",
            data=json.dumps({"target_ids": ["legacy_dot_qalprit", "legacy_avd_qalprit_pixel"]}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as r:
            res = json.loads(r.read().decode("utf-8"))
        assert res["ok"] is True
        assert res["freed_bytes"] > 0
        assert not (fake_home / ".qalprit").exists()
        assert not legacy_pixel.exists()
        assert (studio_avd / "config.ini").is_file()
        assert (active_data / "android-sdk" / "emulator" / "emu.bin").is_file()
        assert (active_ws / "config" / "devices.yaml").is_file()
    finally:
        server.shutdown()
        server.server_close()

    iss_text = (Path(__file__).resolve().parents[1] / "packaging" / "windows" / "installer.iss").read_text(encoding="utf-8")
    assert "InitializeUninstall" in iss_text
    assert "CurUninstallStepChanged" in iss_text
    assert "CreateCustomForm(ScaleX(" in iss_text
    assert "ChkUnSdk" in iss_text
    assert "ChkUnWs" in iss_text
    scan_block = iss_text.split("procedure ScanSystemSoftware();", 1)[1].split("function ", 1)[0]
    assert "{app}" not in scan_block


def test_sdkmanager_prefers_modern_cmdline_tools_over_legacy_tools_bin(tmp_path: Path, monkeypatch) -> None:
    """Modern cmdline-tools (versioned or bundled) must always beat deprecated tools/bin/sdkmanager."""
    from qalprit import doctor
    from qalprit.paths import save_user_settings

    data_dir = tmp_path / "appdata"
    settings_path = data_dir / "settings.json"
    sys_sdk = tmp_path / "system-sdk"
    bundled_rt = tmp_path / "runtime"
    writable_sdk = data_dir / "android-sdk"

    (sys_sdk / "tools" / "bin").mkdir(parents=True)
    legacy_mgr = sys_sdk / "tools" / "bin" / "sdkmanager"
    legacy_mgr.write_text("#!/bin/sh\n", encoding="utf-8")

    bundled_ct_bin = bundled_rt / "android-sdk" / "cmdline-tools" / "latest" / "bin"
    bundled_ct_bin.mkdir(parents=True)
    bundled_mgr = bundled_ct_bin / "sdkmanager"
    bundled_mgr.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setenv("QALPRIT_DATA_DIR", str(data_dir))
    monkeypatch.setenv("QALPRIT_SETTINGS_PATH", str(settings_path))
    monkeypatch.setenv("ANDROID_HOME", str(sys_sdk))
    monkeypatch.setattr(doctor.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(doctor, "find_bundled_runtime_dir", lambda: bundled_rt)
    monkeypatch.setattr(doctor, "_system_android_sdk_candidates", lambda: [sys_sdk])
    save_user_settings({"prefer_existing_system_sdk": True})

    # Even with prefer_existing_system_sdk=True, legacy tools/bin must NOT beat bundled cmdline-tools
    resolved = doctor.find_android_tool("sdkmanager")
    assert resolved == str(bundled_mgr)
    assert doctor.find_cmdline_tool("sdkmanager", writable_sdk) == str(bundled_mgr)

    # And if the system SDK installs versioned cmdline-tools/13.0/bin, find_android_tool picks it cleanly
    ver_ct = sys_sdk / "cmdline-tools" / "13.0" / "bin"
    ver_ct.mkdir(parents=True)
    ver_mgr = ver_ct / "sdkmanager"
    ver_mgr.write_text("#!/bin/sh\n", encoding="utf-8")
    assert doctor.find_android_tool("sdkmanager") == str(ver_mgr)


def test_avd_sdk_matching_and_windows_adb_dll_health(tmp_path: Path, monkeypatch) -> None:
    """Incomplete system SDKs must not break AVD boot or return adb.exe missing AdbWinApi.dll."""
    from qalprit import doctor
    from qalprit.paths import save_user_settings

    data_dir = tmp_path / "appdata"
    settings_path = data_dir / "settings.json"
    sys_sdk = tmp_path / "Android" / "Sdk"
    qal_sdk = data_dir / "android-sdk"
    bundled_rt = tmp_path / "runtime"
    avd_dir = data_dir / "avd" / "QAlprit_Pixel.avd"

    (sys_sdk / "emulator").mkdir(parents=True)
    (sys_sdk / "platform-tools").mkdir(parents=True)
    # System SDK has adb.exe WITHOUT AdbWinApi.dll and NO system-images!
    (sys_sdk / "platform-tools" / "adb.exe").write_text("broken", encoding="utf-8")

    (qal_sdk / "emulator").mkdir(parents=True)
    sysimg = qal_sdk / "system-images" / "android-34" / "google_apis" / "x86_64"
    sysimg.mkdir(parents=True)
    (sysimg / "source.properties").write_text("Pkg.Revision=14\n", encoding="utf-8")

    bundled_pt = bundled_rt / "android-sdk" / "platform-tools"
    bundled_pt.mkdir(parents=True)
    (bundled_pt / "adb.exe").write_text("good", encoding="utf-8")
    (bundled_pt / "AdbWinApi.dll").write_text("dll", encoding="utf-8")
    (bundled_pt / "AdbWinUsbApi.dll").write_text("dll", encoding="utf-8")

    avd_dir.mkdir(parents=True)
    (avd_dir / "config.ini").write_text(
        "image.sysdir.1=system-images\\android-34\\google_apis\\x86_64\\\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("QALPRIT_DATA_DIR", str(data_dir))
    monkeypatch.setenv("QALPRIT_SETTINGS_PATH", str(settings_path))
    monkeypatch.setenv("ANDROID_HOME", str(sys_sdk))
    monkeypatch.setattr(doctor.platform, "system", lambda: "Windows")
    monkeypatch.setattr(doctor, "find_bundled_runtime_dir", lambda: bundled_rt)
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: None)
    save_user_settings({"prefer_existing_system_sdk": True})

    # Even with prefer_existing_system_sdk=True, incomplete system SDK does NOT beat populated qal_sdk
    assert doctor.find_android_sdk() == qal_sdk.resolve()
    assert doctor.find_sdk_for_avd("QAlprit_Pixel") == qal_sdk.resolve()

    # Broken adb.exe (without AdbWinApi.dll) is healed / skipped in favor of healthy AdbWinApi.dll copy
    adb_path = doctor.find_android_tool("adb")
    assert adb_path is not None
    assert (Path(adb_path).parent / "AdbWinApi.dll").is_file()

    iss_text = (Path(__file__).resolve().parents[1] / "packaging" / "windows" / "installer.iss").read_text(encoding="utf-8")
    assert "PrepareToInstall" in iss_text
    assert "AdbWinApi.dll" in iss_text


def test_recorder_and_environments_redaction_toggle_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import io
    import json
    import threading
    import time
    import urllib.request
    from PIL import Image
    import qalprit.recorder as rec_mod
    from qalprit.config_loader import load_environments
    from qalprit.ui_server import create_ui_server

    ws = ensure_workspace(tmp_path / "redact_ws")
    default_envs = load_environments(ws / "config" / "environments.yaml")
    assert default_envs["dev"].redact_screenshots is False
    assert default_envs["qa"].redact_screenshots is True
    assert default_envs["pre-prod"].redact_screenshots is True
    assert default_envs["prod"].redact_screenshots is True

    buf = io.BytesIO()
    Image.new("RGB", (1080, 2400), color=(240, 245, 250)).save(buf, format="PNG")
    clear_png = buf.getvalue()

    pw_xml = (
        '<hierarchy rotation="0">'
        '  <android.widget.EditText resource-id="com.demo:id/password_et" text="secret123" bounds="[60,300][1000,420]" clickable="true" />'
        '</hierarchy>'
    )

    class _FakeDriver:
        capabilities = {"deviceUDID": "emulator-5554"}
        page_source = pw_xml

        def get_screenshot_as_png(self) -> bytes:
            return clear_png

        def get_window_size(self) -> dict[str, int]:
            return {"width": 1080, "height": 2400}

        def quit(self) -> None:
            return

    class _FakeAppiumMgr:
        url = "http://127.0.0.1:4723"

        def __init__(self, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> "_FakeAppiumMgr":
            return self

        def __exit__(self, *_args: object) -> None:
            return

    monkeypatch.setattr(rec_mod, "AppiumManager", _FakeAppiumMgr)
    monkeypatch.setattr(rec_mod, "create_appium_driver", lambda *_a, **_kw: _FakeDriver())
    monkeypatch.setattr(rec_mod.RecorderManager, "_start_android_getevent_watcher", lambda *_a: None)

    server = create_ui_server(ws, host="127.0.0.1", port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def _post(path: str, payload: dict[str, object]) -> dict[str, object]:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read().decode("utf-8"))

    try:
        # 1. Saving pre-prod / dev via API exposes redact_screenshots cleanly
        saved = _post("/api/environments/save", {
            "env_name": "pre-prod",
            "android_package": "com.demo.preprod",
            "default_timeout": 12,
            "redact_screenshots": True,
        })
        assert saved["config"]["redact_screenshots"] is True

        # 2. Starting recorder in 'dev' defaults to clear unredacted frames
        _post("/api/recorder/start", {
            "platform": "android",
            "device_profile": "android_emulator",
            "env_name": "dev",
            "screen_name": "login",
        })
        for _ in range(100):
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/recorder/status") as r:
                st = json.loads(r.read().decode("utf-8"))
            if not st.get("starting"):
                break
            time.sleep(0.05)
        assert st["active"] is True
        assert st["redact_sensitive"] is False

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/recorder/screenshot") as r:
            with Image.open(io.BytesIO(r.read())) as im:
                assert im.convert("RGB").getpixel((200, 350)) == (240, 245, 250)

        # 3. Toggling black-marker redaction ON live immediately masks password box
        st_on = _post("/api/recorder/redaction", {"enabled": True})
        assert st_on["redact_sensitive"] is True
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/recorder/screenshot") as r:
            with Image.open(io.BytesIO(r.read())) as im:
                assert im.convert("RGB").getpixel((200, 350)) != (240, 245, 250)

        # 4. Toggling back OFF restores clear frame
        st_off = _post("/api/recorder/redaction", {"enabled": False})
        assert st_off["redact_sensitive"] is False
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/recorder/screenshot") as r:
            with Image.open(io.BytesIO(r.read())) as im:
                assert im.convert("RGB").getpixel((200, 350)) == (240, 245, 250)

        _post("/api/recorder/stop", {})
    finally:
        server.shutdown()
        server.server_close()
