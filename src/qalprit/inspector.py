"""Element tree inspector (qalprit inspect) and screens.yaml suggestion generator."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from qalprit.evidence import detect_webviews


@dataclass
class InspectedElement:
    tag: str
    accessibility_id: str
    resource_id: str
    text: str
    bounds: str
    clickable: bool
    enabled: bool
    visible: bool

    def suggested_key(self) -> str:
        raw = self.accessibility_id or self.resource_id.split("/")[-1] or self.text or self.tag
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", raw).strip("_").lower()
        return slug[:40] or "element"


def parse_page_source_elements(xml_text: str) -> tuple[list[InspectedElement], bool, list[str]]:
    """Extract actionable/labeled elements and detect WebViews from Appium XML page source."""
    webview_detected, webview_details = detect_webviews(xml_text)
    elements: list[InspectedElement] = []
    if not xml_text or not xml_text.strip():
        return elements, webview_detected, webview_details

    root = ET.fromstring(xml_text)
    for node in root.iter():
        attrs = node.attrib
        acc_id = (
            attrs.get("content-desc")
            or attrs.get("accessibilityId")
            or attrs.get("name")
            or ""
        ).strip()
        res_id = (attrs.get("resource-id") or "").strip()
        text = (
            attrs.get("text")
            or attrs.get("label")
            or attrs.get("value")
            or ""
        ).strip()
        bounds = (
            attrs.get("bounds")
            or (
                f"[{attrs.get('x')},{attrs.get('y')}][{attrs.get('width')}x{attrs.get('height')}]"
                if "x" in attrs and "width" in attrs
                else ""
            )
        )
        clickable = str(attrs.get("clickable", "")).lower() == "true"
        enabled = str(attrs.get("enabled", "true")).lower() != "false"
        visible = str(attrs.get("visible", attrs.get("displayed", "true"))).lower() != "false"

        if acc_id or res_id or text or clickable:
            elements.append(
                InspectedElement(
                    tag=node.tag,
                    accessibility_id=acc_id,
                    resource_id=res_id,
                    text=text,
                    bounds=bounds,
                    clickable=clickable,
                    enabled=enabled,
                    visible=visible,
                )
            )
    return elements, webview_detected, webview_details


def format_inspect_output(xml_text: str, screen_name: str = "screen") -> str:
    """Format inspected elements and a starter screens.yaml snippet."""
    elements, webview_detected, webview_details = parse_page_source_elements(xml_text)
    lines: list[str] = []
    if webview_detected:
        lines.append(
            f"[WARNING] WebView detected in page source: {', '.join(webview_details)} "
            "(pure-native v1 scope — flag before expanding scope)"
        )
        lines.append("")

    lines.append(f"Found {len(elements)} addressable element(s):")
    lines.append(f"{'TAG':<28} {'ACC_ID':<26} {'RES_ID':<28} {'TEXT':<28} {'BOUNDS'}")
    lines.append("-" * 124)
    for el in elements[:80]:
        lines.append(
            f"{el.tag[:27]:<28} {el.accessibility_id[:25]:<26} "
            f"{el.resource_id[:27]:<28} {el.text[:27]:<28} {el.bounds}"
        )

    lines.append("")
    lines.append("# Suggested screens.yaml snippet:")
    lines.append(f"{screen_name}:")
    used_keys: set[str] = set()
    for el in elements:
        if not (el.accessibility_id or el.resource_id or el.text):
            continue
        key = el.suggested_key()
        if key in used_keys:
            continue
        used_keys.add(key)
        if el.accessibility_id:
            lines.append(f"  {key}: {{accessibility_id: {el.accessibility_id!r}}}")
        elif el.resource_id:
            lines.append(f"  {key}: {{id: {el.resource_id!r}}}")
        elif el.text:
            lines.append(f"  {key}: {{text: {el.text!r}}}")
        if len(used_keys) >= 25:
            break

    return "\n".join(lines)
