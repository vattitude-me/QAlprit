"""Where the Workbench keeps its workspace, and keeping it out of a source checkout."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from qalprit import cli as cli_mod
from qalprit import desktop, workbench_api
from qalprit.cli import main


def _fake_repo(path: Path) -> Path:
    (path / ".git").mkdir(parents=True)
    return path


def test_default_workspace_can_skip_the_current_folder(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    (checkout / "config").mkdir(parents=True)
    (checkout / "config" / "devices.yaml").write_text("devices: {}\n", encoding="utf-8")
    (checkout / "config" / "environments.yaml").write_text("environments: {}\n", encoding="utf-8")
    saved = tmp_path / "saved-ws"
    monkeypatch.delenv("QALPRIT_WORKSPACE", raising=False)
    monkeypatch.setenv("QALPRIT_SETTINGS_PATH", str(tmp_path / "settings.json"))
    (tmp_path / "settings.json").write_text(f'{{"workspace_dir": "{saved}"}}', encoding="utf-8")
    monkeypatch.chdir(checkout)

    assert desktop.resolve_default_workspace() == checkout.resolve()
    assert desktop.resolve_default_workspace(use_cwd=False) == saved.resolve()


def test_workspace_inside_a_repo_gets_a_gitignore(tmp_path):
    repo = _fake_repo(tmp_path / "repo")
    ws = repo / "my-workspace"
    desktop.ensure_workspace(ws)
    assert (ws / ".gitignore").read_text(encoding="utf-8").splitlines()[-1] == "*"
    assert desktop.ensure_workspace_git_ignored(ws) is True


def test_workspace_outside_a_repo_or_at_the_repo_root_is_left_alone(tmp_path):
    plain = tmp_path / "plain"
    desktop.ensure_workspace(plain)
    assert not (plain / ".gitignore").exists()

    repo = _fake_repo(tmp_path / "repo")
    assert desktop.ensure_workspace_git_ignored(repo) is False
    assert not (repo / ".gitignore").exists()


def test_existing_gitignore_is_not_overwritten(tmp_path):
    repo = _fake_repo(tmp_path / "repo")
    ws = repo / "ws"
    ws.mkdir()
    (ws / ".gitignore").write_text("results/\n", encoding="utf-8")
    desktop.ensure_workspace(ws)
    assert (ws / ".gitignore").read_text(encoding="utf-8") == "results/\n"


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_folder_with_tracked_files_is_not_hidden(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    sub = repo / "examples"
    sub.mkdir()
    (sub / "kept.md").write_text("tracked", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "examples/kept.md"], check=True)

    assert desktop.ensure_workspace_git_ignored(sub) is False
    assert not (sub / ".gitignore").exists()


class _FakeServer:
    server_address = ("127.0.0.1", 9999)

    def serve_forever(self):
        raise KeyboardInterrupt

    def server_close(self):
        pass


def _run_ui(monkeypatch, args):
    seen = {}

    def fake_create(root, **kwargs):
        seen["root"] = Path(root)
        return _FakeServer()

    monkeypatch.setattr(cli_mod, "create_ui_server", fake_create)
    result = CliRunner().invoke(main, ["ui", "--no-open", *args])
    assert result.exit_code == 0, result.output
    return seen["root"], result.output


def test_browser_ui_does_not_use_the_current_folder(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    (checkout / "config").mkdir(parents=True)
    (checkout / "config" / "devices.yaml").write_text("devices: {}\n", encoding="utf-8")
    (checkout / "config" / "environments.yaml").write_text("environments: {}\n", encoding="utf-8")
    ws = tmp_path / "chosen"
    monkeypatch.delenv("QALPRIT_WORKSPACE", raising=False)
    monkeypatch.setenv("QALPRIT_SETTINGS_PATH", str(tmp_path / "settings.json"))
    (tmp_path / "settings.json").write_text(f'{{"workspace_dir": "{ws}"}}', encoding="utf-8")
    monkeypatch.chdir(checkout)

    root, output = _run_ui(monkeypatch, [])
    assert root == ws.resolve()
    assert (ws / "config" / "devices.yaml").is_file()
    assert str(ws.resolve()) in output
    assert not (checkout / ".env").exists()


def test_browser_ui_workspace_flag_wins(tmp_path, monkeypatch):
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    root, _ = _run_ui(monkeypatch, ["--workspace", str(explicit)])
    assert root == explicit.resolve()
    # An explicit folder is opened as is. QAlprit does not add files to it.
    assert not (explicit / ".env").exists()


def test_pick_folder_returns_the_chosen_path_or_reports_cancel(tmp_path, monkeypatch):
    chosen = tmp_path / "picked"
    chosen.mkdir()

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=f"{chosen}/\n", stderr="")

    monkeypatch.setattr(workbench_api.subprocess, "run", fake_run)
    monkeypatch.setattr(workbench_api.shutil, "which", lambda name: "/usr/bin/" + name)
    got = workbench_api.pick_folder(str(tmp_path))
    assert got["cancelled"] is False and got["path"] == str(chosen.resolve())

    monkeypatch.setattr(
        workbench_api.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="canceled")
    )
    assert workbench_api.pick_folder(str(tmp_path)) == {"ok": True, "cancelled": True, "path": ""}


def test_switching_workspace_inside_a_repo_reports_it_and_ignores_it(tmp_path, monkeypatch):
    monkeypatch.setenv("QALPRIT_SETTINGS_PATH", str(tmp_path / "settings.json"))
    repo = _fake_repo(tmp_path / "repo")
    target = repo / "ws"
    res = workbench_api.update_storage_preferences(tmp_path, {"workspace_dir": str(target)})
    assert res["inside_git_repo"] is True
    assert (target / ".gitignore").is_file()
