"""Result data models and results.json persistence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from qalprit.env_loader import SecretMasker


class StepResult(BaseModel):
    """Execution result of a single expanded step."""

    index: int
    phase: Literal["steps", "teardown"] = "steps"
    action: str
    target: str | None = None
    flow_name: str | None = None
    description: str
    status: Literal["passed", "failed", "skipped"]
    duration_ms: int = 0
    screenshot_path: str | None = None
    page_source_path: str | None = None
    error: str | None = None
    webview_detected: bool = False
    webview_details: list[str] = Field(default_factory=list)
    redacted_count: int = 0


class AttemptResult(BaseModel):
    """Single attempt of a test case (supports retries)."""

    attempt: int
    status: Literal["passed", "failed", "skipped"]
    duration_ms: int = 0
    error: str | None = None
    steps: list[StepResult] = Field(default_factory=list)


class TestResult(BaseModel):
    """Final test case result across all attempts."""

    __test__ = False

    id: str
    title: str
    tags: list[str] = Field(default_factory=list)
    platforms: list[str] = Field(default_factory=list)
    targets: list[str] = Field(default_factory=list)
    priority: str = "medium"
    expected: str = ""
    status: Literal["passed", "failed", "skipped"]
    flaky: bool = False
    skip_reason: str | None = None
    failure_reason: str | None = None
    duration_ms: int = 0
    webview_detected: bool = False
    attempts: list[AttemptResult] = Field(default_factory=list)
    steps: list[StepResult] = Field(default_factory=list)


class RunSummary(BaseModel):
    """Aggregated test run counters."""

    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    flaky: int = 0
    webview_tests: int = 0
    duration_ms: int = 0


class RunResult(BaseModel):
    """Top-level run report payload serialized to results.json."""

    run_id: str
    tool_version: str
    started_at: str
    finished_at: str
    environment: str
    platform: str
    device_profile: str
    device_name: str
    target_type: str
    os_version: str = ""
    app_path: str = ""
    app_version: str = ""
    suite: str = "all"
    tags: list[str] = Field(default_factory=list)
    summary: RunSummary
    tests: list[TestResult] = Field(default_factory=list)
    appium_log_path: str | None = None


def write_results_json(
    run_result: RunResult,
    output_path: Path,
    masker: SecretMasker | None = None,
) -> Path:
    """Serialize RunResult to JSON with secret masking applied."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = run_result.model_dump(mode="json")
    if masker is not None:
        payload = masker.mask_obj(payload)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return output_path


def load_results_json(path: Path) -> RunResult:
    """Load RunResult from a results.json file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return RunResult.model_validate(data)
