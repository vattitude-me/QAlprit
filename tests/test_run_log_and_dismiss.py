from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

from qalprit.desktop import ensure_workspace
from qalprit.ui_server import create_ui_server
from qalprit.workbench_api import RunJobManager


def _failed(mgr: RunJobManager) -> None:
    with mgr._lock:
        mgr._state.update(
            running=False,
            run_id="20261123_205424_android_qa",
            status="error",
            phase="Error",
            logs=["[20261123_205424_android_qa] Starting", "[20261123_205424_android_qa] ERROR: The app could not be opened."],
            error="The app could not be opened.",
            error_detail="Cannot start the 'com.example' application. Stacktrace: at async ...",
        )


def test_log_text_has_logs_and_full_error() -> None:
    mgr = RunJobManager()
    _failed(mgr)
    name, text = mgr.log_text()
    assert name == "qalprit-20261123_205424_android_qa.log"
    assert "Status: error" in text
    assert "ERROR: The app could not be opened." in text
    assert "Error details:\nCannot start the 'com.example' application." in text


def test_clear_resets_finished_run_but_not_running_one() -> None:
    mgr = RunJobManager()
    _failed(mgr)
    assert mgr.clear() == {"ok": True, "running": False}
    st = mgr.status()
    assert st["status"] == "idle" and st["logs"] == [] and st["error"] == "" and st["error_detail"] == ""

    with mgr._lock:
        mgr._state.update(running=True, status="running", logs=["busy"])
    assert mgr.clear() == {"ok": False, "running": True}
    assert mgr.status()["logs"] == ["busy"]


def test_log_download_and_clear_routes(tmp_path: Path) -> None:
    ws = ensure_workspace(tmp_path / "ws")
    server = create_ui_server(ws, host="127.0.0.1", port=0)
    port = server.server_address[1]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        _failed(server.RequestHandlerClass.keywords["job_manager"])  # type: ignore[attr-defined]
        with opener.open(f"http://127.0.0.1:{port}/api/runs/log") as resp:
            assert "attachment" in resp.headers["Content-Disposition"]
            assert "qalprit-20261123_205424_android_qa.log" in resp.headers["Content-Disposition"]
            assert "Error details:" in resp.read().decode("utf-8")

        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/runs/clear", data=b"{}", headers={"Content-Type": "application/json"}, method="POST"
        )
        with opener.open(req) as resp:
            assert json.loads(resp.read())["ok"] is True
        with opener.open(f"http://127.0.0.1:{port}/api/runs/status") as resp:
            assert json.loads(resp.read())["status"] == "idle"
    finally:
        server.shutdown()
        server.server_close()
