"""Hand-off point for one-time codes that only a person can supply during a run.

The ``enter_otp`` step asks the broker for a code and waits. The Workbench shows a prompt and
posts the code back. A terminal run asks on the console. A run with neither fails with a
clear message instead of hanging.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Any


class OtpUnavailableError(RuntimeError):
    """No way to ask a person for the code, or nobody answered in time."""


class OtpBroker:
    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._pending: dict[str, Any] | None = None
        self._code: str | None = None
        self._web = False
        self._seq = 0
        self._aborted = False

    def enable_web(self) -> None:
        """Called by the Workbench server: prompts go to the browser, not the console."""
        self._web = True

    def pending(self) -> dict[str, Any] | None:
        with self._cond:
            return dict(self._pending) if self._pending else None

    def submit(self, code: str) -> bool:
        code = str(code or "").strip()
        with self._cond:
            if self._pending is None or not code:
                return False
            self._code = code
            self._cond.notify_all()
            return True

    def request(
        self,
        label: str,
        timeout: float = 120.0,
    ) -> str:
        if not self._web:
            if sys.stdin is not None and sys.stdin.isatty():
                import getpass

                code = getpass.getpass(f"Enter the code for '{label}': ").strip()
                if code:
                    return code
                raise OtpUnavailableError("No code entered.")
            raise OtpUnavailableError(
                "This step needs a one-time code and nobody can be asked. Set OTP in the "
                "environment or dataset, or run from the Workbench or a terminal."
            )
        deadline = time.monotonic() + timeout
        with self._cond:
            self._seq += 1
            self._code = None
            self._aborted = False
            self._pending = {"id": self._seq, "label": label, "timeout_sec": timeout}
            try:
                while self._code is None:
                    if self._aborted:
                        raise OtpUnavailableError("Run stopped while waiting for the code.")
                    left = deadline - time.monotonic()
                    if left <= 0:
                        raise OtpUnavailableError(f"No code was entered within {int(timeout)}s.")
                    self._cond.wait(timeout=min(0.5, left))
                return self._code
            finally:
                self._pending = None
                self._code = None

    def abort(self) -> None:
        """Wake a waiting step so a stopped run does not sit out the full timeout."""
        with self._cond:
            self._aborted = True
            self._cond.notify_all()


BROKER = OtpBroker()
