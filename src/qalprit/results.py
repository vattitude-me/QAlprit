"""Result data models and results.json persistence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from qalprit.env_loader import SecretMasker

FAILURE_CATEGORIES: tuple[str, ...] = (
    "application_failure",
    "assertion_failure",
    "locator_not_found",
    "element_not_interactable",
    "wait_timeout",
    "appium_session_failure",
    "device_unavailable",
    "app_launch_failure",
    "environment_configuration",
    "interrupted",
    "unknown",
)

TRANSIENT_FAILURE_CATEGORIES: frozenset[str] = frozenset(
    {
        "wait_timeout",
        "locator_not_found",
        "element_not_interactable",
        "appium_session_failure",
    }
)


def classify_failure(
    exc: BaseException,
    step_action: str | None = None,
    phase: str | None = None,
) -> tuple[str, str]:
    """Classify an exception into a standardized failure category and type name."""
    exc_type = type(exc).__name__
    if isinstance(exc, KeyboardInterrupt):
        return "interrupted", exc_type

    explicit_cat = getattr(exc, "failure_category", None)
    if isinstance(explicit_cat, str) and explicit_cat in FAILURE_CATEGORIES:
        return explicit_cat, exc_type

    msg = str(exc)
    low = msg.lower()
    mod = type(exc).__module__.lower()

    if "interrupted" in low or "keyboardinterrupt" in low:
        return "interrupted", exc_type

    if phase == "reset" or step_action in {"launch", "reset_app"}:
        if "no app identifier" in low or "environment variable" in low:
            return "environment_configuration", exc_type
        return "app_launch_failure", exc_type

    if "stale" in low or "stale" in exc_type.lower() or "not interactable" in low:
        return "element_not_interactable", exc_type

    if isinstance(exc, TimeoutError) and step_action in {
        "wait_for_visible",
        "wait_for_enabled",
        "wait_for_text",
        "wait_for_keyboard",
        "scroll_until_visible",
    }:
        return "wait_timeout", exc_type

    if isinstance(exc, AssertionError):
        if step_action in {
            "assert_visible",
            "assert_not_visible",
            "assert_text",
            "assert_enabled",
            "assert_disabled",
            "visual_compare",
        } or "assertion failed" in low or "visual diff" in low:
            return "assertion_failure", exc_type
        if "stale" in low or "not interactable" in low or "disabled" in low or "hidden" in low:
            return "element_not_interactable", exc_type
        if step_action in {
            "wait_for_visible",
            "wait_for_enabled",
            "wait_for_text",
            "wait_for_keyboard",
            "scroll_until_visible",
        }:
            return "wait_timeout", exc_type
        if "timed out" in low:
            return "locator_not_found", exc_type
        return "assertion_failure", exc_type

    if isinstance(exc, (KeyError, ValueError)) and any(
        hint in low
        for hint in (
            "environment variable",
            "totp",
            "no app identifier",
            "unknown screen",
            "unknown target",
            "missing required",
            "must be",
        )
    ):
        return "environment_configuration", exc_type

    if any(tok in low for tok in ("device offline", "device unavailable", "device not found", "no connected device", "adb")):
        return "device_unavailable", exc_type

    if (
        "session" in low
        or "connection refused" in low
        or "urllib" in exc_type.lower()
        or "webdriver" in mod
        or "appium" in mod
    ):
        return "appium_session_failure", exc_type

    if any(tok in low for tok in ("crash", "anr", "instrumentation process", "fatal exception")):
        return "application_failure", exc_type

    return "unknown", exc_type


class StepResult(BaseModel):
    """Execution result of a single expanded step."""

    index: int
    phase: Literal["setup", "steps", "teardown"] = "steps"
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
    redaction_status: str = "none_needed"
    redaction_warning: str = ""
    failure_category: str = ""
    exception_type: str = ""
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    visual_diff_path: str = ""
    visual_diff_score: float | None = None


class AttemptResult(BaseModel):
    """Single attempt of a test case (supports retries)."""

    attempt: int
    status: Literal["passed", "failed", "skipped"]
    duration_ms: int = 0
    error: str | None = None
    failure_category: str = ""
    exception_type: str = ""
    failed_step_index: int | None = None
    failed_step_action: str = ""
    reset_status: str = "ok"
    reset_error: str = ""
    retry_reason: str = ""
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
    failure_category: str = ""
    exception_type: str = ""
    failed_step_index: int | None = None
    failed_step_action: str = ""
    retriable: bool = True
    reset_mode: str = "none"
    redaction_warnings: int = 0
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
    interrupted: int = 0
    webview_tests: int = 0
    redaction_warnings: int = 0
    failure_categories: dict[str, int] = Field(default_factory=dict)
    duration_ms: int = 0


class RunResult(BaseModel):
    """Top-level run report payload serialized to results.json."""

    schema_version: str = "1.1"
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
    app_sha256: str = ""
    appium_version: str = ""
    automation_name: str = ""
    host_os: str = ""
    python_version: str = ""
    suite: str = "all"
    tags: list[str] = Field(default_factory=list)
    interrupted: bool = False
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
    """Load RunResult from a results.json file, normalizing legacy v1.0 payloads."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        if "schema_version" not in data:
            data = {**data, "schema_version": "1.0"}
        for test in data.get("tests") or []:
            if not isinstance(test, dict):
                continue
            for step_list_key in ("steps",):
                for st in test.get(step_list_key) or []:
                    if isinstance(st, dict) and "redaction_status" not in st:
                        st["redaction_status"] = "redacted" if (st.get("redacted_count") or 0) > 0 else "none_needed"
            for att in test.get("attempts") or []:
                if not isinstance(att, dict):
                    continue
                for st in att.get("steps") or []:
                    if isinstance(st, dict) and "redaction_status" not in st:
                        st["redaction_status"] = "redacted" if (st.get("redacted_count") or 0) > 0 else "none_needed"
    return RunResult.model_validate(data)
