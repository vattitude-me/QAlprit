"""Hand-off point for anything only a person can do during a run.

Two kinds of request share one broker:

* ``input``: the person types a value (a one-time code, a name, an answer) and the run uses it.
* ``action``: the person does something on the device (takes a selfie, scans an ID card, signs
  in with a fingerprint) and then says Done, Skip or Fail. A ``poll`` callback can finish the
  wait on its own, for example when the next screen of the app shows up.

The Workbench shows the request on the Run page and posts the answer back. A terminal run asks
on the console. A run with neither fails at once with a clear message instead of hanging.
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

HandoffKind = Literal["input", "action"]
HandoffAnswer = Literal["done", "skip", "fail", "auto"]

# Appium closes a session after ``new_command_timeout`` (180 s) without a command, so a long
# wait has to touch the driver now and then. The poll callback does that.
DEFAULT_POLL_EVERY = 2.0


class HandoffUnavailableError(RuntimeError):
    """No way to ask a person, nobody answered in time, or the run was stopped."""


class HandoffFailedError(RuntimeError):
    """The person said the step failed."""


@dataclass
class HandoffResult:
    answer: HandoffAnswer
    value: str = ""
    note: str = ""
    waited_sec: float = 0.0


class HandoffBroker:
    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._pending: dict[str, Any] | None = None
        self._result: HandoffResult | None = None
        self._web = False
        self._seq = 0
        self._aborted = False

    def enable_web(self) -> None:
        """Called by the Workbench server: requests go to the browser, not the console."""
        self._web = True

    def pending(self) -> dict[str, Any] | None:
        with self._cond:
            if not self._pending:
                return None
            out = dict(self._pending)
            started = out.pop("_started", None)
            if started is not None:
                out["waited_sec"] = round(time.monotonic() - started, 1)
            return out

    def respond(self, answer: str, value: str = "", note: str = "", request_id: int | None = None) -> bool:
        """Answer the waiting request. Returns False when nothing is waiting or the answer is unusable."""
        answer = str(answer or "").strip().lower()
        value = str(value or "").strip()
        with self._cond:
            pend = self._pending
            if pend is None or answer not in ("done", "skip", "fail"):
                return False
            if request_id is not None and int(request_id) != int(pend["id"]):
                return False
            if answer == "done" and pend["kind"] == "input" and not value:
                return False
            self._result = HandoffResult(answer=answer, value=value, note=str(note or "").strip())  # type: ignore[arg-type]
            self._cond.notify_all()
            return True

    def submit(self, code: str) -> bool:
        """Answer an input request with a value (the one-time code endpoint uses this)."""
        return self.respond("done", value=code)

    def request(
        self,
        label: str,
        timeout: float = 120.0,
        *,
        kind: HandoffKind = "input",
        prompt: str = "",
        secret: bool = True,
        until: str = "",
        can_skip: bool = False,
        poll: Callable[[], bool] | None = None,
        poll_every: float | None = None,
    ) -> HandoffResult:
        """Wait for a person. ``poll`` returning True finishes an action request with ``auto``."""
        prompt = prompt or (f"Enter the value for '{label}'" if kind == "input" else f"Finish '{label}' on the device")
        started = time.monotonic()
        poll_every = DEFAULT_POLL_EVERY if poll_every is None else poll_every
        if not self._web:
            result = self._ask_console(label, kind=kind, prompt=prompt, secret=secret, can_skip=can_skip, poll=poll, poll_every=poll_every)
            result.waited_sec = round(time.monotonic() - started, 1)
            return self._finish(result)

        deadline = started + timeout
        last_poll = started
        with self._cond:
            self._seq += 1
            self._result = None
            self._aborted = False
            self._pending = {
                "id": self._seq,
                "kind": kind,
                "label": label,
                "prompt": prompt,
                "secret": bool(secret),
                "until": until,
                "can_skip": bool(can_skip),
                "timeout_sec": timeout,
                "_started": started,
            }
        try:
            while True:
                with self._cond:
                    if self._result is None and not self._aborted:
                        left = deadline - time.monotonic()
                        if left > 0:
                            self._cond.wait(timeout=min(0.5, left))
                    if self._aborted:
                        raise HandoffUnavailableError("Run stopped while waiting for a person.")
                    if self._result is not None:
                        result = self._result
                        break
                    if time.monotonic() >= deadline:
                        what = "value" if kind == "input" else "answer"
                        raise HandoffUnavailableError(f"No {what} for '{label}' within {int(timeout)}s.")
                # Poll outside the lock so an answer from the UI is never held up by the device.
                if poll is not None and time.monotonic() - last_poll >= poll_every:
                    last_poll = time.monotonic()
                    try:
                        hit = bool(poll())
                    except Exception:
                        hit = False
                    if hit and kind == "action":
                        result = HandoffResult(answer="auto")
                        break
        finally:
            with self._cond:
                self._pending = None
                self._result = None
        result.waited_sec = round(time.monotonic() - started, 1)
        return self._finish(result)

    @staticmethod
    def _finish(result: HandoffResult) -> HandoffResult:
        if result.answer == "fail":
            why = f": {result.note}" if result.note else "."
            raise HandoffFailedError(f"Marked as failed by the person running the test{why}")
        return result

    def _ask_console(
        self,
        label: str,
        *,
        kind: HandoffKind,
        prompt: str,
        secret: bool,
        can_skip: bool,
        poll: Callable[[], bool] | None,
        poll_every: float,
    ) -> HandoffResult:
        if sys.stdin is None or not sys.stdin.isatty():
            raise HandoffUnavailableError(
                f"'{label}' needs a person and nobody can be asked. Set the value in the environment "
                "or dataset, or run from the Workbench or a terminal."
            )
        # Keep the Appium session alive while the console blocks on the person.
        stop = threading.Event()

        def _keepalive() -> None:
            while not stop.wait(max(poll_every, 20.0)):
                if poll is not None:
                    try:
                        poll()
                    except Exception:
                        pass

        keeper = threading.Thread(target=_keepalive, name="qalprit-handoff-keepalive", daemon=True)
        keeper.start()
        try:
            if kind == "input":
                if secret:
                    import getpass

                    value = getpass.getpass(f"{prompt}: ").strip()
                else:
                    value = input(f"{prompt}: ").strip()
                if not value:
                    raise HandoffUnavailableError(f"No value entered for '{label}'.")
                return HandoffResult(answer="done", value=value)
            hint = "Enter when done" + (", s to skip" if can_skip else "") + ", f to fail"
            reply = input(f"{prompt} ({hint}): ").strip().lower()
            if reply in ("f", "fail"):
                return HandoffResult(answer="fail")
            if can_skip and reply in ("s", "skip"):
                return HandoffResult(answer="skip")
            return HandoffResult(answer="done")
        finally:
            stop.set()

    def abort(self) -> None:
        """Wake a waiting step so a stopped run does not sit out the full timeout."""
        with self._cond:
            self._aborted = True
            self._cond.notify_all()


BROKER = HandoffBroker()
