"""Run failure text: headline and hint for the Workbench, and fast failure on a dead device session."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from qalprit.error_text import explain_failure, is_session_dead
from qalprit.steps.base import StepTimeoutError, wait_for_element
from tests.fake_driver import FakeDriver
from tests.test_device_input import _ctx

UIA2_CRASH = (
    "Message: 'POST /elements' cannot be proxied to UiAutomator2 server because the instrumentation process "
    "is not running (probably crashed). Check the server log and/or the logcat output for more details\n"
    "Stacktrace:\nUnknownError: 'POST /elements' cannot be proxied\n    at UIA2Proxy.command (/opt/proxy.js:276:19)"
)


def test_uia2_crash_gets_plain_headline_and_keeps_raw_detail() -> None:
    reason = (
        "Timed out after 12.0s waiting for 'test1.accept_all_btn' on android [xpath='(//android.view.View)[7]'] "
        f"(state=not_found, matches=0, polls=33; last error: {UIA2_CRASH})"
    )
    out = explain_failure(reason, "locator_not_found")
    assert out["headline"] == "The connection to the phone dropped"
    assert "other recording or run" in out["hint"]
    assert "UIA2Proxy.command" in out["detail"]


def test_connection_refused_means_appium_stopped() -> None:
    out = explain_failure("HTTPConnectionPool(host='127.0.0.1', port=4723): Max retries exceeded (Connection refused)")
    assert out["headline"] == "Appium stopped during the run"


@pytest.mark.parametrize(
    ("value", "hint_word"),
    [
        ("'SelectAccountProductItem_d77607b0-a343-47b6-bb8c-2a54b9bb39d1'", "generated id"),
        ("'(//android.view.View)[7]'", "position on screen"),
        ("'com.app:id/verificationCodeEdit'", "Check the screenshot"),
    ],
)
def test_timeout_headline_names_the_element_and_flags_brittle_locators(value: str, hint_word: str) -> None:
    reason = f"Timed out after 12.0s waiting for 'tng.code' on android [id={value}] (state=not_found, matches=0, polls=40)"
    out = explain_failure(reason, "locator_not_found")
    assert out["headline"] == "Could not find tng.code within 12 s"
    assert hint_word in out["hint"]


def test_disabled_state_headline() -> None:
    reason = "Timed out after 5.0s waiting for 'login.submit' on ios [accessibility id='Submit'] (state=disabled, matches=1, polls=9)"
    assert explain_failure(reason)["headline"] == "login.submit was on screen but disabled within 5 s"


def test_empty_reason() -> None:
    assert explain_failure(None) == {"headline": "", "hint": "", "detail": ""}


def test_is_session_dead() -> None:
    assert is_session_dead(UIA2_CRASH)
    assert is_session_dead("InvalidSessionIdException: invalid session id")
    assert not is_session_dead("no such element: Unable to locate element")
    assert not is_session_dead(None)


class _DeadDriver(FakeDriver):
    def find_elements(self, strategy, value):  # type: ignore[override]
        raise RuntimeError(UIA2_CRASH)


def test_wait_for_element_stops_at_once_when_session_is_dead(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, _DeadDriver(platform="android"))
    ctx.env_cfg.default_timeout = 5.0
    started = time.monotonic()
    with pytest.raises(StepTimeoutError) as info:
        wait_for_element(ctx, "home.item_row")
    assert time.monotonic() - started < 1.0
    assert info.value.failure_category == "appium_session_failure"
    msg = str(info.value)
    assert msg.startswith("Lost the device session while waiting for 'home.item_row'")
    assert "UIA2Proxy.command" not in msg
    assert explain_failure(msg, "appium_session_failure")["headline"] == "The connection to the phone dropped"
