"""Jira and Confluence connection settings, separate PATs, and references on workflows."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

from qalprit.atlassian_refs import (
    AtlassianClient,
    AtlassianConfig,
    check_connection,
    read_connection,
    save_connection,
)
from qalprit.env_loader import parse_env_file, update_env_file
from qalprit.spec_loader import FlowSpec, TestSpec, effective_references, load_flows, normalize_step
from qalprit.workbench_api import save_flow_spec


def test_env_writer_keeps_other_lines_and_quotes_awkward_values(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("# keep me\nA=1\nexport B=2\nC=\"x y\"\n", encoding="utf-8")
    update_env_file(env, {"A": "new val #1", "B": None, "D": 'to"ken'})
    text = env.read_text(encoding="utf-8")
    assert text.startswith("# keep me\n")
    assert parse_env_file(env) == {"A": "new val #1", "C": "x y", "D": 'to"ken'}


def test_save_and_read_never_return_tokens(tmp_path: Path) -> None:
    saved = save_connection(tmp_path, {
        "site": "acme.atlassian.net/", "email": "qa@acme.test", "token": "secret-1",
        "confluence_token": "pat-2", "confluence_site": "https://wiki.acme.test",
    })
    assert saved["site"] == "https://acme.atlassian.net"
    assert saved["token_set"] and saved["confluence_token_set"] and not saved["jira_token_set"]
    assert "secret-1" not in str(saved) and "pat-2" not in str(saved)
    assert (tmp_path / ".env").stat().st_mode & 0o077 == 0, "a new .env is owner-only"

    # A blank token keeps the saved one; a clear flag removes it.
    again = save_connection(tmp_path, {"site": "https://acme.atlassian.net", "token": ""})
    assert again["token_set"]
    cleared = save_connection(tmp_path, {"clear_token": True, "clear_confluence_token": True})
    assert not cleared["token_set"] and not cleared["confluence_token_set"]
    assert cleared["site"] == "https://acme.atlassian.net", "clearing tokens leaves the site"


def test_a_bad_site_address_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a web address"):
        save_connection(tmp_path, {"site": "https://"})


def test_each_product_can_use_its_own_pat() -> None:
    cfg = AtlassianConfig.from_env({
        "ATLASSIAN_BASE_URL": "https://acme.atlassian.net",
        "ATLASSIAN_EMAIL": "qa@acme.test", "ATLASSIAN_API_TOKEN": "shared",
        "CONFLUENCE_API_TOKEN": "conf-pat",
    })
    assert cfg.auth_header("jira") == "Basic " + base64.b64encode(b"qa@acme.test:shared").decode()
    # Confluence has its own token and no email of its own, so it sends a Bearer PAT.
    assert cfg.auth_header("confluence") == "Bearer conf-pat"
    assert cfg.configured_for("jira") and cfg.configured_for("confluence")


def test_connection_test_reports_each_product(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []

    def fake(url: str, headers: dict[str, str]) -> Any:
        calls.append((url, headers["Authorization"]))
        if "/rest/api/2/myself" in url:
            return {"displayName": "Quinn QA"}
        raise RuntimeError("Atlassian returned 401. Check the email and token in Settings & setup > Jira and Confluence.")

    save_connection(tmp_path, {"site": "https://acme.atlassian.net", "email": "qa@acme.test", "token": "t"})
    res = check_connection(tmp_path, {}, fetch=fake)
    assert res["results"]["jira"] == {"ok": True, "message": "Connected to Jira as Quinn QA."}
    assert res["results"]["confluence"]["ok"] is False and "401" in res["results"]["confluence"]["message"]
    assert res["ok"] is False

    # Typed but unsaved values are tried, and are not written to disk.
    res = check_connection(tmp_path, {"jira_token": "typed-pat", "email": ""}, fetch=fake)
    jira_auth = next(auth for url, auth in reversed(calls) if "/rest/api/2/myself" in url)
    assert jira_auth == "Bearer typed-pat", "a product's own token brings its own (empty) email"
    assert "typed-pat" not in (tmp_path / ".env").read_text(encoding="utf-8")


def test_unconfigured_connection_says_so(tmp_path: Path) -> None:
    res = check_connection(tmp_path, {})
    assert res["ok"] is False
    assert all(r["ok"] is None for r in res["results"].values())
    assert read_connection({})["jira_ready"] is False


def _flow(tmp_path: Path, name: str, refs: list[str] | None) -> None:
    save_flow_spec(tmp_path, name=name, params=[], steps=[{"tap": "Go"}], references=refs)


def test_workflow_references_round_trip_and_survive_step_edits(tmp_path: Path) -> None:
    _flow(tmp_path, "checkout", ["SHOP-1", "SHOP-1", "https://acme.atlassian.net/wiki/spaces/QA/pages/9"])
    flows, errors = load_flows(tmp_path / "flows")
    assert not errors
    assert flows["checkout"].references == ["SHOP-1", "https://acme.atlassian.net/wiki/spaces/QA/pages/9"]

    # Saving steps without a references list (recording, step edits) keeps the links.
    _flow(tmp_path, "checkout", None)
    assert load_flows(tmp_path / "flows")[0]["checkout"].references[0] == "SHOP-1"
    # An explicit empty list removes them.
    _flow(tmp_path, "checkout", [])
    assert load_flows(tmp_path / "flows")[0]["checkout"].references == []


def test_a_journey_inherits_the_links_of_the_workflows_it_runs() -> None:
    flows = {
        "login": FlowSpec(name="login", references=["AUTH-1", "SHOP-1"], source_path=Path("login.md")),
        "pay": FlowSpec(name="pay", references=["PAY-7"], source_path=Path("pay.md")),
        "plain": FlowSpec(name="plain", source_path=Path("plain.md")),
    }
    spec = TestSpec.model_construct(
        id="T-1", title="t", references=["SHOP-1", "T-OWN-1"],
        setup=[], teardown=[],
        steps=[normalize_step({"run_flow": n}) for n in ("login", "plain", "pay", "login")],
    )
    assert effective_references(spec, flows) == ["SHOP-1", "T-OWN-1", "AUTH-1", "PAY-7"]
