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
_ANDROID_BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
_CRYPTIC_ID_RE = re.compile(
    r"^(?:[a-z]{1,4}_?\d+|view_?\d*|item_?\d*|container_?\d*|root|content|layout_?\d*|text\d*|edittext\d*)$",
    re.IGNORECASE,
)
_HUNGARIAN_PREFIX_RE = re.compile(
    r"^(?:et|edt|txt|tv|btn|bt|iv|img|cb|chk|sw|fld|lbl|rb|sp)_+",
    re.IGNORECASE,
)


def _slugify(raw: str, max_len: int = 40) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", raw).strip("_").lower()[:max_len].strip("_")


def _parse_bounds_rect(bounds: str, attrs: dict[str, str]) -> list[int] | None:
    """Parse Android `[x1,y1][x2,y2]` or iOS x/y/width/height into `[x1, y1, x2, y2]`."""
    m = _ANDROID_BOUNDS_RE.search(bounds or "")
    if m:
        x1, y1, x2, y2 = (int(m.group(i)) for i in range(1, 5))
        if x2 > x1 and y2 > y1:
            return [x1, y1, x2, y2]
    try:
        if "x" in attrs and "y" in attrs and "width" in attrs and "height" in attrs:
            x1 = int(float(attrs["x"]))
            y1 = int(float(attrs["y"]))
            x2 = x1 + int(float(attrs["width"]))
            y2 = y1 + int(float(attrs["height"]))
            if x2 > x1 and y2 > y1:
                return [x1, y1, x2, y2]
    except (TypeError, ValueError):
        pass
    return None


def _clean_resource_id(res_id: str) -> str:
    tail = (res_id or "").split("/")[-1].strip()
    if not tail or _CRYPTIC_ID_RE.match(tail):
        return ""
    stripped = _HUNGARIAN_PREFIX_RE.sub("", tail)
    return stripped if len(stripped) >= 2 else tail


def _infer_role(
    tag: str,
    clickable: bool,
    editable: bool,
    checkable: bool,
    password: bool,
) -> tuple[str, str]:
    low = (tag or "").lower()
    if password or "securetextfield" in low:
        return "input", "Password Field"
    if editable or any(k in low for k in ("edittext", "textfield", "textinput", "searchview")):
        return "input", "Text Input"
    if checkable or any(k in low for k in ("checkbox", "switch", "toggle", "radiobutton")):
        return "toggle", "Toggle / Check"
    if "tab" in low:
        return "tab", "Tab"
    if clickable or any(k in low for k in ("button", "imagebutton", "floatingactionbutton")):
        return "button", "Button"
    return "label", "Text / Element"


