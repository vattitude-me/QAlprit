"""In-memory Pillow bounding-box screenshot redaction, compression, and WebView detection."""

from __future__ import annotations

import base64
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw

from qalprit.config_loader import ScreensCatalog
from qalprit.env_loader import EnvContext, SecretMasker

_WEBVIEW_RE = re.compile(
    r"(android\.webkit\.WebView|XCUIElementTypeWebView|class=[\"'][^\"']*WebView[^\"']*[\"'])",
    re.IGNORECASE,
)


@dataclass
class ScreenshotCaptureResult:
    """Detailed result of screenshot capture and in-memory redaction."""

    rel_path: str | None
    redacted_count: int
    redaction_status: str = "none_needed"
    warning: str = ""


def _redact_sensitive_xml(
    xml_text: str,
    screens: ScreensCatalog,
    platform: str,
    env_ctx: EnvContext,
    extra_env_vars: dict[str, str],
) -> str:
    """Scrub text/value attributes of XML nodes matching sensitive:true locators."""
    if not xml_text.strip():
        return xml_text
    sensitive_values: list[tuple[str, str]] = []
    for target in screens.sensitive_targets(platform):
        try:
            resolved = screens.resolve(target, platform, env_ctx=env_ctx, extra_vars=extra_env_vars)
            sensitive_values.append((resolved.strategy, resolved.value))
        except Exception:
            continue
    if not sensitive_values:
        return xml_text

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return xml_text

    id_attrs = ("resource-id", "name", "content-desc", "accessibility-id", "id")
    value_attrs = ("text", "value", "label", "content-desc", "hint")
    modified = False
    for node in root.iter():
        matched = False
        for strat, val in sensitive_values:
            if strat in ("accessibility_id", "id"):
                for attr in id_attrs:
                    if node.attrib.get(attr) == val:
                        matched = True
                        break
            elif strat == "text" and node.attrib.get("text") == val:
                matched = True
            if matched:
                break
        if matched:
            for attr in value_attrs:
                if attr in node.attrib and node.attrib[attr]:
                    node.attrib[attr] = SecretMasker.REPLACEMENT
                    modified = True
            if node.text and node.text.strip():
                node.text = SecretMasker.REPLACEMENT
                modified = True

    if not modified:
        return xml_text
    return ET.tostring(root, encoding="unicode")


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
        *,
        secure_evidence: bool = True,
        save_page_source_on_failure: bool = True,
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
        self.secure_evidence = secure_evidence
        self.save_page_source_on_failure = save_page_source_on_failure

    def _collect_sensitive_boxes_with_diagnostics(
        self,
        driver: Any,
        extra_secret_elements: list[Any] | None = None,
    ) -> tuple[list[dict[str, int | float]], int, list[str]]:
        boxes: list[dict[str, int | float]] = []
        seen_rects: set[tuple[int, int, int, int]] = set()
        invalid_count = 0
        warnings: list[str] = []

        def _add_elem(el: Any, *, source_label: str) -> None:
            nonlocal invalid_count
            try:
                if hasattr(el, "is_displayed") and not el.is_displayed():
                    return
                rect = el.rect
                if not isinstance(rect, dict):
                    invalid_count += 1
                    warnings.append(f"Sensitive element '{source_label}' missing dict rect bounds")
                    return
                x = int(round(float(rect.get("x", 0))))
                y = int(round(float(rect.get("y", 0))))
                w = int(round(float(rect.get("width", 0))))
                h = int(round(float(rect.get("height", 0))))
                if w <= 0 or h <= 0:
                    invalid_count += 1
                    warnings.append(
                        f"Sensitive element '{source_label}' has invalid geometry ({w}x{h})"
                    )
                    return
                key = (x, y, w, h)
                if key not in seen_rects:
                    seen_rects.add(key)
                    boxes.append({"x": x, "y": y, "width": w, "height": h})
            except Exception as exc:
                invalid_count += 1
                warnings.append(f"Failed inspecting sensitive element '{source_label}': {exc}")
                return

        for idx, elem in enumerate(extra_secret_elements or []):
            _add_elem(elem, source_label=f"step_secret[{idx}]")

        for target in self.screens.sensitive_targets(self.platform):
            try:
                resolved = self.screens.resolve(
                    target,
                    self.platform,
                    env_ctx=self.env_ctx,
                    extra_vars=self.extra_env_vars,
                )
                for elem in driver.find_elements(resolved.strategy, resolved.value):
                    _add_elem(elem, source_label=target)
            except KeyError:
                continue
            except Exception as exc:
                invalid_count += 1
                warnings.append(f"Sensitive locator query failed for '{target}': {exc}")
        return boxes, invalid_count, warnings

    def _collect_sensitive_boxes(
        self,
        driver: Any,
        extra_secret_elements: list[Any] | None = None,
    ) -> list[dict[str, int | float]]:
        boxes, _, _ = self._collect_sensitive_boxes_with_diagnostics(
            driver,
            extra_secret_elements=extra_secret_elements,
        )
        return boxes

    def capture_screenshot_detailed(
        self,
        driver: Any,
        rel_stem: str,
        extra_secret_elements: list[Any] | None = None,
    ) -> ScreenshotCaptureResult:
        """Capture PNG in memory, fail-safe redact sensitive bounds, and write to disk."""
        try:
            raw_png = driver.get_screenshot_as_png()
        except Exception as exc:
            return ScreenshotCaptureResult(
                rel_path=None,
                redacted_count=0,
                redaction_status="failed",
                warning=f"Driver screenshot capture failed: {exc}",
            )

        try:
            window_size = driver.get_window_size()
        except Exception:
            window_size = None

        boxes, invalid_count, warnings = self._collect_sensitive_boxes_with_diagnostics(
            driver,
            extra_secret_elements=extra_secret_elements,
        )

        if invalid_count > 0 and self.secure_evidence:
            return ScreenshotCaptureResult(
                rel_path=None,
                redacted_count=0,
                redaction_status="skipped_secure_mode",
                warning="; ".join(warnings),
            )

        try:
            redacted_png, count = redact_screenshot_bytes(raw_png, boxes, window_size)
        except Exception as exc:
            msg = f"Screenshot redaction failed: {exc}"
            if self.secure_evidence and (boxes or invalid_count > 0 or extra_secret_elements):
                return ScreenshotCaptureResult(
                    rel_path=None,
                    redacted_count=0,
                    redaction_status="skipped_secure_mode",
                    warning=msg,
                )
            return ScreenshotCaptureResult(
                rel_path=None,
                redacted_count=0,
                redaction_status="failed",
                warning=msg,
            )

        if boxes and count == 0:
            msg = "Sensitive element bounds fell outside screenshot area; 0 boxes drawn"
            warnings.append(msg)
            if self.secure_evidence:
                return ScreenshotCaptureResult(
                    rel_path=None,
                    redacted_count=0,
                    redaction_status="skipped_secure_mode",
                    warning="; ".join(warnings),
                )

        out_path = self.screenshots_dir / f"{rel_stem}.png"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(redacted_png)

        if warnings:
            status = "warning"
        elif count > 0:
            status = "redacted"
        else:
            status = "none_needed"

        return ScreenshotCaptureResult(
            rel_path=out_path.relative_to(self.run_dir).as_posix(),
            redacted_count=count,
            redaction_status=status,
            warning="; ".join(warnings),
        )

    def capture_screenshot(
        self,
        driver: Any,
        rel_stem: str,
        extra_secret_elements: list[Any] | None = None,
    ) -> tuple[str | None, int]:
        """Backward-compatible wrapper returning (rel_path, redacted_count)."""
        res = self.capture_screenshot_detailed(
            driver,
            rel_stem,
            extra_secret_elements=extra_secret_elements,
        )
        return res.rel_path, res.redacted_count

    def capture_page_source(
        self,
        driver: Any,
        rel_stem: str,
        save_to_disk: bool = True,
    ) -> tuple[str | None, bool, list[str]]:
        """Read page source, detect WebViews, scrub sensitive nodes & secrets, and write to disk."""
        try:
            xml_text = driver.page_source or ""
        except Exception:
            return None, False, []

        webview_detected, webview_details = detect_webviews(xml_text)
        if not save_to_disk or not self.save_page_source_on_failure:
            return None, webview_detected, webview_details

        xml_scrubbed = _redact_sensitive_xml(
            xml_text,
            self.screens,
            self.platform,
            self.env_ctx,
            self.extra_env_vars,
        )
        masked_xml = self.masker.mask(xml_scrubbed)
        out_path = self.page_sources_dir / f"{rel_stem}.xml"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(masked_xml, encoding="utf-8")
        return out_path.relative_to(self.run_dir).as_posix(), webview_detected, webview_details
