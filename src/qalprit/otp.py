"""Old name for the hand-off broker. ``enter_otp`` and ``wait_for_user`` share it.

Kept so existing imports (``from qalprit.otp import BROKER``) keep working. New code should
import from :mod:`qalprit.handoff`.
"""

from __future__ import annotations

from qalprit.handoff import BROKER, HandoffBroker, HandoffUnavailableError

OtpBroker = HandoffBroker
OtpUnavailableError = HandoffUnavailableError

__all__ = ["BROKER", "OtpBroker", "OtpUnavailableError"]