def _human_title(raw: str) -> str:
    cleaned = re.sub(r"[_\-]+", " ", (raw or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:48]


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
    hint: str = ""
    context_label: str = ""
    editable: bool = False
    checkable: bool = False
    rect: list[int] | None = None

    @property
    def role_slug(self) -> str:
        slug, _ = _infer_role(self.tag, self.clickable, self.editable, self.checkable, self.password)
        return slug

    @property
    def role_label(self) -> str:
        _, label = _infer_role(self.tag, self.clickable, self.editable, self.checkable, self.password)
        return label

    def suggested_key(self) -> str:
        role, _ = _infer_role(self.tag, self.clickable, self.editable, self.checkable, self.password)
        clean_id = _clean_resource_id(self.resource_id)
        acc = self.accessibility_id.strip()
        hint_or_ctx = (self.hint or self.context_label).strip()
        short_text = self.text.strip() if len(self.text.strip()) <= 36 else ""

        if role == "input":
            base_raw = acc or hint_or_ctx or clean_id or short_text or "text"
        elif role == "button":
            base_raw = short_text or acc or clean_id or hint_or_ctx or "button"
        elif role == "toggle":
            base_raw = short_text or self.context_label or acc or clean_id or "toggle"
        else:
            base_raw = acc or short_text or clean_id or hint_or_ctx or role

        slug = _slugify(base_raw, max_len=34) or role
        suffix_map = {
            "input": ("_input", ("_input", "_field", "_box")),
            "button": ("_btn", ("_btn", "_button")),
            "toggle": ("_toggle", ("_toggle", "_switch", "_checkbox", "_chk")),
            "tab": ("_tab", ("_tab",)),
        }
        if role in suffix_map:
            suffix, accepted = suffix_map[role]
            if not slug.endswith(accepted):
                slug = f"{slug}{suffix}"[:40].strip("_")
        return slug[:40] or "element"

    def display_label(self, masker: SecretMasker | None = None) -> str:
        if self.suggested_sensitive:
            label_text = self.hint or self.context_label or _clean_resource_id(self.resource_id) or "Secret"
            return f"{_human_title(label_text)} · {self.role_label}"
        raw_label = (
            self.text
            or self.hint
            or self.context_label
            or self.accessibility_id
            or _clean_resource_id(self.resource_id)
            or _human_title(self.suggested_key())
        )
        safe_label = masker.mask(raw_label) if masker else raw_label
        return f"{_human_title(safe_label) or 'Element'} · {self.role_label}"

    @property
    def suggested_sensitive(self) -> bool:
        if self.password or "SecureTextField" in self.tag:
            return True
        combined = f"{self.accessibility_id} {self.resource_id} {self.text} {self.hint} {self.context_label}"
        return bool(_SENSITIVE_FIELD_RE.search(combined))


def _first_descendant_text(node: ET.Element, depth: int = 0) -> str:
    if depth > 3:
        return ""
    for child in node:
        txt = (
            child.attrib.get("text")
            or child.attrib.get("label")
            or child.attrib.get("content-desc")
            or ""
        ).strip()
        if txt and len(txt) <= 42:
            return txt
        nested = _first_descendant_text(child, depth + 1)
        if nested:
            return nested
    return ""


def parse_page_source_elements(xml_text: str) -> tuple[list[InspectedElement], bool, list[str]]:
    """Extract actionable/labeled elements and detect WebViews from Appium XML page source."""
    webview_detected, webview_details = detect_webviews(xml_text)
    elements: list[InspectedElement] = []
    if not xml_text or not xml_text.strip():
        return elements, webview_detected, webview_details

    root = ET.fromstring(xml_text)
    parent_map = {child: parent for parent in root.iter() for child in parent}
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
        hint = (
            attrs.get("hint")
            or attrs.get("placeholderValue")
            or attrs.get("placeholder")
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
        checkable = str(attrs.get("checkable", "")).lower() == "true"
        enabled = str(attrs.get("enabled", "true")).lower() != "false"
        visible = str(attrs.get("visible", attrs.get("displayed", "true"))).lower() != "false"
        password = str(attrs.get("password", "")).lower() == "true"
        tag_low = node.tag.lower()
        editable = bool(
            hint
            or password
            or any(k in tag_low for k in ("edittext", "textfield", "securetextfield", "autocompletetextview"))
        )
        rect = _parse_bounds_rect(bounds, attrs)
        xpath = f"(//{node.tag})[{idx}]"

        context_label = ""
        if not text and not hint:
            child_txt = _first_descendant_text(node)
            if child_txt:
                context_label = child_txt
            elif node in parent_map:
                parent = parent_map[node]
                siblings = list(parent)
                try:
                    pos = siblings.index(node)
                except ValueError:
                    pos = -1
                if pos > 0:
                    prev_sib = siblings[pos - 1]
                    sib_txt = (
                        prev_sib.attrib.get("text")
                        or prev_sib.attrib.get("label")
                        or prev_sib.attrib.get("content-desc")
                        or _first_descendant_text(prev_sib)
                    ).strip()
                    if sib_txt and len(sib_txt) <= 42:
                        context_label = sib_txt

        if acc_id or res_id or text or hint or clickable or editable or checkable or password:
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
                    hint=hint,
                    context_label=context_label,
                    editable=editable,
                    checkable=checkable,
                    rect=rect,
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

    used_keys: dict[str, int] = {}
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

        base_key = el.suggested_key()
        occ = used_keys.get(base_key, 0) + 1
        used_keys[base_key] = occ
        unique_key = base_key if occ == 1 else f"{base_key[:35]}_{occ}"

        display_text = SecretMasker.REPLACEMENT if el.suggested_sensitive else (masker.mask(el.text) if masker else el.text)
        safe_hint = SecretMasker.REPLACEMENT if el.suggested_sensitive and el.hint else (masker.mask(el.hint) if masker else el.hint)
        ranked.append(
            {
                "key": unique_key,
                "display_label": el.display_label(masker=masker),
                "role": el.role_slug,
                "role_label": el.role_label,
                "editable": el.editable,
                "hint": safe_hint,
                "context_label": el.context_label,
                "rect": el.rect,
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
