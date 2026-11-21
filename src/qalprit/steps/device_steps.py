"""Device/simulator-specific step handlers: biometric, set_camera_image."""

from __future__ import annotations

from pathlib import Path

from qalprit.camera_injection import VALID_CAMERAS, set_android_camera_image
from qalprit.spec_loader import StepItem
from qalprit.steps.base import StepContext, StepFailureError, StepOutcome


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


def _resolve_camera_image_path(ctx: StepContext, raw_image: str) -> Path:
    candidate = Path(raw_image).expanduser()
    if candidate.is_absolute():
        return candidate
    if ctx.run_dir is not None:
        ws_candidate = (ctx.run_dir.parent.parent / candidate).resolve()
        if ws_candidate.is_file():
            return ws_candidate
    return (Path.cwd() / candidate).resolve()


def exec_set_camera_image(ctx: StepContext, step: StepItem) -> StepOutcome:
    """Inject a still image into the Android emulator's front or back camera."""
    if ctx.target_type == "device" or ctx.platform != "android":
        return StepOutcome(
            status="skipped",
            note="Camera image injection is only supported on Android emulator",
        )

    raw_image = str(step.params.get("image") or step.params.get("path") or "").strip()
    if not raw_image:
        raise StepFailureError(
            "'set_camera_image' requires an 'image' path",
            failure_category="config_error",
        )

    camera = str(step.params.get("camera") or "back").strip().lower()
    if camera not in VALID_CAMERAS:
        raise StepFailureError(
            f"'set_camera_image.camera' must be 'front' or 'back', got {camera!r}",
            failure_category="config_error",
        )

    resolved = _resolve_camera_image_path(ctx, raw_image)
    if not resolved.is_file():
        raise StepFailureError(
            f"Camera image file not found: {resolved}",
            failure_category="config_error",
            diagnostics={"image": raw_image, "resolved_path": str(resolved), "camera": camera},
        )

    udid = ctx.profile.udid
    if not udid:
        caps = getattr(ctx.driver, "capabilities", None)
        if isinstance(caps, dict):
            udid = str(caps.get("udid") or "").strip() or None

    slot_path = set_android_camera_image(resolved, camera=camera, udid=udid)
    return StepOutcome(note=f"Set Android {camera} camera image to {resolved.name} ({slot_path})")
