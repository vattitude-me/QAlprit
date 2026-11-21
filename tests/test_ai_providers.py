"""AI check providers: the Anthropic API, the agy CLI and the GitHub Copilot CLI."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from qalprit.ai_review import (
    CliReviewer,
    _extract_json,
    make_reviewer,
    resolve_provider,
    review_run_dir,
)
from qalprit.results import load_results_json
from tests.test_reference_review import _run_with_reference

ANSWER = {
    "steps": [
        {"index": 0, "verdict": "pass", "reason": "Cart opened.", "requirement": "cart shows items"},
        {"index": 1, "verdict": "fail", "reason": "Cart title missing.", "requirement": ""},
    ],
    "summary": "One step does not match.",
    "uncovered_requirements": ["Guest can pay"],
}


class _FakeCli:
    """Stands in for subprocess.run and records what the reviewer ran and what it left in the folder."""

    def __init__(self, stdout: str, returncode: int = 0, stderr: str = "") -> None:
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr
        self.calls: list[dict[str, Any]] = []

    def __call__(self, cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        work = Path(kwargs["cwd"])
        self.calls.append({
            "cmd": cmd,
            "cwd": work,
            "files": sorted(p.name for p in work.iterdir()),
            "request": (work / "review_request.md").read_text(encoding="utf-8"),
            "stdin": kwargs.get("stdin"),
        })
        return subprocess.CompletedProcess(cmd, self.returncode, self.stdout, self.stderr)


def _indices(res: Any) -> list[int]:
    return [s.index for s in res.tests[0].steps if s.phase == "steps"]


def _answer_for(res: Any) -> dict[str, Any]:
    idx = _indices(res)
    out = json.loads(json.dumps(ANSWER))
    out["steps"][0]["index"], out["steps"][1]["index"] = idx[0], idx[1]
    return out


def test_provider_defaults_to_anthropic_and_follows_env_and_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QALPRIT_AI_PROVIDER", raising=False)
    assert resolve_provider({}) == "anthropic"
    assert resolve_provider({"QALPRIT_AI_PROVIDER": "AGY"}) == "agy"
    assert resolve_provider({"QALPRIT_AI_PROVIDER": "agy"}, "copilot") == "copilot"
    assert resolve_provider({}, "antigravity") == "agy"
    assert resolve_provider({}, "github-copilot") == "copilot"
    with pytest.raises(ValueError, match="Unknown AI provider"):
        resolve_provider({}, "gpt")


def test_missing_cli_says_how_to_install_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("QALPRIT_COPILOT_BIN", raising=False)
    with pytest.raises(FileNotFoundError, match="npm install -g @github/copilot"):
        make_reviewer({"QALPRIT_COPILOT_BIN": str(tmp_path / "nope")}, "copilot")
    with pytest.raises(FileNotFoundError, match="QALPRIT_AGY_BIN"):
        make_reviewer({"QALPRIT_AGY_BIN": str(tmp_path / "nope")}, "agy")


def test_agy_gets_a_request_file_and_screenshots_and_returns_structured_output(tmp_path: Path) -> None:
    run_dir, res = _run_with_reference(tmp_path)
    answer = _answer_for(res)
    stdout = json.dumps({"status": "SUCCESS", "response": "ignored", "structured_output": answer})
    fake = _FakeCli(stdout)
    reviewer = CliReviewer("agy", "/bin/agy", model="gemini-4-neon-high", runner=fake)

    out = review_run_dir(tmp_path, run_dir, client=reviewer)

    (call,) = fake.calls
    cmd = call["cmd"]
    assert cmd[:2] == ["/bin/agy", "--print"]
    assert "--json-schema" in cmd and cmd[cmd.index("--output-format") + 1] == "json"
    assert cmd[cmd.index("--model") + 1] == "gemini-4-neon-high"
    assert "--dangerously-skip-permissions" not in cmd
    assert call["stdin"] == subprocess.DEVNULL
    idx = _indices(res)
    assert "review_request.md" in call["files"]
    assert f"step{idx[0]}_replay.jpg" in call["files"] and f"step{idx[0]}_recorded.jpg" in call["files"]
    assert f"Replay screenshot: step{idx[1]}_replay.jpg" in call["request"]
    assert "Browse to cart" in call["request"]
    assert not call["cwd"].exists(), "the throwaway folder is removed after the review"

    assert out["provider"] == "agy" and "Antigravity" in out["model"]
    saved = load_results_json(run_dir / "results.json").tests[0]
    verdicts = {s.index: s.ai_verdict for s in saved.steps if s.phase == "steps"}
    assert verdicts == {idx[0]: "pass", idx[1]: "fail"}
    assert saved.ai_summary == "One step does not match."


def test_copilot_reply_with_prose_and_a_code_fence_is_parsed(tmp_path: Path) -> None:
    run_dir, res = _run_with_reference(tmp_path)
    stdout = "Here is my review.\n```json\n" + json.dumps(_answer_for(res)) + "\n```\nDone."
    fake = _FakeCli(stdout)

    out = review_run_dir(tmp_path, run_dir, client=CliReviewer("copilot", "/bin/copilot", runner=fake))

    cmd = fake.calls[0]["cmd"]
    assert cmd[:2] == ["/bin/copilot", "-p"] and "--silent" in cmd
    assert cmd[cmd.index("--add-dir") + 1] == str(fake.calls[0]["cwd"])
    assert not any(flag.startswith("--allow-all") or flag == "--yolo" for flag in cmd)
    assert out["provider"] == "copilot"
    assert out["tests"][0]["error"] == ""
    assert {v["verdict"] for v in out["tests"][0]["steps"].values()} == {"pass", "fail"}


def test_a_failing_cli_is_reported_not_raised(tmp_path: Path) -> None:
    run_dir, _ = _run_with_reference(tmp_path)
    fake = _FakeCli("", returncode=1, stderr="warming up\nPlease sign in first.")

    out = review_run_dir(tmp_path, run_dir, client=CliReviewer("agy", "/bin/agy", runner=fake))

    assert "exited with code 1" in out["tests"][0]["error"]
    assert "Please sign in first." in out["tests"][0]["error"]
    assert load_results_json(run_dir / "results.json").tests[0].ai_error


def test_a_cli_that_times_out_is_reported(tmp_path: Path) -> None:
    run_dir, _ = _run_with_reference(tmp_path)

    def slow(cmd: list[str], **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd, 1)

    out = review_run_dir(tmp_path, run_dir, client=CliReviewer("copilot", "/bin/copilot", runner=slow, timeout=600))
    assert "longer than 10 minutes" in out["tests"][0]["error"]


def test_a_reply_without_json_is_reported(tmp_path: Path) -> None:
    run_dir, _ = _run_with_reference(tmp_path)
    out = review_run_dir(tmp_path, run_dir, client=CliReviewer("copilot", "/bin/copilot", runner=_FakeCli("Looks fine to me.")))
    assert "did not return a JSON verdict" in out["tests"][0]["error"]


def test_extract_json_handles_braces_inside_strings_and_picks_the_verdict() -> None:
    answer = {"steps": [{"index": 1, "verdict": "pass", "reason": 'Shows "{Cart}" and a } sign.', "requirement": ""}],
              "summary": "ok", "uncovered_requirements": []}
    text = 'Thinking {not json}. Final answer: ' + json.dumps(answer) + " Thanks."
    assert _extract_json(text) == answer
    assert _extract_json(json.dumps(answer)) == answer
    with pytest.raises(RuntimeError):
        _extract_json("no object here")
