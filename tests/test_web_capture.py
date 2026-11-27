"""Tests for tools/web-capture/web_capture.py.

The unit tests need only PyYAML. The end-to-end tests drive a small fake portal
with Playwright and are skipped when Playwright or its Chromium is missing.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tools" / "web-capture" / "web_capture.py"
if not TOOL.is_file():  # the sdist ships tests/ but not tools/
    pytest.skip("tools/web-capture is not in this checkout", allow_module_level=True)
spec = importlib.util.spec_from_file_location("web_capture", TOOL)
wc = importlib.util.module_from_spec(spec)
sys.modules["web_capture"] = wc
spec.loader.exec_module(wc)


# ---------------------------------------------------------------- unit tests

def test_load_env_file_handles_quotes_comments_and_export(tmp_path):
    env = tmp_path / ".env"
    env.write_text('# c\nexport A=1\nB="two words"\nC=\'x\'\n\nnot a line\n')
    assert wc.load_env_file(env) == {"A": "1", "B": "two words", "C": "x"}


def test_substitute_nested_and_unknown():
    out = wc.substitute({"a": ["${X}-1", {"b": "${Y}"}], "n": 3}, {"X": "x", "Y": 2})
    assert out == {"a": ["x-1", {"b": "2"}], "n": 3}
    with pytest.raises(wc.CaptureError, match=r"\$\{MISSING\} is not set"):
        wc.substitute("${MISSING}", {}, "CSV row 2")


def test_parse_row_selection():
    assert wc.parse_row_selection(None, 3) == [0, 1, 2]
    assert wc.parse_row_selection("1,3-4", 5) == [0, 2, 3]
    with pytest.raises(wc.CaptureError):
        wc.parse_row_selection("6", 5)


def test_validate_steps_catches_mistakes():
    wc.validate_steps([{"goto": "/"}, {"fill": "#q", "value": "x"}])
    with pytest.raises(wc.CaptureError, match="exactly one action"):
        wc.validate_steps([{"goto": "/", "click": "#a"}])
    with pytest.raises(wc.CaptureError, match="needs a `value`"):
        wc.validate_steps([{"fill": "#q"}])
    with pytest.raises(wc.CaptureError, match="unknown option"):
        wc.validate_steps([{"click": "#a", "valeu": "x"}])
    with pytest.raises(wc.CaptureError, match=r"then\[1\]"):
        wc.validate_steps([{"if_visible": "#a", "then": [{"bogus": 1}]}])


def test_mask_text_hides_secrets():
    assert wc.mask_text("pw is hunter22 ok", ["hunter22"]) == "pw is *** ok"


def _write_config(folder: Path, base_url: str, steps: str, login: str = "") -> Path:
    config = folder / "capture.yaml"
    config.write_text(
        f"""
base_url: {base_url}
headless: true
timeout: 5
login:
  mode: sso
  url: /login
  sso_timeout: 10
  logged_in_when:
    selector: "nav[aria-label='Main']"
{login}
data:
  csv: data.csv
  label: "${{customer_id}}"
steps:
{steps}
"""
    )
    return config


STEPS = """
  - goto: /customers
  - screenshot: list
  - fill: {placeholder: Search customers}
    value: "${customer_name}"
  - press: Enter
  - wait_for: {text: "${customer_name}"}
  - click: {role: link, name: "${customer_name}"}
  - extract: status
    selector: ".status-badge"
  - if_visible: {role: button, name: Dismiss}
    timeout: 1
    then:
      - click: {role: button, name: Dismiss}
  - screenshot: detail
    mask: [".ssn"]
  - click: {role: tab, name: Billing}
    optional: true
    timeout: 1
