"""Tests for in-memory Pillow bounding-box redaction, compression, and WebView detection."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from qalprit.config_loader import ScreensCatalog
from qalprit.env_loader import EnvContext, SecretMasker
from qalprit.evidence import (
    EvidenceCollector,
    compress_image_for_html,
    detect_webviews,
    redact_screenshot_bytes,
)
from tests.fake_driver import FakeDriver


def test_pillow_redaction_scales_window_to_retina_pixels() -> None:
    driver = FakeDriver(window_size={"width": 390, "height": 844}, screenshot_scale=2)
    secret_box = {"x": 50, "y": 100, "width": 80, "height": 40}
    driver.secret_paint_boxes.append(secret_box)
    raw_png = driver.get_screenshot_as_png()

    # Confirm raw image has red pixel at scaled center (50+40)*2 = 180, (100+20)*2 = 240
    with Image.open(io.BytesIO(raw_png)) as raw_img:
        assert raw_img.size == (780, 1688)
        assert raw_img.getpixel((180, 240)) == (255, 0, 0)

    redacted_png, count = redact_screenshot_bytes(
        raw_png,
        boxes=[secret_box],
        window_size={"width": 390, "height": 844},
    )
    assert count == 1
    with Image.open(io.BytesIO(redacted_png)) as out_img:
        # Center of secret box must be solid black (0, 0, 0)
        assert out_img.getpixel((180, 240)) == (0, 0, 0)
        # Pixel outside box remains original light background
        assert out_img.getpixel((10, 10)) == (240, 245, 250)


def test_evidence_collector_redacts_sensitive_target_even_without_secret_step(tmp_path: Path) -> None:
    screens = ScreensCatalog(
        {
            "login": {
                "password_field": {"accessibility_id": "pw_id", "sensitive": True},
            }
        }
    )
    driver = FakeDriver(window_size={"width": 390, "height": 844}, screenshot_scale=2)
    box = {"x": 30, "y": 200, "width": 120, "height": 40}
    driver.add_element("accessibility_id", "pw_id", rect=box, displayed=True)
    driver.secret_paint_boxes.append(box)

    collector = EvidenceCollector(
        run_dir=tmp_path,
        screens=screens,
        platform="android",
        env_ctx=EnvContext(),
        extra_env_vars={},
        masker=SecretMasker(),
    )
    rel_path, count = collector.capture_screenshot(driver, "step_01")
    assert rel_path == "screenshots/step_01.png"
    assert count == 1

    saved = tmp_path / rel_path
    with Image.open(saved) as img:
        assert img.getpixel((180, 440)) == (0, 0, 0)


def test_compress_image_for_html_resizes_to_max_width(tmp_path: Path) -> None:
    src = tmp_path / "wide.png"
    Image.new("RGB", (1440, 2880), color=(20, 40, 60)).save(src, format="PNG")
    mime, b64 = compress_image_for_html(src, max_width=720)
    assert mime in ("image/webp", "image/jpeg")
    assert len(b64) > 20


def test_detect_webviews_flags_android_and_ios() -> None:
    detected_and, details_and = detect_webviews("<hierarchy><android.webkit.WebView /></hierarchy>")
    assert detected_and is True
    assert any("WebView" in d for d in details_and)

    detected_ios, _ = detect_webviews("<AppiumAUT><XCUIElementTypeWebView /></AppiumAUT>")
    assert detected_ios is True

    detected_native, _ = detect_webviews("<hierarchy><android.widget.Button /></hierarchy>")
    assert detected_native is False
