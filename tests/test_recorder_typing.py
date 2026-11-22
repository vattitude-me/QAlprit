"""Typing done by hand on a phone: labels on text fields, password fields, and tap to type."""

from __future__ import annotations

import pytest

from qalprit.env_loader import SecretMasker
from qalprit.inspector import extract_interactive_elements
from qalprit.recorder import RecorderManager, detect_changed_editable_inputs, hit_test_element

# A text field with its "First name" label drawn on top, the way Compose and React Native apps
# lay them out, inside a non-clickable container.
_NAME_SCREEN = [
    {"recommended_value": "NameFieldFirstName", "rect": [72, 638, 888, 782], "editable": False, "clickable": False},
    {"recommended_value": "(//android.widget.EditText)[1]", "rect": [72, 638, 888, 764], "editable": True, "clickable": True},
    {"recommended_value": "First name", "rect": [72, 682, 220, 721], "editable": False, "clickable": False},
    {"recommended_value": "clear", "rect": [820, 682, 870, 721], "editable": False, "clickable": True},
]


def test_tap_on_a_label_inside_a_field_hits_the_field() -> None:
    idx, el = hit_test_element(_NAME_SCREEN, 100, 700)
    assert el["editable"] is True and idx == 1


def test_clickable_control_inside_a_field_is_still_hit() -> None:
    _, el = hit_test_element(_NAME_SCREEN, 840, 700)
    assert el["recommended_value"] == "clear"


def test_label_outside_any_field_is_hit_as_is() -> None:
    _, el = hit_test_element(_NAME_SCREEN, 80, 775)
    assert el["recommended_value"] == "NameFieldFirstName"


def _pw(filled: bool) -> dict:
    return {
        "recommended_strategy": "id",
        "recommended_value": "app:id/password",
        "editable": True,
        "text": SecretMasker.REPLACEMENT,
        "hint": "",
        "filled": filled,
    }


def test_password_field_filled_is_detected_as_typing() -> None:
    changed = detect_changed_editable_inputs([_pw(False)], [_pw(True)])
    assert len(changed) == 1 and changed[0][1] == SecretMasker.REPLACEMENT
    # More key presses in the same field do not add another step.
    assert detect_changed_editable_inputs([_pw(True)], [_pw(True)]) == []
    assert detect_changed_editable_inputs([_pw(False)], [_pw(False)]) == []


def test_inspector_reports_filled_without_the_password() -> None:
    xml = (
        '<hierarchy><android.widget.EditText resource-id="app:id/password" password="true" '
        'text="••••••" hint="Password" clickable="true" enabled="true" displayed="true" '
        'bounds="[0,0][500,100]" /></hierarchy>'
    )
    els = extract_interactive_elements(xml, platform="android")
    field = next(e for e in els if e["editable"])
    assert field["filled"] is True
    assert field["text"] == SecretMasker.REPLACEMENT
    empty = extract_interactive_elements(xml.replace('text="••••••"', 'text="Password"'), platform="android")
    assert next(e for e in empty if e["editable"])["filled"] is False


def _manager_with(steps: list[dict]) -> RecorderManager:
    mgr = RecorderManager()
    mgr._steps = steps
    return mgr


def test_recorded_tap_can_become_a_type_and_back() -> None:
    mgr = _manager_with([{"uid": "a", "action": "tap", "target": "name.first", "value": "", "secret": False, "label": "Tap"}])
    mgr.update_step(0, action="type")
    mgr.update_step(0, value="Alice")
    st = mgr._steps[0]
    assert st["action"] == "type" and st["value"] == "Alice"
    assert st["label"] == "Type 'Alice' into name.first"
    mgr.update_step(0, action="tap")
    assert mgr._steps[0]["action"] == "tap" and mgr._steps[0]["value"] == ""


def test_only_tap_and_type_with_a_target_can_switch() -> None:
    mgr = _manager_with([
        {"uid": "a", "action": "swipe", "target": "", "value": "up", "secret": False, "label": "Swipe up"},
        {"uid": "b", "action": "tap", "target": "", "value": "", "secret": False, "label": "Tap"},
    ])
    with pytest.raises(ValueError):
        mgr.update_step(0, action="type")
    with pytest.raises(ValueError):
        mgr.update_step(1, action="type")