"""


def _workspace(tmp_path: Path, email: str, base_url: str = "http://127.0.0.1:1") -> Path:
    (tmp_path / ".env").write_text(f"SSO_EMAIL={email}\nPORTAL_PASSWORD=hunter22\n")
    (tmp_path / "data.csv").write_text(
        "customer_id,customer_name\nC-1,Ada Lovelace\nC-2,Grace Hopper\nC-3,Nobody Here\n"
    )
    return _write_config(tmp_path, base_url, STEPS)


def test_check_mode_reports_every_row(tmp_path, capsys):
    config = _workspace(tmp_path, "a@sso.test")
    assert wc.main(["--config", str(config), "--check"]) == 0
    out = capsys.readouterr().out
    assert "row 1: C-1" in out and "row 3: C-3" in out
    assert "SSO email: set" in out


def test_check_mode_fails_on_missing_column(tmp_path, capsys):
    config = _workspace(tmp_path, "a@sso.test")
    (tmp_path / "data.csv").write_text("customer_id\nC-1\n")
    assert wc.main(["--config", str(config), "--check"]) == 2
    assert "${customer_name} is not set" in capsys.readouterr().err


def test_config_needs_logged_in_rule(tmp_path, capsys):
    (tmp_path / "capture.yaml").write_text("base_url: http://x\nsteps:\n  - goto: /\n")
    assert wc.main(["--config", str(tmp_path / "capture.yaml"), "--check"]) == 2
    assert "logged_in_when" in capsys.readouterr().err


def test_shipped_sample_config_checks_out(monkeypatch, capsys):
    monkeypatch.setenv("PORTAL_URL", "https://portal.example.com")
    monkeypatch.setenv("SSO_EMAIL", "qa@example.com")
    samples = TOOL.parent / "samples"
    assert wc.main(["--config", str(samples / "capture.yaml"), "--env", str(samples / ".env.example"), "--check"]) == 0
    assert "row 3: C-3" in capsys.readouterr().out
    assert (samples / "sample-run" / "index.html").is_file()


# ---------------------------------------------------------- fake portal e2e

CUSTOMERS = {
    "C-1": ("Ada Lovelace", "Active"),
    "C-2": ("Grace Hopper", "Suspended"),
    "C-3": ("Alan Turing", "Active"),
}


def _page(body: str) -> bytes:
    return f"<!doctype html><html><head><title>Portal</title></head><body>{body}</body></html>".encode()


class PortalHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, body: bytes, status: int = 200, headers: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", "text/html")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str, cookie: str | None = None):
        headers = {"Location": location}
        if cookie:
            headers["Set-Cookie"] = cookie
        self._send(b"", 302, headers)

    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        signed_in = "session=ok" in (self.headers.get("Cookie") or "")
        if url.path == "/login":
            # The portal asks for an email, then hands over to the "IdP".
            return self._send(_page(
                '<form action="/idp"><input type="email" name="email"><button>Next</button></form>'
            ))
        if url.path == "/idp":
            email = query.get("email", [""])[0]
            if email.endswith("@blocked.test"):
                return self._send(_page('<input type="password" name="pw">'))
            if email:
                return self._redirect("/customers", "session=ok; Path=/")
            return self._send(_page(
                '<form action="/idp"><input name="loginfmt"><button>Next</button></form>'
            ))
        if not signed_in:
            return self._redirect("/login")
        nav = '<nav aria-label="Main">Home</nav>'
        if url.path == "/customers":
            q = query.get("q", [""])[0].lower()
            items = "".join(
                f'<li><a href="/customers/{cid}">{name}</a></li>'
                for cid, (name, _) in CUSTOMERS.items() if q in name.lower()
            )
            return self._send(_page(
                f'{nav}<form><input name="q" placeholder="Search customers"></form><ul>{items}</ul>'
            ))
        if url.path.startswith("/customers/"):
            name, status = CUSTOMERS[url.path.rsplit("/", 1)[1]]
            return self._send(_page(
                f'{nav}<h1>{name}</h1><span class="status-badge">{status}</span>'
                f'<p class="ssn">123-45-6789</p><button onclick="this.remove()">Dismiss</button>'
            ))
        self._send(_page("not found"), 404)


@pytest.fixture()
def portal():
    pytest.importorskip("playwright.sync_api")
    server = ThreadingHTTPServer(("127.0.0.1", 0), PortalHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def _needs_chromium():
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as pw:
            pw.chromium.launch(headless=True).close()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Chromium for Playwright is not installed: {exc}")


def test_sso_capture_runs_every_row_and_reuses_session(tmp_path, portal, capsys):
    _needs_chromium()
    config = _workspace(tmp_path, "qa@sso.test", portal)

    assert wc.main(["--config", str(config)]) == 1  # row 3 finds no match
    out = capsys.readouterr().out
    assert "Entered the SSO email" in out
    assert (tmp_path / "auth" / "session.json").is_file()

    run_dir = next((tmp_path / "captures").iterdir())
    manifest = json.loads((run_dir / "manifest.json").read_text())
    rows = {row["label"]: row for row in manifest["rows"]}
    assert rows["C-1"]["status"] == "passed"
    assert rows["C-1"]["extracted"] == {"status": "Active"}
    assert rows["C-2"]["extracted"] == {"status": "Suspended"}
    assert [s["file"] for s in rows["C-1"]["screenshots"]] == ["01_list.png", "02_detail.png"]
    assert rows["C-3"]["status"] == "failed"
    assert rows["C-3"]["screenshots"][-1]["name"] == "FAIL"
    assert (run_dir / "C-1" / "02_detail.png").is_file()
    assert "Suspended" in (run_dir / "extracted.csv").read_text()
    assert "Suspended" in (run_dir / "index.html").read_text()

    # Second run reuses the saved session instead of signing in.
    assert wc.main(["--config", str(config), "--rows", "1"]) == 0
    assert "Signing in" not in capsys.readouterr().out


def test_sso_mode_stops_when_idp_wants_a_password(tmp_path, portal, capsys):
    _needs_chromium()
    config = _workspace(tmp_path, "qa@blocked.test", portal)
    assert wc.main(["--config", str(config), "--rows", "1"]) == 2
    captured = capsys.readouterr()
    assert "asks for a password" in captured.out
    assert "--login manual" in captured.err
    assert not (tmp_path / "auth" / "session.json").exists()
