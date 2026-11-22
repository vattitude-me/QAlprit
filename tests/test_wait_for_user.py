"""wait_for_user: pausing a run for something only a person can do."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest

from qalprit import handoff
from qalprit.config_loader import ScreensCatalog
from qalprit.recorder import _step_to_yaml_dict
from qalprit.report.generator import _manual_label
from qalprit.spec_loader import StepItem, normalize_step
from qalprit.steps import execute_step, format_step_description
from qalprit.steps.base import StepFailureError
from qalprit.validator import _validate_locator_and_env, _validate_step_shape
from tests.fake_driver import FakeDriver
from tests.test_roadmap_features import _make_step_context


@pytest.fixture()
def broker(monkeypatch: pytest.MonkeyPatch) -> handoff.HandoffBroker:
    b = handoff.HandoffBroker()
    b.enable_web()
    monkeypatch.setattr("qalprit.steps.handoff_steps.BROKER", b)
    return b


def _answer_when_pending(b: handoff.HandoffBroker, answer: str, value: str = "", note: str = "") -> threading.Thread:
    def run() -> None:
        for _ in range(250):
            pend = b.pending()
            if pend:
                b.respond(answer, value=value, note=note, request_id=pend["id"])
                return
            time.sleep(0.02)

    t = threading.Thread(target=run)
    t.start()
    return t


def test_shorthand_sets_prompt_and_turns_screenshots_off() -> None:
    step = normalize_step({"wait_for_user": "Take a selfie"})
    assert step.params == {"prompt": "Take a selfie"}
    assert step.capture == "off"
    assert "Take a selfie" in format_step_description(step)
    # An explicit capture setting wins.
    assert normalize_step({"wait_for_user": "x", "capture": "on"}).capture == "on"


def test_action_waits_for_done(tmp_path: Path, broker: handoff.HandoffBroker) -> None:
    ctx = _make_step_context(tmp_path, FakeDriver(platform="android"))
    t = _answer_when_pending(broker, "done")
    out = execute_step(ctx, normalize_step({"wait_for_user": {"prompt": "Scan your ID", "timeout": 5}}))
    t.join()
    assert out.status == "passed"
    assert out.diagnostics["manual"] is True
    assert out.diagnostics["answer"] == "done"
    assert broker.pending() is None


def test_action_resumes_by_itself_when_until_shows_up(tmp_path: Path, broker: handoff.HandoffBroker) -> None:
    driver = FakeDriver(platform="android")
    driver.appear_after("accessibility_id", "status_lbl", calls=2)
    ctx = _make_step_context(tmp_path, driver)
    step = normalize_step({"wait_for_user": {"prompt": "Take a selfie", "until": "auth.status_lbl", "timeout": 5}})
    started = time.monotonic()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(handoff, "DEFAULT_POLL_EVERY", 0.05)
        out = execute_step(ctx, step)
    assert out.diagnostics["answer"] == "auto"
    assert time.monotonic() - started < 3


def test_done_with_until_missing_fails(tmp_path: Path, broker: handoff.HandoffBroker) -> None:
    ctx = _make_step_context(tmp_path, FakeDriver(platform="android"))
    t = _answer_when_pending(broker, "done")
    step = normalize_step(
        {"wait_for_user": {"prompt": "Take a selfie", "until": "auth.status_lbl", "timeout": 5, "find_timeout": 0.1}}
    )
    with pytest.raises(StepFailureError, match="did not appear"):
        execute_step(ctx, step)
    t.join()


def test_skip_only_when_optional(tmp_path: Path, broker: handoff.HandoffBroker) -> None:
    ctx = _make_step_context(tmp_path, FakeDriver(platform="android"))
    t = _answer_when_pending(broker, "skip")
    out = execute_step(ctx, normalize_step({"wait_for_user": {"prompt": "Selfie", "optional": True, "timeout": 5}}))
    t.join()
    assert out.status == "skipped"
    assert out.diagnostics["answer"] == "skip"


def test_fail_answer_fails_the_step_with_the_note(tmp_path: Path, broker: handoff.HandoffBroker) -> None:
    ctx = _make_step_context(tmp_path, FakeDriver(platform="android"))
    t = _answer_when_pending(broker, "fail", note="Camera screen was black")
    with pytest.raises(StepFailureError, match="Camera screen was black") as exc_info:
        execute_step(ctx, normalize_step({"wait_for_user": {"prompt": "Selfie", "timeout": 5}}))
    t.join()
    assert exc_info.value.failure_category == "application_failure"


def test_input_uses_preset_value_and_types_it(tmp_path: Path, broker: handoff.HandoffBroker) -> None:
    driver = FakeDriver(platform="android")
    field = driver.add_element("accessibility_id", "status_lbl")
    ctx = _make_step_context(tmp_path, driver)
    ctx.dataset_vars["PIN"] = "2468"
    step = normalize_step(
        {"wait_for_user": {"prompt": "Enter the PIN", "input": "PIN", "target": "auth.status_lbl", "secret": True}}
    )
    out = execute_step(ctx, step)
    assert field.typed == ["2468"]
    assert out.diagnostics["answer"] == "preset"
    assert broker.pending() is None
    assert ctx.masker.mask("pin 2468") != "pin 2468"


def test_input_asks_and_saves_the_variable(tmp_path: Path, broker: handoff.HandoffBroker) -> None:
    ctx = _make_step_context(tmp_path, FakeDriver(platform="android"))
    t = _answer_when_pending(broker, "done", value="Toronto")
    execute_step(ctx, normalize_step({"wait_for_user": {"prompt": "Which city?", "input": "CITY", "timeout": 5}}))
    t.join()
    assert ctx.captured_vars["CITY"] == "Toronto"


def test_nobody_to_ask_fails_fast(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    b = handoff.HandoffBroker()  # not web, and pytest's stdin is not a terminal
    monkeypatch.setattr("qalprit.steps.handoff_steps.BROKER", b)
    ctx = _make_step_context(tmp_path, FakeDriver(platform="android"))
    with pytest.raises(StepFailureError, match="nobody can be asked") as exc_info:
        execute_step(ctx, normalize_step({"wait_for_user": "Take a selfie"}))
    assert exc_info.value.failure_category == "environment_configuration"


def test_broker_rejects_bad_answers() -> None:
    b = handoff.HandoffBroker()
    b.enable_web()
    assert b.respond("done") is False  # nothing waiting
    got: dict[str, Any] = {}

    def ask() -> None:
        got["res"] = b.request("CODE", timeout=5, kind="input")

    t = threading.Thread(target=ask)
    t.start()
    for _ in range(100):
        if b.pending():
            break
        time.sleep(0.02)
    pend = b.pending()
    assert pend and pend["kind"] == "input"
    assert b.respond("done", value="") is False  # an input needs a value
    assert b.respond("done", value="1", request_id=pend["id"] + 1) is False  # stale request
    assert b.respond("maybe", value="1") is False
    assert b.respond("done", value="777", request_id=pend["id"]) is True
    t.join()
    assert got["res"].value == "777"


def test_validator_checks_wait_for_user(tmp_path: Path) -> None:
    f = tmp_path / "flows" / "kyc.md"
    issues: list = []
    _validate_step_shape(StepItem(type="wait_for_user", params={}), f, "step 1", issues, True)
    assert any("requires 'prompt'" in i.message for i in issues)

    issues = []
    _validate_step_shape(StepItem(type="wait_for_user", params={"prompt": "x", "target": "a.b"}), f, "s", issues, True)
    assert any("needs 'input'" in i.message for i in issues)

    issues = []
    _validate_step_shape(StepItem(type="wait_for_user", params={"prompt": "x", "input": "1bad"}), f, "s", issues, True)
    assert any("variable name" in i.message for i in issues)

    issues = []
    _validate_step_shape(StepItem(type="wait_for_user", params={"prompt": "x", "timeout": 900}), f, "s", issues, True)
    assert issues == []

    screens = ScreensCatalog({"kyc": {"next_btn": {"accessibility_id": "next"}}})
    issues = []
    _validate_locator_and_env(
        StepItem(type="wait_for_user", params={"prompt": "x", "until": "kyc.missing"}),
        ["android"], screens, None, {}, set(), f, "s", issues,
    )
    assert any("Unknown locator 'kyc.missing'" in i.message for i in issues)


def test_recorder_saves_wait_for_user_step() -> None:
    step = {"action": "wait_for_user", "target": "", "value": "Scan your ID", "until": "kyc.next_btn"}
    assert _step_to_yaml_dict(step) == {"wait_for_user": {"prompt": "Scan your ID", "until": "kyc.next_btn"}}
    assert normalize_step(_step_to_yaml_dict(step)).params["prompt"] == "Scan your ID"


def test_report_marks_manual_steps() -> None:
    assert _manual_label({}) == ""
    assert _manual_label({"manual": True, "answer": "done", "waited_sec": 42.4}) == "Done by a person after 42s"
    assert _manual_label({"manual": True, "answer": "preset"}) == "Value from dataset or .env"
