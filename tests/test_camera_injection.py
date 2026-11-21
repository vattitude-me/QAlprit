"""Tests for Android emulator camera image injection and set_camera_image step."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from qalprit import camera_injection
from qalprit.config_loader import DeviceProfile, EnvironmentConfig, ScreensCatalog
from qalprit.env_loader import EnvContext, SecretMasker
from qalprit.evidence import EvidenceCollector
from qalprit.spec_loader import StepItem, normalize_step
from qalprit.steps import execute_step, format_step_description
from qalprit.steps.base import StepContext, StepFailureError
from qalprit.validator import _validate_step_shape
from tests.fake_driver import FakeDriver


def _make_ctx(
    tmp_path: Path,
    *,
    platform: str = "android",
    target_type: str = "simulator",
) -> StepContext:
    run_dir = tmp_path / "results" / "run-1"
    run_dir.mkdir(parents=True, exist_ok=True)
    profile = DeviceProfile(
        name="pixel",
        platform=platform,
        target_type=target_type,
        device_name="Pixel",
        udid="emulator-5554" if platform == "android" else "SIM-1",
    )
    env_cfg = EnvironmentConfig(name="qa")
    env_ctx = EnvContext(variables={}, masker=SecretMasker([]))
    screens = ScreensCatalog({})
    evidence = EvidenceCollector(
        run_dir=run_dir,
        screens=screens,
        platform=platform,
        env_ctx=env_ctx,
        extra_env_vars={},
        masker=env_ctx.masker,
    )
    return StepContext(
        driver=FakeDriver(),
        platform=platform,
        target_type=target_type,
        profile=profile,
        env_cfg=env_cfg,
        env_ctx=env_ctx,
        screens=screens,
        evidence=evidence,
        run_dir=run_dir,
        masker=env_ctx.masker,
        app_id="com.example.app",
        app_path=None,
        reset_mode="none",
        reset_fn=lambda: None,
    )


def test_camera_slots_and_args(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QALPRIT_DATA_DIR", str(tmp_path / "qalprit-data"))
    back_path, front_path = camera_injection.ensure_camera_slots()
    assert back_path.is_file()
    assert front_path.is_file()

    args = camera_injection.get_android_camera_args()
    assert args == [
        "-camera-back",
        "virtualscene",
        "-virtualscene-poster",
        f"wall={back_path}",
    ]
    args_str = camera_injection.get_android_camera_args_string()
    assert "-camera-back" in args_str
    assert "-virtualscene-poster" in args_str


def test_set_android_camera_image_converts_jpeg_and_calls_adb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("QALPRIT_DATA_DIR", str(tmp_path / "qalprit-data"))
    src_jpg = tmp_path / "selfie.jpg"
    Image.new("RGB", (120, 90), color=(10, 120, 200)).save(src_jpg, format="JPEG")

    adb_calls: list[list[str]] = []
    monkeypatch.setattr(camera_injection, "find_android_tool", lambda name: "/usr/bin/adb" if name == "adb" else None)

    def _fake_run(cmd, **kwargs):
        adb_calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(camera_injection.subprocess, "run", _fake_run)

    slot = camera_injection.set_android_camera_image(src_jpg, camera="front", udid="emulator-5554")
    assert slot == camera_injection.get_camera_slot_path("front")
    assert slot.is_file()
    with Image.open(slot) as out_img:
        assert out_img.format == "PNG"
        assert out_img.size == (120, 90)
    assert adb_calls == [
        ["/usr/bin/adb", "-s", "emulator-5554", "emu", "virtualscene", "poster", "wall", str(slot)]
    ]


def test_normalize_validate_and_format_set_camera_image(tmp_path: Path) -> None:
    shorthand = normalize_step({"set_camera_image": "fixtures/id_card.png"})
    assert shorthand.type == "set_camera_image"
    assert shorthand.params == {"image": "fixtures/id_card.png", "camera": "back"}
    assert "back camera image" in format_step_description(shorthand)

    explicit = normalize_step({"set_camera_image": {"image": "fixtures/selfie.png", "camera": "front"}})
    assert explicit.params == {"image": "fixtures/selfie.png", "camera": "front"}
    assert "front camera image" in format_step_description(explicit)

    issues: list = []
    _validate_step_shape(explicit, tmp_path / "specs" / "kyc.md", "step 1", issues, True)
    assert issues == []

    bad_cam = StepItem(type="set_camera_image", params={"image": "a.png", "camera": "side"})
    _validate_step_shape(bad_cam, tmp_path / "specs" / "kyc.md", "step 2", issues, True)
    assert any("front' or 'back" in i.message for i in issues)

    missing_img = StepItem(type="set_camera_image", params={"camera": "back"})
    _validate_step_shape(missing_img, tmp_path / "specs" / "kyc.md", "step 3", issues, True)
    assert any("requires 'image'" in i.message for i in issues)


def test_execute_set_camera_image_step(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QALPRIT_DATA_DIR", str(tmp_path / "qalprit-data"))
    monkeypatch.setattr(camera_injection, "find_android_tool", lambda _name: None)

    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    id_png = fixtures / "id_card.png"
    Image.new("RGB", (64, 64), color=(255, 0, 0)).save(id_png, format="PNG")

    ctx = _make_ctx(tmp_path, platform="android", target_type="simulator")
    outcome = execute_step(ctx, StepItem(type="set_camera_image", params={"image": "fixtures/id_card.png", "camera": "back"}))
    assert outcome.status == "passed"
    assert camera_injection.get_camera_slot_path("back").is_file()

    # Skips cleanly on iOS or real device
    ios_ctx = _make_ctx(tmp_path, platform="ios", target_type="simulator")
    assert execute_step(ios_ctx, StepItem(type="set_camera_image", params={"image": "fixtures/id_card.png"})).status == "skipped"

    dev_ctx = _make_ctx(tmp_path, platform="android", target_type="device")
    assert execute_step(dev_ctx, StepItem(type="set_camera_image", params={"image": "fixtures/id_card.png"})).status == "skipped"

    # Missing file raises config_error
    with pytest.raises(StepFailureError) as exc_info:
        execute_step(ctx, StepItem(type="set_camera_image", params={"image": "fixtures/missing.png"}))
    assert exc_info.value.failure_category == "config_error"
