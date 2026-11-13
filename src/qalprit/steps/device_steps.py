"""Device/simulator-specific step handlers: biometric."""

from __future__ import annotations

from qalprit.spec_loader import StepItem
from qalprit.steps.base import StepContext, StepOutcome


def exec_biometric(ctx: StepContext, step: StepItem) -> StepOutcome:
    action = step.params["action"]
    if ctx.target_type == "device":
        return StepOutcome(
            status="skipped",
            note="Biometric simulation skipped on real device target",
        )

    if ctx.platform == "ios":
        if action == "enroll":
            ctx.driver.execute_script("mobile: enrollBiometric", {"isEnabled": True})
        elif action == "match":
            ctx.driver.execute_script("mobile: sendBiometricMatch", {"type": "faceId", "match": True})
        elif action == "fail":
            ctx.driver.execute_script("mobile: sendBiometricMatch", {"type": "faceId", "match": False})
    else:
        # Android emulator fingerprint simulation (slot 1 for match, slot 2 for unconfigured fail)
        if action == "enroll":
            # Emulator fingerprints 1..10 are built-in; enroll is a safe no-op/touch
            ctx.driver.execute_script("mobile: fingerprint", {"fingerprintId": 1})
        elif action == "match":
            ctx.driver.execute_script("mobile: fingerprint", {"fingerprintId": 1})
        elif action == "fail":
            ctx.driver.execute_script("mobile: fingerprint", {"fingerprintId": 99})

    return StepOutcome()
