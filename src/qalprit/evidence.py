"""In-memory Pillow bounding-box screenshot redaction, compression, and WebView detection."""

from __future__ import annotations

import base64
import io
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from qalprit.config_loader import ScreensCatalog
from qalprit.env_loader import EnvContext, SecretMasker

_WEBVIEW_RE = re.compile(
    r"(android\.webkit\.WebView|XCUIElementTypeWebView|class=[\"'][^\"']*WebView[^\"']*[\"'])",
    re.IGNORECASE,
)


def detect_webviews(page_source: str | None) -> tuple[bool, list[str]]:
    """Inspect XML page source for embedded WebView components (C8 flagging)."""
    if not page_source:
        return False, []
    matches = sorted(set(_WEBVIEW_RE.findall(page_source)))
    return bool(matches), matches


def redact_screenshot_bytes(
    raw_png: bytes,
    boxes: list[dict[str, int | float]],
    window_size: dict[str, int | float] | None = None,
) -> tuple[bytes, int]:
    """Redact bounding boxes in memory via Pillow before any disk write.

    Scales Appium logical window coordinates to screenshot pixel coordinates
    using ``screenshot_width / window_width`` and ``screenshot_height / window_height``.
    """
    with Image.open(io.BytesIO(raw_png)) as img:
        working = img.convert("RGB")
        if not boxes:
            out = io.BytesIO()
            working.save(out, format="PNG")
            return out.getvalue(), 0

        scale_x = 1.0
        scale_y = 1.0
        if window_size:
            win_w = float(window_size.get("width") or 0)
            win_h = float(window_size.get("height") or 0)
            if win_w > 0 and win_h > 0:
                scale_x = working.width / win_w
                scale_y = working.height / win_h

        draw = ImageDraw.Draw(working)
        redacted = 0
        for box in boxes:
            x = float(box.get("x", 0))
            y = float(box.get("y", 0))
            w = float(box.get("width", 0))
            h = float(box.get("height", 0))
            if w <= 0 or h <= 0:
                continue
            x0 = max(0, int(round(x * scale_x)))
            y0 = max(0, int(round(y * scale_y)))
            x1 = min(working.width, int(round((x + w) * scale_x)))
            y1 = min(working.height, int(round((y + h) * scale_y)))
            if x1 > x0 and y1 > y0:
                draw.rectangle([x0, y0, x1, y1], fill=(0, 0, 0), outline=(40, 40, 40), width=2)
                redacted += 1

        out = io.BytesIO()
        working.save(out, format="PNG")
        return out.getvalue(), redacted


def compress_image_for_html(
    image_path: Path,
    max_width: int = 720,
    quality: int = 75,
) -> tuple[str, str]:
    """Resize and compress a redacted PNG in memory for self-contained report.html."""
    with Image.open(image_path) as img:
        working = img.convert("RGB")
        if working.width > max_width:
            ratio = max_width / float(working.width)
            new_height = max(1, int(round(working.height * ratio)))
            working = working.resize((max_width, new_height), Image.Resampling.LANCZOS)

        buf = io.BytesIO()
        try:
            working.save(buf, format="WEBP", quality=quality, method=4)
            mime = "image/webp"
        except Exception:
            buf = io.BytesIO()
            working.save(buf, format="JPEG", quality=quality, optimize=True)
            mime = "image/jpeg"
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return mime, b64


class EvidenceCollector:
    """Collects redacted step screenshots and masked failure page sources."""

    def __init__(
        self,
        run_dir: Path,
        screens: ScreensCatalog,
        platform: str,
        env_ctx: EnvContext,
        extra_env_vars: dict[str, str],
        masker: SecretMasker,
    ) -> None:
        self.run_dir = run_dir
        self.screenshots_dir = run_dir / "screenshots"
        self.page_sources_dir = run_dir / "page_sources"
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.page_sources_dir.mkdir(parents=True, exist_ok=True)
        self.screens = screens
        self.platform = platform
        self.env_ctx = env_ctx
        self.extra_env_vars = extra_env_vars
        self.masker = masker

    def _collect_sensitive_boxes(
        self,
        driver: Any,
        extra_secret_elements: list[Any] | None = None,
    ) -> list[dict[str, int | float]]:
        boxes: list[dict[str, int | float]] = []
        seen_rects: set[tuple[int, int, int, int]] = set()

        def _add_elem(el: Any) -> None:
            try:
                if hasattr(el, "is_displayed") and not el.is_displayed():
                    return
                rect = el.rect
                if not isinstance(rect, dict):
                    return
                key = (
                    int(rect.get("x", 0)),
                    int(rect.get("y", 0)),
                    int(rect.get("width", 0)),
                    int(rect.get("height", 0)),
                )
                if key[2] > 0 and key[3] > 0 and key not in seen_rects:
                    seen_rects.add(key)
                    boxes.append(rect)
            except Exception:
                return

        for elem in extra_secret_elements or []:
            _add_elem(elem)

        for target in self.screens.sensitive_targets(self.platform):
            try:
                resolved = self.screens.resolve(
                    target,
                    self.platform,
                    env_ctx=self.env_ctx,
                    extra_vars=self.extra_env_vars,
                )
                for elem in driver.find_elements(resolved.strategy, resolved.value):
                    _add_elem(elem)
            except Exception:
                continue
        return boxes

    def capture_screenshot(
        self,
        driver: Any,
        rel_stem: str,
        extra_secret_elements: list[Any] | None = None,
    ) -> tuple[str | None, int]:
        """Capture PNG in memory, redact sensitive bounds, and write to disk."""
        try:
            raw_png = driver.get_screenshot_as_png()
        except Exception:
            return None, 0

        try:
            window_size = driver.get_window_size()
        except Exception:
            window_size = None

        boxes = self._collect_sensitive_boxes(driver, extra_secret_elements=extra_secret_elements)
        redacted_png, count = redact_screenshot_bytes(raw_png, boxes, window_size)

        out_path = self.screenshots_dir / f"{rel_stem}.png"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(redacted_png)
        return out_path.relative_to(self.run_dir).as_posix(), count

    def capture_page_source(
        self,
        driver: Any,
        rel_stem: str,
        save_to_disk: bool = True,
    ) -> tuple[str | None, bool, list[str]]:
        """Read page source, detect WebViews, mask secrets, and optionally write to disk."""
        try:
            xml_text = driver.page_source or ""
        except Exception:
            return None, False, []

        webview_detected, webview_details = detect_webviews(xml_text)
        if not save_to_disk:
            return None, webview_detected, webview_details

        masked_xml = self.masker.mask(xml_text)
        out_path = self.page_sources_dir / f"{rel_stem}.xml"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(masked_xml, encoding="utf-8")
        return out_path.relative_to(self.run_dir).as_posix(), webview_detected, webview_details
