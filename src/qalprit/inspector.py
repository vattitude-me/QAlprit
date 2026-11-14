"""Element tree inspector (qalprit inspect) and screens.yaml suggestion generator."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

from qalprit.env_loader import SecretMasker
from qalprit.evidence import detect_webviews

_SENSITIVE_FIELD_RE = re.compile(
    r"(password|passcode|pin|cvv|cvc|ssn|secret|token|otp|security.?code)",
    re.IGNORECASE,
)


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
    password: bool = False
    xpath: str = ""

    def suggested_key(self) -> str:
        raw = self.accessibility_id or self.resource_id.split("/")[-1] or self.text or self.tag
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", raw).strip("_").lower()
        return slug[:40] or "element"

    @property
    def suggested_sensitive(self) -> bool:
        if self.password or "SecureTextField" in self.tag:
            return True
        combined = f"{self.accessibility_id} {self.resource_id} {self.text}"
        return bool(_SENSITIVE_FIELD_RE.search(combined))


def parse_page_source_elements(xml_text: str) -> tuple[list[InspectedElement], bool, list[str]]:
    """Extract actionable/labeled elements and detect WebViews from Appium XML page source."""
    webview_detected, webview_details = detect_webviews(xml_text)
    elements: list[InspectedElement] = []
    if not xml_text or not xml_text.strip():
        return elements, webview_detected, webview_details

    root = ET.fromstring(xml_text)
    tag_counts: dict[str, int] = {}
    for node in root.iter():
        tag_counts[node.tag] = tag_counts.get(node.tag, 0) + 1
        idx = tag_counts[node.tag]
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
        password = str(attrs.get("password", "")).lower() == "true"
        xpath = f"(//{node.tag})[{idx}]"

        if acc_id or res_id or text or clickable or password:
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
                    password=password,
                    xpath=xpath,
                )
            )
    return elements, webview_detected, webview_details


def extract_interactive_elements(
    xml_text: str,
    platform: str = "android",
    masker: SecretMasker | None = None,
) -> list[dict[str, Any]]:
    """Return elements with ranked locator candidates, ambiguity flags, and sensitivity hints."""
    elements, _, _ = parse_page_source_elements(xml_text)
    acc_counts: dict[str, int] = {}
    id_counts: dict[str, int] = {}
    text_counts: dict[str, int] = {}
    for el in elements:
        if el.accessibility_id:
            acc_counts[el.accessibility_id] = acc_counts.get(el.accessibility_id, 0) + 1
        if el.resource_id:
            id_counts[el.resource_id] = id_counts.get(el.resource_id, 0) + 1
        if el.text:
            text_counts[el.text] = text_counts.get(el.text, 0) + 1

    ranked: list[dict[str, Any]] = []
    for el in elements:
        candidates: list[dict[str, Any]] = []
        if el.accessibility_id:
            cnt = acc_counts.get(el.accessibility_id, 1)
            candidates.append(
                {
                    "strategy": "accessibility_id",
                    "value": el.accessibility_id,
                    "score": 100 - (15 if cnt > 1 else 0),
                    "match_count": cnt,
                    "ambiguous": cnt > 1,
                }
            )
        if el.resource_id:
            cnt = id_counts.get(el.resource_id, 1)
            candidates.append(
                {
                    "strategy": "id",
                    "value": el.resource_id,
                    "score": 90 - (15 if cnt > 1 else 0),
                    "match_count": cnt,
                    "ambiguous": cnt > 1,
                }
            )
        if el.text:
            cnt = text_counts.get(el.text, 1)
            safe_txt = SecretMasker.REPLACEMENT if el.suggested_sensitive else (masker.mask(el.text) if masker else el.text)
            candidates.append(
                {
                    "strategy": "text",
                    "value": safe_txt,
                    "score": 60 - (15 if cnt > 1 else 0),
                    "match_count": cnt,
                    "ambiguous": cnt > 1,
                }
            )
        if el.xpath:
            candidates.append(
                {
                    "strategy": "xpath",
                    "value": el.xpath,
                    "score": 20,
                    "match_count": 1,
                    "ambiguous": False,
                }
            )

        candidates.sort(key=lambda c: c["score"], reverse=True)
        best = candidates[0] if candidates else {"strategy": "xpath", "value": "", "score": 0, "match_count": 0, "ambiguous": False}
        warnings: list[str] = []
        if best["ambiguous"]:
            warnings.append(
                f"Duplicate {best['strategy']} '{best['value']}' matched {best['match_count']} elements"
            )
        if best["strategy"] == "xpath":
            warnings.append("Only positional XPath available; consider adding an accessibility_id")

        display_text = SecretMasker.REPLACEMENT if el.suggested_sensitive else (masker.mask(el.text) if masker else el.text)
        ranked.append(
            {
                "key": el.suggested_key(),
                "tag": el.tag,
                "platform": platform,
                "recommended_strategy": best["strategy"],
                "recommended_value": best["value"],
                "stability_rank": best["score"],
                "ambiguous": bool(best["ambiguous"]),
                "match_count": int(best["match_count"]),
                "suggested_sensitive": el.suggested_sensitive,
                "clickable": el.clickable,
                "enabled": el.enabled,
                "visible": el.visible,
                "bounds": el.bounds,
                "text": display_text,
                "alternatives": candidates[1:],
                "warnings": warnings,
            }
        )
    return ranked


def format_inspect_output(
    xml_text: str,
    screen_name: str = "screen",
    platform: str = "android",
    masker: SecretMasker | None = None,
) -> str:
    """Format inspected elements and a starter screens.yaml snippet."""
    elements, webview_detected, webview_details = parse_page_source_elements(xml_text)
    ranked = extract_interactive_elements(xml_text, platform=platform, masker=masker)
    lines: list[str] = []
    if webview_detected:
        lines.append(
            f"[WARNING] WebView detected in page source: {', '.join(webview_details)} "
            "(pure-native v1 scope — flag before expanding scope)"
        )
        lines.append("")

    lines.append(f"Found {len(elements)} addressable element(s):")
    lines.append(f"{'TAG':<28} {'ACC_ID':<26} {'RES_ID':<28} {'TEXT':<22} {'BEST':<18} {'BOUNDS'}")
    lines.append("-" * 138)
    for el, item in zip(elements[:80], ranked[:80]):
        best_label = f"{item['recommended_strategy']}({item['stability_rank']})"
        if item["ambiguous"]:
            best_label += "!"
        lines.append(
            f"{el.tag[:27]:<28} {el.accessibility_id[:25]:<26} "
            f"{el.resource_id[:27]:<28} {item['text'][:21]:<22} {best_label[:17]:<18} {el.bounds}"
        )

    lines.append("")
    lines.append("# Suggested screens.yaml snippet:")
    lines.append(f"{screen_name}:")
    used_keys: set[str] = set()
    for item in ranked:
        strat = item["recommended_strategy"]
        val = item["recommended_value"]
        if strat == "xpath" or not val:
            continue
        key = item["key"]
        if key in used_keys:
            continue
        used_keys.add(key)
        sens_suffix = ", sensitive: true" if item["suggested_sensitive"] else ""
        comment = f"  # AMBIGUOUS x{item['match_count']}" if item["ambiguous"] else ""
        lines.append(f"  {key}: {{{strat}: {val!r}{sens_suffix}}}{comment}")
        if len(used_keys) >= 25:
            break

    return "\n".join(lines)
