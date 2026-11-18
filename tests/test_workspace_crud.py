"""Create, rename and delete operations behind the Workbench Testing space."""

from __future__ import annotations

import json
from pathlib import Path

import frontmatter
import pytest
import yaml

from qalprit.workbench_api import (
    delete_environment_entry,
    delete_flow_spec,
    delete_run_results,
    delete_screen_locator,
    delete_workspace_app,
    duplicate_flow_spec,
    rename_flow_spec,
)

_FLOW = """---
name: auth/login
params: [username]
steps:
  - launch: app
  - type: {target: login.user, value: "${username}"}
---

Login steps.
"""

_SPEC = """---
id: SMK-001
title: Login works
tags: [smoke]
platforms: [android]
steps:
  - run_flow:
      flow: auth/login
      params: {username: bob}
  - run_flow: auth/login
teardown:
  - run_flow: {name: auth/login}
---
"""


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / "flows" / "auth").mkdir(parents=True)
    (root / "specs" / "smoke").mkdir(parents=True)
    (root / "datasets" / "flows" / "auth").mkdir(parents=True)
    (root / "flows" / "auth" / "login.md").write_text(_FLOW, encoding="utf-8")
    (root / "specs" / "smoke" / "SMK-001.md").write_text(_SPEC, encoding="utf-8")
    (root / "datasets" / "flows" / "auth" / "login.csv").write_text("name,username\nrecorded,bob\n", encoding="utf-8")
    return root


def test_rename_flow_moves_file_dataset_and_updates_references(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    res = rename_flow_spec(root, "auth/login", "auth/sign in")

    assert res["name"] == "auth/sign_in"
    assert res["updated_references"] == 1
    assert not (root / "flows" / "auth" / "login.md").exists()
    moved = frontmatter.loads((root / "flows" / "auth" / "sign_in.md").read_text(encoding="utf-8"))
    assert moved.metadata["name"] == "auth/sign_in"
    assert (root / "datasets" / "flows" / "auth" / "sign_in.csv").is_file()
    assert not (root / "datasets" / "flows" / "auth" / "login.csv").exists()

    spec = frontmatter.loads((root / "specs" / "smoke" / "SMK-001.md").read_text(encoding="utf-8"))
    assert spec.metadata["steps"][0]["run_flow"]["flow"] == "auth/sign_in"
    assert spec.metadata["steps"][1]["run_flow"] == "auth/sign_in"
    assert spec.metadata["teardown"][0]["run_flow"]["name"] == "auth/sign_in"


def test_rename_flow_rejects_missing_and_existing_names(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    with pytest.raises(ValueError, match="not found"):
        rename_flow_spec(root, "auth/nope", "x")
    duplicate_flow_spec(root, "auth/login", "auth/copy")
    with pytest.raises(ValueError, match="already exists"):
        rename_flow_spec(root, "auth/login", "auth/copy")


def test_duplicate_copies_dataset_and_delete_removes_it(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    duplicate_flow_spec(root, "auth/login", "auth/copy")
    copy_csv = root / "datasets" / "flows" / "auth" / "copy.csv"
    assert copy_csv.is_file()

    delete_flow_spec(root, "auth/copy")
    assert not (root / "flows" / "auth" / "copy.md").exists()
    assert not copy_csv.exists()
    assert (root / "datasets" / "flows" / "auth" / "login.csv").is_file()


def test_delete_run_results_removes_batch_and_children_only(tmp_path: Path) -> None:
    results = tmp_path / "results"
    batch = results / "20260101_batch"
    batch.mkdir(parents=True)
    (batch / "batch.json").write_text(json.dumps({"batch_id": "b"}), encoding="utf-8")
    for suffix in ("_01_alice", "_02_bob"):
        child = results / f"20260101_batch{suffix}"
        child.mkdir()
        (child / "results.json").write_text("{}", encoding="utf-8")
    other = results / "20260102_run"
    other.mkdir()
    (other / "results.json").write_text("{}", encoding="utf-8")

    res = delete_run_results(results, "20260101_batch")
    assert sorted(res["deleted"]) == ["20260101_batch", "20260101_batch_01_alice", "20260101_batch_02_bob"]
    assert other.is_dir()

    with pytest.raises(ValueError):
        delete_run_results(results, "../results")
    stray = results / "notes"
    stray.mkdir()
    with pytest.raises(ValueError, match="not a QAlprit run"):
        delete_run_results(results, "notes")
    assert stray.is_dir()


def test_delete_workspace_app_only_touches_app_files(tmp_path: Path) -> None:
    apps = tmp_path / "apps"
    apps.mkdir()
    (apps / "demo.apk").write_bytes(b"apk")
    (apps / "notes.txt").write_text("keep", encoding="utf-8")

    assert delete_workspace_app(tmp_path, "apps/demo.apk")["deleted_path"] == "apps/demo.apk"
    assert not (apps / "demo.apk").exists()
    with pytest.raises(ValueError):
        delete_workspace_app(tmp_path, "apps/notes.txt")
    with pytest.raises(ValueError):
        delete_workspace_app(tmp_path, "../apps/demo.apk")
    assert (apps / "notes.txt").is_file()


def test_delete_screen_locator_and_environment(tmp_path: Path) -> None:
    (tmp_path / "screens").mkdir()
    screens = {"login": {"user": {"accessibility_id": "user"}}, "home": {"title": {"accessibility_id": "t"}, "menu": {"accessibility_id": "m"}}}
    (tmp_path / "screens" / "screens.yaml").write_text(yaml.safe_dump(screens), encoding="utf-8")

    delete_screen_locator(tmp_path, "home.menu")
    delete_screen_locator(tmp_path, "login.user")
    left = yaml.safe_load((tmp_path / "screens" / "screens.yaml").read_text(encoding="utf-8"))
    assert left == {"home": {"title": {"accessibility_id": "t"}}}
    with pytest.raises(ValueError, match="not found"):
        delete_screen_locator(tmp_path, "home.menu")

    (tmp_path / "config").mkdir()
    envs = {"environments": {"qa": {"android_package": "a.b", "default_timeout": 12}, "dev": {"android_package": "a.c", "default_timeout": 12}}}
    (tmp_path / "config" / "environments.yaml").write_text(yaml.safe_dump(envs), encoding="utf-8")
    delete_environment_entry(tmp_path, "dev")
    left_envs = yaml.safe_load((tmp_path / "config" / "environments.yaml").read_text(encoding="utf-8"))
    assert list(left_envs["environments"]) == ["qa"]
    with pytest.raises(ValueError, match="at least one"):
        delete_environment_entry(tmp_path, "qa")


def test_flow_remembers_the_app_per_platform_and_keeps_it_on_edit(tmp_path: Path) -> None:
    from qalprit.spec_loader import load_flows
    from qalprit.workbench_api import save_flow_spec

    steps = [{"tap": "login.button"}]
    save_flow_spec(tmp_path, name="auth/login", params=[], steps=steps, platforms=["android"], apps={"android": "apps/demo.apk"})
    save_flow_spec(tmp_path, name="auth/login", params=[], steps=steps, platforms=["ios"], apps={"ios": "apps/Demo.app"})
    # A plain edit from the Workbench passes no apps and must not drop them.
    save_flow_spec(tmp_path, name="auth/login", params=[], steps=steps)

    flows, errors = load_flows(tmp_path / "flows")
    assert not errors
    assert flows["auth/login"].apps == {"android": "apps/demo.apk", "ios": "apps/Demo.app"}
