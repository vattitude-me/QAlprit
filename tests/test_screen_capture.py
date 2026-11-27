"""scripts/screen_capture.py: tree parsing, selectors, replay and recording, with a fake phone."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest
import yaml

TOOL = Path(__file__).resolve().parents[1] / "scripts" / "screen_capture.py"
if not TOOL.is_file():  # the sdist ships tests/ but not scripts/
    pytest.skip("scripts/screen_capture.py is not in this checkout", allow_module_level=True)
spec = importlib.util.spec_from_file_location("screen_capture", TOOL)
sc = importlib.util.module_from_spec(spec)
sys.modules["screen_capture"] = sc
spec.loader.exec_module(sc)

PKG = "com.example.bank"


def _node(rid: str = "", text: str = "", desc: str = "", bounds: str = "[0,0][10,10]", **extra: str) -> str:
    attrs = {"class": extra.pop("cls", "android.widget.TextView"), "package": PKG, "resource-id": rid, "text": text, "content-desc": desc, "bounds": bounds, **extra}
    return "<node " + " ".join(f'{k}="{v}"' for k, v in attrs.items()) + " />"


def _screen(*nodes: str) -> str:
    return f'<hierarchy rotation="0"><node class="android.widget.FrameLayout" package="{PKG}" bounds="[0,0][1080,2400]">{"".join(nodes)}</node></hierarchy>'


HOME = _screen(
    _node(f"{PKG}:id/title", "Home", bounds="[0,100][1080,200]"),
    _node(f"{PKG}:id/move_money", "Move money", bounds="[0,2200][540,2400]", clickable="true"),
    _node(f"{PKG}:id/row", "Account", bounds="[0,400][1080,500]"),
    _node(f"{PKG}:id/row", "Savings", bounds="[0,500][1080,600]"),
)
MOVE = _screen(
    _node(f"{PKG}:id/title", "Move money", bounds="[0,100][1080,200]"),
    _node(f"{PKG}:id/amount", "", bounds="[0,300][1080,400]", cls="android.widget.EditText"),
)
# The same screen in French: ids stay, text changes.
HOME_FR = HOME.replace('"Move money"', '"Virer des fonds"').replace('"Home"', '"Accueil"')
PIN = _screen(*[_node(f"{PKG}:id/keyboard_key_{d}", d, bounds=f"[{d}0,1000][{d}9,1100]") for d in "0123456789"])


class FakeDevice:
    serial = "fake"
    adb = "adb"

    def __init__(self, reader: "FakeReader") -> None:
        self.reader = reader
        self.taps: list[tuple[int, int]] = []

    def screen_size(self) -> tuple[int, int]:
        return 1080, 2400

    def tap(self, x: int, y: int) -> None:
        self.taps.append((x, y))
        self.reader.on_tap(x, y)

    def long_press(self, x: int, y: int, ms: int = 800) -> None:
        self.tap(x, y)

    def swipe(self, *a: int) -> None:
        pass

    def back(self) -> None:
        self.reader.current = self.reader.home

    def foreground_package(self) -> str:
        return PKG


class FakeReader:
    name = "fake"

    def __init__(self, home: str = HOME) -> None:
        self.home = home
        self.current = home
        self.typed: list[str] = []

    def on_tap(self, x: int, y: int) -> None:
        if self.current in (HOME, HOME_FR) and y >= 2200:
            self.current = MOVE

    def tree(self) -> str:
        return self.current

    def screenshot(self) -> bytes:
        return b"\x89PNG fake"

    def type_text(self, node, value: str) -> None:
        self.typed.append(value)

    def close(self) -> None:
        pass


def _cap(tmp_path: Path, reader: FakeReader | None = None):
    reader = reader or FakeReader()
    return sc.Capture(reader, FakeDevice(reader), tmp_path / "flow", settle_timeout=0.2, poll=0.01)


def test_parse_nodes_reads_uiautomator_and_appium_trees() -> None:
    nodes = sc.parse_nodes(HOME)
    assert [n.text for n in nodes if n.text][:2] == ["Home", "Move money"]
    appium = '<hierarchy><android.widget.Button class="android.widget.Button" resource-id="a:id/b" text="Go" bounds="[1,2][3,4]" displayed="true"/><android.view.View bounds="[0,0][5,5]" displayed="false"/></hierarchy>'
    parsed = sc.parse_nodes(appium)
    assert len(parsed) == 1 and parsed[0].rid == "a:id/b" and parsed[0].center == (2, 3)


def test_hit_test_picks_the_smallest_named_element() -> None:
    nodes = sc.parse_nodes(HOME)
    assert sc.hit_test(nodes, 100, 2300).rid.endswith("move_money")
    assert sc.hit_test(nodes, 100, 1500) is None


def test_selector_for_keeps_every_selector_and_an_index_for_repeated_ids() -> None:
    nodes = sc.parse_nodes(HOME)
    savings = next(n for n in nodes if n.text == "Savings")
    sel = sc.selector_for(savings, nodes, 50, 550)
    assert sel == {"id": f"{PKG}:id/row", "index": 1, "text": "Savings", "xy": [50, 550]}
    found, how = sc.find_node(nodes, sel)
    assert found is savings and how == "id"


def test_find_node_order_and_xy_only_when_allowed() -> None:
    nodes = sc.parse_nodes(HOME)
    assert sc.find_node(nodes, {"id": "move_money"})[1] == "id"  # bare id matches the suffix
    assert sc.find_node(nodes, {"id": "gone", "text": "Move money"})[1] == "text"
    assert sc.find_node(nodes, {"id": "gone", "xy": [5, 5]}) == (None, "")
    assert sc.find_node(nodes, {"id": "gone", "xy": [5, 5]}, allow_xy=True)[1] == "xy"


def test_mask_match_turns_a_passcode_key_into_a_template() -> None:
    node = sc.parse_nodes(PIN)[3]
    assert sc.mask_match(node, re.compile(sc.DEFAULT_MASK)) == (f"{PKG}:id/keyboard_key_{{}}", "2")
    assert sc.mask_match(sc.parse_nodes(HOME)[1], re.compile(sc.DEFAULT_MASK)) is None


def test_expand_vars_needs_every_variable() -> None:
    assert sc.expand_vars("${A}-x", {"A": "1"}) == "1-x"
    with pytest.raises(KeyError, match="NOPE"):
        sc.expand_vars("${NOPE}", {})


def test_tap_by_text_records_the_id_so_the_path_replays_in_another_language(tmp_path: Path) -> None:
    cap = _cap(tmp_path)
    cap.read()
    res = sc.execute_step(cap, {"tap": {"text": "Move money"}}, {}, timeout=0.2, allow_xy=False)
    assert res.record["tap"]["id"] == f"{PKG}:id/move_money"
    assert cap.reader.current == MOVE

    fr = _cap(tmp_path / "fr", FakeReader(HOME_FR))
    doc = {"steps": [res.record, {"snap": "001_move"}]}
    notes = sc.run_path(fr, doc, {}, timeout=0.2, allow_xy=False)
    assert notes == [] and (tmp_path / "fr" / "flow" / "001_move.png").exists()


def test_missing_element_saves_the_screen_and_stops(tmp_path: Path) -> None:
    cap = _cap(tmp_path)
    with pytest.raises(sc.StepFailed, match="no element matches"):
        sc.run_path(cap, {"steps": [{"tap": {"id": "pay_bill"}}]}, {}, timeout=0.05, allow_xy=False)
    assert (tmp_path / "flow" / "001_tap_MISSING.png").exists()
    assert (tmp_path / "flow" / "001_tap_MISSING.xml").read_text() == HOME


def test_type_keeps_the_variable_name_in_the_path(tmp_path: Path) -> None:
    reader = FakeReader()
    reader.current = MOVE
    cap = _cap(tmp_path, reader)
    res = sc.execute_step(cap, {"type": {"id": "amount", "value": "${AMOUNT}"}}, {"AMOUNT": "12.50"}, timeout=0.2, allow_xy=False)
    assert reader.typed == ["12.50"]
    assert res.record["type"]["value"] == "${AMOUNT}"


def test_keypad_taps_each_digit_of_the_variable(tmp_path: Path) -> None:
    reader = FakeReader(PIN)
    cap = _cap(tmp_path, reader)
    spec_ = {"keypad": {"id_template": f"{PKG}:id/keyboard_key_{{}}", "value": "${PIN}"}}
    res = sc.execute_step(cap, spec_, {"PIN": "40"}, timeout=0.2, allow_xy=False)
    assert cap.dev.taps == [(44, 1050), (4, 1050)]
    assert res.record == spec_


def test_path_recorder_appends_and_continues_numbering(tmp_path: Path) -> None:
    cap = _cap(tmp_path)
    cap.read()
    path = tmp_path / "paths" / "flow.yaml"
    rec = sc.PathRecorder(path, "flow", PKG, cap)
    assert rec.snap("Home screen") == "001_home_screen"
    rec.add("back")
    again = sc.PathRecorder(path, "flow", PKG, cap)
    assert again.snap("x") == "002_x"
    doc = yaml.safe_load(path.read_text())
    assert doc["steps"] == [{"snap": "001_home_screen"}, "back", {"snap": "002_x"}]


def test_screen_summary_lists_elements_with_ids_and_centres(tmp_path: Path) -> None:
    cap = _cap(tmp_path)
    cap.read()
    text = sc.screen_summary(cap)
    assert text.splitlines()[0] == f"app: {PKG}"
    assert "Move money [move_money] @270,2300" in text


def test_write_index_lists_each_path_folder(tmp_path: Path) -> None:
    (tmp_path / "send").mkdir()
    (tmp_path / "send" / "001_home.png").write_bytes(b"x")
    sc.write_index(tmp_path)
    page = (tmp_path / "index.html").read_text()
    assert "send/001_home.png" in page and "<title>Screen capture</title>" in page


def test_step_kind_ignores_the_person_marker() -> None:
    assert sc.step_kind({"by": "person", "tap": {"id": "x"}}) == ("tap", {"id": "x"})
    assert sc.step_kind({"back": None, "by": "person"}) == ("back", None)
    assert sc.step_kind("back") == ("back", None)


def test_find_node_falls_back_to_the_label_in_either_attribute_and_any_case() -> None:
    nodes = sc.parse_nodes(_screen(_node(desc="Move Money", bounds="[0,0][100,100]")))
    node, how = sc.find_node(nodes, {"text": "Move money"})
    assert node is not None and how == "label"


def test_path_app_moves_off_the_lock_screen_to_the_real_app(tmp_path: Path) -> None:
    cap = _cap(tmp_path)
    cap.read()
    rec = sc.PathRecorder(tmp_path / "p.yaml", "p", "com.android.systemui", cap)
    rec.add("back")
    assert rec.doc["app"] == PKG
