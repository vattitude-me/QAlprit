"""Tests for QAlprit local workbench UI server and overview aggregation."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

from qalprit.results import RunResult, RunSummary, write_results_json
from qalprit.ui_server import collect_workspace_overview, create_ui_server


def test_ui_server_serves_overview_and_blocks_traversal(tmp_path: Path) -> None:
    (tmp_path / "screens").mkdir(parents=True)
    (tmp_path / "specs" / "smoke").mkdir(parents=True)
    (tmp_path / "flows" / "auth").mkdir(parents=True)
    (tmp_path / "screens" / "screens.yaml").write_text(
        "login:\n  password: {accessibility_id: pw, sensitive: true}\n",
        encoding="utf-8",
    )
    (tmp_path / "specs" / "smoke" / "demo.md").write_text(
        "---\nid: DEMO-1\ntitle: Demo spec\nsteps:\n  - tap: login.password\n---\n",
        encoding="utf-8",
    )

    run_dir = tmp_path / "results" / "20261113_demo"
    run_res = RunResult(
        run_id="20261113_demo",
        tool_version="0.1.0",
        started_at="2026-11-13T00:00:00Z",
        finished_at="2026-11-13T00:00:05Z",
        environment="qa",
        platform="android",
        device_profile="android_emulator",
        device_name="Pixel 8",
        target_type="simulator",
        suite="smoke",
        summary=RunSummary(total=1, passed=1),
    )
    write_results_json(run_res, run_dir / "results.json")
    (run_dir / "report.html").write_text("<h1>Report</h1>", encoding="utf-8")

    overview = collect_workspace_overview(tmp_path)
    assert len(overview["runs"]) == 1
    assert overview["runs"][0]["has_html"] is True
    assert overview["screens"][0]["sensitive"] is True
    assert overview["specs"][0]["id"] == "DEMO-1"

    server = create_ui_server(tmp_path, host="127.0.0.1", port=0)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as resp:
            assert resp.status == 200
            assert b"QAlprit Workbench" in resp.read()

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/qalprit-icon.svg") as resp:
            assert resp.status == 200
            assert "image/svg+xml" in resp.headers.get("Content-Type", "")
            assert b"QAlprit icon" in resp.read()

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/overview") as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            assert payload["runs"][0]["run_id"] == "20261113_demo"

        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/results/20261113_demo/report.html"
        ) as resp:
            assert b"<h1>Report</h1>" in resp.read()

        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{port}/results/../../screens/screens.yaml"
            )
            raise AssertionError("Expected 404 for path traversal")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        server.shutdown()
        server.server_close()
