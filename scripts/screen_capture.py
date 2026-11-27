"""Capture every screen of an Android app fast, with an AI agent driving and a person helping.

    serve    Holds one device session open on 127.0.0.1:8790.
    path N   Starts (or continues) the flow N. Steps go to results/screen-capture/paths/N.yaml.
    tap "Send money" | tap --id ID | tap --desc D     Act, wait for the screen to settle, save it.
    type --id ID --value '${NAME}' | keypad | back | swipe up | wait_for | snap NAME
    do '[{tap: {text: Pay}}, back]'                   Several steps in one call.
    screen   Prints the current screen without acting.
    run P.yaml [...]                                  Replays recorded paths and re-shoots them.
    stop

Every action call prints the new screen as one line per element ("text [resource id] @x,y"),
so the agent can choose the next step without a second call. A step is recorded with every
selector the screen offers for that element (resource id, content-desc, text, point), not
only the one the agent used. A path recorded in English by text therefore replays in French
by id.

A person can help at any time by working the phone by hand. While serving, the script watches
the touchscreen (`getevent`) and records those taps, swipes, Back and typing into the same path
with "by: person", saves the screen, and reports them on the agent's next call. Passcode keys
matching --mask become one `keypad` step with a ${PIN} value, and password fields become
${SECRET}, so digits and passwords are never written.

`run` waits for each step's element instead of sleeping. When a new build moved or renamed an
element, it saves the screen as <step>_MISSING.png/.xml, stops that path and carries on with
the others. Fix the step from the saved tree, or re-record that part, never by guessing.

Output: <out>/<path>/<snap>.png and .xml, timings.json, and <out>/index.html with every screen
grouped by path. The script never installs, clears, resets or stops an app. It attaches to
whatever is on the screen.

Speed: one Appium UiAutomator2 session stays open (idle wait off), so a UI tree read takes a few
hundred ms instead of about 3 s for `uiautomator dump`, and it works on animated screens where
the dump fails. --backend adb skips Appium.

Run it with the repo venv: .venv/bin/python scripts/screen_capture.py <command>
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

_BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
DEFAULT_MASK = r"(?i)(keyboard_key|keypad|pin|digit|key)_?(\d)$"
SELECTOR_KEYS = ("id", "desc", "text")


# ---------------------------------------------------------------- UI tree (pure, tested offline)


@dataclass
class Node:
    cls: str
    rid: str
    text: str
    desc: str
    x1: int
    y1: int
    x2: int
    y2: int
    clickable: bool = False
    focused: bool = False
    password: bool = False
    pkg: str = ""

    @property
    def area(self) -> int:
        return max(0, self.x2 - self.x1) * max(0, self.y2 - self.y1)

    @property
    def center(self) -> tuple[int, int]:
        return (self.x1 + self.x2) // 2, (self.y1 + self.y2) // 2

    def contains(self, x: int, y: int) -> bool:
        return self.x1 <= x < self.x2 and self.y1 <= y < self.y2

    def label(self) -> str:
        return self.text or self.desc or self.rid.split("/")[-1]


def parse_nodes(xml_text: str) -> list[Node]:
    """Read every element with bounds from a `uiautomator dump` or Appium page source."""
    root = ET.fromstring(xml_text)
    out: list[Node] = []
    for el in root.iter():
        a = el.attrib
        m = _BOUNDS_RE.fullmatch(a.get("bounds", "").strip())
        if not m:
            continue
        if a.get("displayed", "true").lower() == "false":
            continue
        x1, y1, x2, y2 = (int(v) for v in m.groups())
        out.append(
            Node(
                cls=a.get("class", el.tag),
                rid=a.get("resource-id", ""),
                text=a.get("text", ""),
                desc=a.get("content-desc", ""),
                x1=x1,
                y1=y1,
                x2=x2,
                y2=y2,
                clickable=a.get("clickable") == "true",
                focused=a.get("focused") == "true",
                password=a.get("password") == "true",
                pkg=a.get("package", ""),
            )
        )
    return out


def tree_signature(nodes: list[Node]) -> str:
    """Changes when anything visible changes. Two equal reads in a row mean the screen settled."""
    h = hashlib.sha1()
    for n in nodes:
        h.update(f"{n.rid}|{n.text}|{n.desc}|{n.x1},{n.y1},{n.x2},{n.y2}\n".encode())
    return h.hexdigest()


def hit_test(nodes: list[Node], x: int, y: int) -> Node | None:
    """The smallest element under the finger that can be named (id, desc or text)."""
    named = [n for n in nodes if n.contains(x, y) and n.area > 0 and (n.rid or n.desc or n.text)]
    if not named:
        return None
    return min(named, key=lambda n: n.area)


def selector_for(node: Node, nodes: list[Node], x: int, y: int) -> dict[str, Any]:
    """Every selector the screen offers for this element, plus where the finger landed.

    A value is kept only when it picks out one element on the screen, or when the element is
    the first match (replay takes the first match too).
    """
    sel: dict[str, Any] = {}
    for key, attr in (("id", "rid"), ("desc", "desc"), ("text", "text")):
        val = getattr(node, attr)
        if not val:
            continue
        matches = [n for n in nodes if getattr(n, attr) == val]
        if matches and matches[0] is node:
            sel[key] = val
        elif len(matches) > 1:
            idx = next((i for i, n in enumerate(matches) if n is node), None)
            if idx is not None:
                sel[key] = val
                sel.setdefault("index", idx)
    sel["xy"] = [int(x), int(y)]
    return sel


def find_node(nodes: list[Node], sel: dict[str, Any], *, allow_xy: bool = False) -> tuple[Node | None, str]:
    """Find a step's element. Returns the node and which selector matched."""
    index = int(sel.get("index", 0) or 0)
    for key, attr in (("id", "rid"), ("desc", "desc"), ("text", "text")):
        val = sel.get(key)
        if not val:
            continue
        matches = [n for n in nodes if getattr(n, attr) == val and n.area > 0]
        if key == "id" and not matches and ":" not in str(val):
            matches = [n for n in nodes if n.rid.endswith(f":id/{val}") and n.area > 0]
        if len(matches) > index:
            return matches[index], key
    # Labels move between text and content-desc, and case differs across screens
    # ("Move Money" tab, "Move money" title), so try the label loosely before giving up.
    for key in ("text", "desc"):
        val = str(sel.get(key) or "").casefold()
        if not val:
            continue
        matches = [n for n in nodes if n.area > 0 and val in (n.text.casefold(), n.desc.casefold())]
        if len(matches) > index:
            return matches[index], "label"
    if allow_xy and sel.get("xy"):
        x, y = sel["xy"]
        return Node("", "", "", "", int(x), int(y), int(x) + 1, int(y) + 1), "xy"
    return None, ""


def focused_field(nodes: list[Node]) -> Node | None:
    fields = [n for n in nodes if n.focused and "EditText" in n.cls]
    return fields[-1] if fields else None


def mask_match(node: Node | None, pattern: re.Pattern[str] | None) -> tuple[str, str] | None:
    """For a passcode key, return (id template, digit). The digit is never written anywhere."""
    if node is None or pattern is None or not node.rid:
        return None
    m = pattern.search(node.rid)
    if not m:
        return None
    digit = m.group(m.lastindex or 0)
    start, end = m.span(m.lastindex or 0)
    return node.rid[:start] + "{}" + node.rid[end:], digit


def slug(text: str, limit: int = 32) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return s[:limit].strip("_") or "screen"


def expand_vars(value: str, env: dict[str, str]) -> str:
    def _sub(m: re.Match[str]) -> str:
        name = m.group(1)
        if name not in env:
            raise KeyError(f"Variable ${{{name}}} is not set. Put it in the environment or --env-file.")
        return env[name]

    return _VAR_RE.sub(_sub, value)


# ---------------------------------------------------------------- device backends


def _adb_path() -> str:
    from qalprit.doctor import find_android_tool

    adb = find_android_tool("adb") or shutil.which("adb")
    if not adb:
        raise SystemExit("adb not found. Install Android platform-tools or run QAlprit setup.")
    return adb


class Device:
    """Actions go through adb (fast and the same for both backends). Reads use the backend."""

    def __init__(self, serial: str | None) -> None:
        self.adb = _adb_path()
        self.serial = serial or self._only_device()

    def _only_device(self) -> str:
        out = subprocess.run([self.adb, "devices"], capture_output=True, text=True).stdout
        serials = [ln.split()[0] for ln in out.splitlines()[1:] if ln.strip().endswith("device")]
        if len(serials) != 1:
            raise SystemExit(f"Pick a device with --serial. Connected: {serials or 'none'}")
        return serials[0]

    def sh(self, *args: str, binary: bool = False) -> Any:
        res = subprocess.run([self.adb, "-s", self.serial, *args], capture_output=True, text=not binary)
        return res.stdout

    def tap(self, x: int, y: int) -> None:
        self.sh("shell", "input", "tap", str(x), str(y))

    def long_press(self, x: int, y: int, ms: int = 800) -> None:
        self.sh("shell", "input", "swipe", str(x), str(y), str(x), str(y), str(ms))

    def swipe(self, x1: int, y1: int, x2: int, y2: int, ms: int = 300) -> None:
        self.sh("shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(ms))

    def back(self) -> None:
        self.sh("shell", "input", "keyevent", "KEYCODE_BACK")

    def screen_size(self) -> tuple[int, int]:
        if getattr(self, "_size", None):
            return self._size
        out = self.sh("shell", "wm", "size")
        m = re.findall(r"(\d+)x(\d+)", out or "")
        if not m:
            return 1080, 2400
        w, h = m[-1]  # "Override size" comes last when set
        self._size = (int(w), int(h))
        return self._size

    def keyboard_top(self, height: int) -> int | None:
        from qalprit.device_input import parse_keyboard_top

        ime = self.sh("shell", "dumpsys", "input_method") or ""
        if "mInputShown=true" not in ime:  # mIsInputViewShown stays true after the keyboard hides
            return None
        top = parse_keyboard_top(self.sh("shell", "dumpsys", "window", "InputMethod"), height)
        # Unknown layout: treat the lower 40% as keys rather than record key presses as taps.
        return top if top is not None else int(height * 0.6)

    def foreground_package(self) -> str:
        out = self.sh("shell", "dumpsys", "window") or ""
        m = re.search(r"mCurrentFocus=Window\{\S+ \S+ ([\w.]+)/", out)
        return m.group(1) if m else ""


class AdbReader:
    name = "adb"

    def __init__(self, dev: Device) -> None:
        self.dev = dev

    def tree(self) -> str:
        self.dev.sh("shell", "uiautomator", "dump", "/sdcard/qa_capture.xml")
        return self.dev.sh("exec-out", "cat", "/sdcard/qa_capture.xml")

    def screenshot(self) -> bytes:
        return self.dev.sh("exec-out", "screencap", "-p", binary=True)

    def type_text(self, node: Node, value: str) -> None:
        x, y = node.center
        self.dev.tap(x, y)
        # adb input text has no escaping for spaces or accents. Spaces become %s.
        self.dev.sh("shell", "input", "text", value.replace(" ", "%s"))

    def close(self) -> None:
        pass


class AppiumReader:
    """One UiAutomator2 session for the whole capture. Attaches only: no app, no reset."""

    name = "appium"

    def __init__(self, dev: Device, log_path: Path, appium_url: str | None) -> None:
        from appium import webdriver
        from appium.options.android import UiAutomator2Options

        from qalprit.appium_manager import AppiumManager

        self.dev = dev
        self._mgr = AppiumManager(log_path=log_path, appium_url=appium_url)
        self._mgr.__enter__()
        opts = UiAutomator2Options()
        opts.platform_name = "Android"
        opts.automation_name = "UiAutomator2"
        opts.udid = dev.serial
        opts.device_name = dev.serial
        opts.no_reset = True
        opts.new_command_timeout = 900
        opts.set_capability("appium:skipUnlock", True)
        opts.set_capability("appium:disableWindowAnimation", False)
        self.driver = webdriver.Remote(self._mgr.url, options=opts)
        # Read the tree right away instead of waiting for the app to go idle. Screens with a
        # spinner or animation never go idle, which is why `uiautomator dump` fails on them.
        try:
            self.driver.update_settings({"waitForIdleTimeout": 0, "waitForSelectorTimeout": 0})
        except Exception:
            pass

    def tree(self) -> str:
        return self.driver.page_source

    def screenshot(self) -> bytes:
        return self.driver.get_screenshot_as_png()

    def type_text(self, node: Node, value: str) -> None:
        from appium.webdriver.common.appiumby import AppiumBy

        if node.rid:
            els = self.driver.find_elements(AppiumBy.ID, node.rid)
            if els:
                els[0].send_keys(value)
                return
        x, y = node.center
        self.dev.tap(x, y)
        self.driver.switch_to.active_element.send_keys(value)

    def close(self) -> None:
        try:
            self.driver.quit()
        except Exception:
            pass
        self._mgr.__exit__(None, None, None)


# ---------------------------------------------------------------- capture session


@dataclass
class Capture:
    reader: Any
    dev: Device
    out_dir: Path
    settle_timeout: float = 4.0
    poll: float = 0.25
    timings: list[dict[str, Any]] = field(default_factory=list)
    _xml: str = ""
    _nodes: list[Node] = field(default_factory=list)

    def read(self) -> list[Node]:
        try:
            self._xml = self.reader.tree()
            self._nodes = parse_nodes(self._xml)
        except Exception:
            self._xml, self._nodes = "", []
        return self._nodes

    def settle(self, min_wait: float = 0.3) -> list[Node]:
        """Wait until two reads in a row show the same tree, up to settle_timeout."""
        time.sleep(min_wait)
        deadline = time.monotonic() + self.settle_timeout
        last = tree_signature(self.read())
        while time.monotonic() < deadline:
            time.sleep(self.poll)
            sig = tree_signature(self.read())
            if sig == last and self._nodes:
                break
            last = sig
        return self._nodes

    def wait_for(self, sel: dict[str, Any], timeout: float, allow_xy: bool = False) -> tuple[Node | None, str]:
        deadline = time.monotonic() + timeout
        while True:
            node, how = find_node(self.read(), sel, allow_xy=False)
            if node is not None:
                return node, how
            if time.monotonic() >= deadline:
                break
            time.sleep(self.poll)
        if allow_xy:
            return find_node(self._nodes, sel, allow_xy=True)
        return None, ""

    def snap(self, name: str, *, fresh: bool = False) -> None:
        t0 = time.monotonic()
        self.out_dir.mkdir(parents=True, exist_ok=True)
        png = self.reader.screenshot()
        (self.out_dir / f"{name}.png").write_bytes(png)
        xml_text = self.reader.tree() if fresh or not self._xml else self._xml
        (self.out_dir / f"{name}.xml").write_text(xml_text or "", encoding="utf-8")
        self.timings.append({"snap": name, "seconds": round(time.monotonic() - t0, 2)})

    @property
    def nodes(self) -> list[Node]:
        return self._nodes


# ---------------------------------------------------------------- run (replay)




class StepFailed(Exception):
    pass


def step_kind(raw: Any) -> tuple[str, Any]:
    """A step is "back" or {kind: args}, optionally with by: person."""
    if isinstance(raw, str):
        return raw, None
    for k, v in raw.items():
        if k != "by":
            return k, v
    raise StepFailed(f"empty step {raw!r}")


def _swipe_points(step: dict[str, Any], w: int, h: int) -> tuple[int, int, int, int]:
    if "from" in step and "to" in step:
        (x1, y1), (x2, y2) = step["from"], step["to"]
        return int(x1), int(y1), int(x2), int(y2)
    direction = str(step.get("direction", "up"))
    dist = float(step.get("distance", 0.5))
    cx, cy = w // 2, h // 2
    dx, dy = int(w * dist / 2), int(h * dist / 2)
    return {
        "up": (cx, cy + dy, cx, cy - dy),
        "down": (cx, cy - dy, cx, cy + dy),
        "left": (cx + dx, cy, cx - dx, cy),
        "right": (cx - dx, cy, cx + dx, cy),
    }[direction]


@dataclass
class StepResult:
    record: Any  # the step as it goes into the path file, with every selector the screen offers
    label: str  # short name for the screen it led to
    note: str = ""  # a selector fallback, worth fixing in the path
    snap: str | None = None  # an explicit snap step's name


def execute_step(cap: Capture, raw: Any, env: dict[str, str], *, timeout: float, allow_xy: bool, index: int = 0) -> StepResult:
    """Run one step on the device. Raises StepFailed and saves the screen when an element is missing."""
    kind, arg = step_kind(raw)
    w, h = cap.dev.screen_size()
    if kind == "snap":
        return StepResult(raw, str(arg), snap=str(arg))
    if kind in ("tap", "long_press", "wait_for", "type"):
        sel = dict(arg) if isinstance(arg, dict) else {"text": str(arg)}
        node, how = cap.wait_for(sel, float(sel.get("timeout", timeout)), allow_xy=allow_xy)
        if node is None:
            cap.snap(f"{index:03d}_{kind}_MISSING", fresh=True)
            want = {k: sel[k] for k in (*SELECTOR_KEYS, "index") if k in sel}
            raise StepFailed(f"{kind}: no element matches {want}. Screen saved as {index:03d}_{kind}_MISSING.")
        note = f"{kind}: id {sel['id']!r} not found, matched by {how}" if sel.get("id") and how != "id" else ""
        x, y = node.center
        full = selector_for(node, cap.nodes, x, y) if how != "xy" else {"xy": sel["xy"]}
        if kind == "tap":
            cap.dev.tap(x, y)
        elif kind == "long_press":
            cap.dev.long_press(x, y)
        elif kind == "type":
            value = str(sel.get("value", ""))
            cap.reader.type_text(node, expand_vars(value, env))
            full.pop("text", None)
            full["value"] = value  # ${NAME} stays a name in the path file
        if kind != "wait_for":
            cap.settle()
        return StepResult({kind: full}, f"{kind}_{node.label()}" if kind != "tap" else node.label(), note)
    if kind == "keypad":
        spec = dict(arg or {})
        template = str(spec["id_template"])
        for d in expand_vars(str(spec.get("value", "")), env):
            node, _ = cap.wait_for({"id": template.format(d)}, timeout)
            if node is None:
                cap.snap(f"{index:03d}_keypad_MISSING", fresh=True)
                raise StepFailed(f"keypad: key not found for template {template!r}")
            cap.dev.tap(*node.center)
            time.sleep(0.15)
        cap.settle(min_wait=1.0)
        return StepResult({"keypad": spec}, "after_keypad")
    if kind == "back":
        cap.dev.back()
        cap.settle()
        return StepResult("back", "after_back")
    if kind == "swipe":
        spec = dict(arg) if isinstance(arg, dict) else {"direction": str(arg or "up")}
        cap.dev.swipe(*_swipe_points(spec, w, h))
        cap.settle()
        return StepResult({"swipe": spec}, f"swipe_{spec.get('direction', '')}")
    if kind == "wait":
        time.sleep(float(arg or 1))
        cap.settle()
        return StepResult({"wait": arg}, "after_wait")
    raise StepFailed(f"unknown step type {kind!r}")


def is_system_package(pkg: str) -> bool:
    """The lock screen, launcher or system UI, which a path can start on but is never about."""
    return not pkg or pkg == "com.android.systemui" or "launcher" in pkg


class PathRecorder:
    """Appends steps and snaps to a path file as they happen, so nothing is lost on a crash."""

    def __init__(self, path: Path | None, name: str, app: str, cap: Capture, start: int = 0) -> None:
        self.path = path
        self.cap = cap
        self.counter = start
        self.doc: dict[str, Any] = {"name": name, "app": app, "steps": []}
        if path is not None and path.exists():
            old = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            self.doc["steps"] = list(old.get("steps") or [])
            self.counter = sum(1 for s in self.doc["steps"] if isinstance(s, dict) and "snap" in s)

    def add(self, step: Any) -> None:
        if is_system_package(self.doc.get("app", "")):
            pkgs = [n.pkg for n in self.cap.nodes if n.pkg]
            if pkgs and not is_system_package(pkgs[0]):
                self.doc["app"] = pkgs[0]
        self.doc["steps"].append(step)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(yaml.safe_dump(self.doc, sort_keys=False, allow_unicode=True), encoding="utf-8")

    def snap(self, label: str, *, name: str | None = None) -> str:
        self.counter += 1
        snap_name = name or f"{self.counter:03d}_{slug(label)}"
        self.cap.settle(min_wait=0.0)
        self.cap.snap(snap_name)
        self.add({"snap": snap_name})
        return snap_name


def screen_summary(cap: Capture, limit: int = 70) -> str:
    """What an agent needs to pick the next step: the app, then each named element and its centre."""
    pkgs = [n.pkg for n in cap.nodes if n.pkg]
    lines = [f"app: {pkgs[0] if pkgs else '?'}"]
    seen: set[tuple[str, str, str]] = set()
    for n in cap.nodes:
        if not (n.text or n.desc) or n.area == 0:
            continue
        key = (n.text, n.desc, n.rid)
        if key in seen:
            continue
        seen.add(key)
        name = n.text if n.text else "~" + n.desc
        rid = n.rid.split("/")[-1]
        flags = "".join(f for f, on in ((" [field]", "EditText" in n.cls), (" [focused]", n.focused), (" [password]", n.password)) if on)
        lines.append(f"{name[:70]} [{rid[:30]}] @{n.center[0]},{n.center[1]}{flags}")
        if len(lines) > limit:
            lines.append(f"... {len([1 for m in cap.nodes if m.text or m.desc]) - limit} more")
            break
    return "\n".join(lines)


# ---------------------------------------------------------------- hand taps (person or follow mode)


class HandWatcher:
    """Records what a person does on the phone itself into the same path, with a snap each time.

    `adb shell input` taps go through the input dispatcher, not the touchscreen device, so the
    script's own actions never show up here.
    """

    def __init__(self, cap: Capture, rec: PathRecorder, lock: threading.Lock, mask: str, mask_var: str, log: Any = print) -> None:
        from qalprit.device_input import GeteventParser, pick_touch_device

        self.cap, self.rec, self.lock, self.log = cap, rec, lock, log
        self.mask = re.compile(mask) if mask else None
        self.mask_var = mask_var
        self.w, self.h = cap.dev.screen_size()
        touch = pick_touch_device(cap.dev.sh("shell", "getevent", "-lp"))
        if touch is None:
            raise SystemExit("Could not find the touchscreen in getevent -lp.")
        self.parser = GeteventParser(device=touch, display_width=self.w, display_height=self.h)
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.notes: list[str] = []  # what the person did since the agent last asked
        self.kb_top: int | None = None
        self.typing: Node | None = None
        self.keypad: tuple[str, int] | None = None
        self.before: list[Node] = []
        self.proc = subprocess.Popen(
            [cap.dev.adb, "-s", cap.dev.serial, "shell", "getevent", "-lt"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self.stop = threading.Event()
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._work, daemon=True).start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            for kind, payload in self.parser.feed(line):
                if kind == "down":
                    # The screen as it was when the finger landed, for hit-testing.
                    self.before = list(self.cap.nodes)
                else:
                    self.events.put((kind, payload))

    def note(self, text: str) -> None:
        self.notes.append(text)
        self.log("person: " + text)

    def take_notes(self) -> list[str]:
        out, self.notes = self.notes, []
        return out

    def refresh_keyboard(self) -> None:
        self.kb_top = self.cap.dev.keyboard_top(self.h) if focused_field(self.cap.nodes) else None

    def _flush(self) -> None:
        if self.keypad is not None:
            template, count = self.keypad
            self.keypad = None
            self.rec.add({"keypad": {"id_template": template, "value": "${" + self.mask_var + "}"}, "by": "person"})
            self.note(f"keypad, {count} keys, kept as ${{{self.mask_var}}}; saved {self.rec.snap('after_keypad')}")
        if self.typing is not None:
            fld, self.typing = self.typing, None
            nodes = self.cap.read()
            now = next((n for n in nodes if fld.rid and n.rid == fld.rid), None) or focused_field(nodes)
            if now is not None:
                secret = now.password or fld.password
                sel = selector_for(now, nodes, *now.center)
                sel.pop("text", None)
                sel["value"] = "${SECRET}" if secret else now.text
                self.rec.add({"type": sel, "by": "person"})
                self.note(f"typed into {now.label()}" + (" (password, kept as ${SECRET})" if secret else "") + f"; saved {self.rec.snap('typed_' + now.label())}")

    def _work(self) -> None:
        while not self.stop.is_set():
            try:
                kind, g = self.events.get(timeout=1.5)
            except queue.Empty:
                if self.typing is not None or self.keypad is not None:
                    with self.lock:
                        self._flush()
                continue
            with self.lock:
                before = self.before or list(self.cap.nodes)
                if kind == "key":
                    self._flush()
                    self.rec.add({"back": None, "by": "person"})
                    self.note(f"back; saved {self.rec.snap('after_back')}")
                    continue
                if g.kind in ("tap", "long_press") and self.kb_top is not None and g.y >= self.kb_top:
                    if self.typing is None:
                        self.typing = focused_field(before)
                    continue
                node = hit_test(before, g.x, g.y) if g.kind in ("tap", "long_press") else None
                masked = mask_match(node, self.mask)
                if masked and g.kind == "tap":
                    t = masked[0]
                    self.keypad = (t, self.keypad[1] + 1 if self.keypad and self.keypad[0] == t else 1)
                    continue
                self._flush()
                if g.kind in ("tap", "long_press"):
                    if node is None:
                        self.rec.add({g.kind: {"xy": [g.x, g.y]}, "by": "person"})
                        label, what = f"{g.kind}_{g.x}_{g.y}", f"{g.kind} at {g.x},{g.y} (nothing named there)"
                    else:
                        self.rec.add({g.kind: selector_for(node, before, g.x, g.y), "by": "person"})
                        label, what = node.label(), f"{g.kind} {node.label()!r}"
                    self.note(f"{what}; saved {self.rec.snap(label)}")
                elif g.kind == "swipe":
                    self.rec.add({"swipe": {"from": [g.x, g.y], "to": [g.end_x, g.end_y]}, "by": "person"})
                    self.note(f"swipe {g.direction}; saved {self.rec.snap('swipe_' + g.direction)}")
                elif g.kind == "back":
                    self.rec.add({"back": None, "by": "person"})
                    self.note(f"back (edge swipe); saved {self.rec.snap('after_back')}")
                else:
                    self.note(f"system gesture {g.direction}, not recorded")
                self.refresh_keyboard()

    def close(self) -> None:
        self.stop.set()
        self.proc.terminate()


# ---------------------------------------------------------------- shared setup


def _load_env(env_file: str | None) -> dict[str, str]:
    env = dict(os.environ)
    if env_file:
        from qalprit.env_loader import parse_env_file

        env.update(parse_env_file(Path(env_file)))
    return env


def _open_reader(args: argparse.Namespace, dev: Device, out_root: Path) -> Any:
    out_root.mkdir(parents=True, exist_ok=True)
    if args.backend == "adb":
        return AdbReader(dev)
    t0 = time.monotonic()
    reader = AppiumReader(dev, out_root / "appium.log", args.appium_url)
    print(f"Appium session ready in {time.monotonic() - t0:.1f}s", flush=True)
    return reader


def write_index(out_root: Path) -> None:
    """One page with every captured screen, one section per path folder."""
    status: dict[str, dict[str, Any]] = {}
    try:
        for r in json.loads((out_root / "run.json").read_text(encoding="utf-8")):
            status[r["path"]] = r
    except Exception:
        pass
    parts = [
        "<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>Screen capture</title><style>:root{--bg:#f3f6f9;--fg:#0f172a}"
        "@media(prefers-color-scheme:dark){:root{--bg:#090e1a;--fg:#e2e8f0}}"
        "body{font:14px system-ui;margin:16px;background:var(--bg);color:var(--fg)}"
        "h2{margin:28px 0 8px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px}"
        "figure{margin:0}img{width:100%;border-radius:8px;border:1px solid #8884}"
        "figcaption{font-size:12px;word-break:break-all}.failed{color:#dc2626}</style><h1>Screen capture</h1>",
    ]
    for d in sorted(p for p in out_root.iterdir() if p.is_dir()):
        snaps = sorted(f.stem for f in d.glob("*.png"))
        if not snaps:
            continue
        r = status.get(d.name, {})
        bad = r.get("status") == "failed"
        parts.append(f"<h2{' class=failed' if bad else ''}>{html.escape(d.name)}" + (f" · {html.escape(r['status'])}" if r.get("status") else "") + "</h2>")
        if r.get("reason"):
            parts.append(f"<p class='failed'>{html.escape(r['reason'])}</p>")
        parts.append("<div class='grid'>")
        for s in snaps:
            src = html.escape(f"{d.name}/{s}.png")
            parts.append(f"<figure><a href='{src}'><img loading='lazy' src='{src}' alt=''></a><figcaption>{html.escape(s)}</figcaption></figure>")
        parts.append("</div>")
    (out_root / "index.html").write_text("\n".join(parts), encoding="utf-8")


# ---------------------------------------------------------------- run (replay)


def run_path(cap: Capture, doc: dict[str, Any], env: dict[str, str], *, timeout: float, allow_xy: bool) -> list[str]:
    """Replay one path. Snaps keep their recorded names. Returns selector fallback notes."""
    notes: list[str] = []
    cap.read()
    for i, raw in enumerate(doc.get("steps") or [], start=1):
        t0 = time.monotonic()
        res = execute_step(cap, raw, env, timeout=timeout, allow_xy=allow_xy, index=i)
        if res.snap:
            cap.settle(min_wait=0.0)
            cap.snap(res.snap)
        if res.note:
            notes.append(f"step {i} {res.note}")
        cap.timings.append({"step": i, "kind": step_kind(raw)[0], "seconds": round(time.monotonic() - t0, 2)})
    return notes


def cmd_run(args: argparse.Namespace) -> int:
    env = _load_env(args.env_file)
    dev = Device(args.serial)
    out_root = Path(args.out)
    reader = _open_reader(args, dev, out_root)
    results: list[dict[str, Any]] = []
    try:
        for p in args.paths:
            doc = yaml.safe_load(Path(p).read_text(encoding="utf-8")) or {}
            name = str(doc.get("name") or Path(p).stem)
            cap = Capture(reader, dev, out_root / name, settle_timeout=args.settle)
            app = doc.get("app")
            if app and dev.foreground_package() != app:
                print(f"[{name}] skipped: {app} is not on screen")
                results.append({"path": name, "status": "skipped", "reason": f"{app} is not on screen"})
                continue
            t0 = time.monotonic()
            notes: list[str] = []
            try:
                notes = run_path(cap, doc, env, timeout=args.timeout, allow_xy=args.allow_xy)
                status, reason = "passed", ""
            except StepFailed as exc:
                status, reason = "failed", str(exc)
            secs = round(time.monotonic() - t0, 1)
            count = len(list(cap.out_dir.glob("*.png"))) if cap.out_dir.exists() else 0
            print(f"[{name}] {status} in {secs}s, {count} screens" + (f". {reason}" if reason else ""))
            for n in notes:
                print(f"[{name}]   {n}")
            cap.out_dir.mkdir(parents=True, exist_ok=True)
            (cap.out_dir / "timings.json").write_text(json.dumps(cap.timings, indent=2), encoding="utf-8")
            results.append({"path": name, "status": status, "reason": reason, "seconds": secs, "screens": count, "notes": notes})
    finally:
        reader.close()
    (out_root / "run.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    write_index(out_root)
    print(f"Index: {out_root / 'index.html'}")
    return 0 if all(r["status"] != "failed" for r in results) else 1


# ---------------------------------------------------------------- serve (an agent drives, a person helps)


class Session:
    """State behind the local server: one device session, one open path, one hand watcher."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.env = _load_env(args.env_file)
        self.dev = Device(args.serial)
        self.out_root = Path(args.out)
        self.reader = _open_reader(args, self.dev, self.out_root)
        self.lock = threading.Lock()
        self.cap: Capture | None = None
        self.rec: PathRecorder | None = None
        self.watch: HandWatcher | None = None

    def open_path(self, name: str) -> str:
        if self.watch:
            self.watch.close()
        self.cap = Capture(self.reader, self.dev, self.out_root / name, settle_timeout=self.args.settle)
        path_file = Path(self.args.paths_dir) / f"{name}.yaml"
        self.rec = PathRecorder(path_file, name, self.dev.foreground_package(), self.cap)
        self.cap.read()
        if not self.args.no_watch:
            self.watch = HandWatcher(self.cap, self.rec, self.lock, self.args.mask, self.args.mask_var, log=lambda m: print(m, flush=True))
            self.watch.refresh_keyboard()
        return f"path {name}: writing {path_file}, screens in {self.cap.out_dir}"

    def do(self, steps: list[Any], snap: bool) -> dict[str, Any]:
        if self.cap is None or self.rec is None:
            raise StepFailed("No path open. Start with: path <name>")
        t0 = time.monotonic()
        saved: list[str] = []
        notes: list[str] = []
        error = ""
        with self.lock:
            last_label = ""
            for raw in steps:
                try:
                    res = execute_step(self.cap, raw, self.env, timeout=self.args.timeout, allow_xy=self.args.allow_xy, index=self.rec.counter + 1)
                except StepFailed as exc:
                    error = str(exc)
                    if "_MISSING" in error:
                        self.rec.counter += 1  # the saved screen used this number
                    break
                if res.snap:
                    saved.append(self.rec.snap(res.snap, name=res.snap if re.match(r"^\d{3}_", res.snap) else None))
                    last_label = ""
                    continue
                self.rec.add(res.record)
                if res.note:
                    notes.append(res.note)
                last_label = res.label
            if snap and last_label and not error:
                saved.append(self.rec.snap(last_label))
            self.cap.read()
            if self.watch:
                self.watch.refresh_keyboard()
            write_index(self.out_root)
        person = self.watch.take_notes() if self.watch else []
        return {
            "saved": saved,
            "error": error,
            "notes": notes,
            "person": person,
            "seconds": round(time.monotonic() - t0, 2),
            "screen": screen_summary(self.cap),
        }

    def screen(self) -> dict[str, Any]:
        if self.cap is None:
            self.cap = Capture(self.reader, self.dev, self.out_root / "_scratch", settle_timeout=self.args.settle)
        with self.lock:
            self.cap.read()
        person = self.watch.take_notes() if self.watch else []
        return {"saved": [], "error": "", "notes": [], "person": person, "seconds": 0, "screen": screen_summary(self.cap)}

    def close(self) -> None:
        if self.watch:
            self.watch.close()
        self.reader.close()


def cmd_serve(args: argparse.Namespace) -> int:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    session = Session(args)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a: Any) -> None:
            pass

        def _send(self, code: int, body: dict[str, Any]) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:  # noqa: N802
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                if self.path == "/path":
                    self._send(200, {"message": session.open_path(slug(str(req["name"]), 60))})
                elif self.path == "/do":
                    self._send(200, session.do(list(req.get("steps") or []), bool(req.get("snap", True))))
                elif self.path == "/screen":
                    self._send(200, session.screen())
                elif self.path == "/stop":
                    self._send(200, {"message": "stopping"})
                    threading.Thread(target=server.shutdown, daemon=True).start()
                else:
                    self._send(404, {"error": "unknown endpoint"})
            except Exception as exc:  # report, keep serving
                self._send(200, {"error": f"{type(exc).__name__}: {exc}", "saved": [], "notes": [], "person": [], "screen": ""})

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Serving on http://127.0.0.1:{args.port} for {session.dev.serial}. Output {session.out_root}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        session.close()
        write_index(session.out_root)
    return 0


# ---------------------------------------------------------------- client commands (stdlib only, start fast)


def _post(port: int, endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
    from urllib.request import Request, urlopen

    req = Request(f"http://127.0.0.1:{port}{endpoint}", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=600) as resp:
        return json.loads(resp.read())


def _print_result(res: dict[str, Any]) -> int:
    if res.get("message"):
        print(res["message"])
    for p in res.get("person") or []:
        print(f"person: {p}")
    for n in res.get("notes") or []:
        print(f"note: {n}")
    if res.get("saved"):
        print("saved: " + ", ".join(res["saved"]) + f" ({res.get('seconds')}s)")
    if res.get("error"):
        print(f"ERROR: {res['error']}")
    if res.get("screen"):
        print(res["screen"])
    return 1 if res.get("error") else 0


def _selector_from(args: argparse.Namespace) -> dict[str, Any]:
    sel: dict[str, Any] = {}
    if args.target:
        sel["text"] = args.target
    for key in ("id", "desc"):
        if getattr(args, key, None):
            sel[key] = getattr(args, key)
    if getattr(args, "index", None):
        sel["index"] = args.index
    if not sel:
        raise SystemExit("Give the element's text, or --id or --desc.")
    return sel


def cmd_client(args: argparse.Namespace) -> int:
    port = args.port
    c = args.cmd
    if c == "path":
        return _print_result(_post(port, "/path", {"name": args.name}))
    if c == "screen":
        return _print_result(_post(port, "/screen", {}))
    if c == "stop":
        return _print_result(_post(port, "/stop", {}))
    if c == "do":
        text = sys.stdin.read() if args.steps == "-" else args.steps
        steps = yaml.safe_load(text)
        steps = steps if isinstance(steps, list) else [steps]
        return _print_result(_post(port, "/do", {"steps": steps, "snap": not args.no_snap}))
    if c in ("tap", "long_press", "wait_for"):
        step: Any = {c: _selector_from(args)}
    elif c == "type":
        sel = _selector_from(args)
        sel["value"] = args.value
        step = {"type": sel}
    elif c == "keypad":
        step = {"keypad": {"id_template": args.id_template, "value": args.value}}
    elif c == "back":
        step = "back"
    elif c == "swipe":
        step = {"swipe": {"direction": args.direction, "distance": args.distance}}
    elif c == "snap":
        step = {"snap": args.name}
    else:
        raise SystemExit(f"unknown command {c}")
    return _print_result(_post(port, "/do", {"steps": [step], "snap": not getattr(args, "no_snap", False)}))


# ---------------------------------------------------------------- command line


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--port", type=int, default=8790, help="Local port of the serve process.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def device_opts(p: argparse.ArgumentParser) -> None:
        p.add_argument("--serial", help="adb serial. Needed when more than one device is connected.")
        p.add_argument("--out", default=str(REPO / "results" / "screen-capture"), help="Output folder.")
        p.add_argument("--backend", choices=["appium", "adb"], default="appium", help="How the UI tree and screenshots are read.")
        p.add_argument("--appium-url", help="Use a running Appium server instead of starting one.")
        p.add_argument("--settle", type=float, default=4.0, help="Most seconds to wait for a screen to stop changing.")
        p.add_argument("--env-file", help=".env file for ${NAME} values. Values are never printed or written.")
        p.add_argument("--timeout", type=float, default=8.0, help="Seconds to wait for each step's element.")
        p.add_argument("--allow-xy", action="store_true", help="Tap the recorded point when no selector matches.")

    s = sub.add_parser("serve", help="Hold one device session open for an agent (and a helping person).")
    device_opts(s)
    s.add_argument("--paths-dir", default=str(REPO / "results" / "screen-capture" / "paths"), help="Where path files are written.")
    s.add_argument("--no-watch", action="store_true", help="Do not record taps a person makes on the phone.")
    s.add_argument("--mask", default=DEFAULT_MASK, help="Regex on passcode key resource ids. Its last group is the digit.")
    s.add_argument("--mask-var", default="PIN", help="Variable written for a masked keypad entry.")

    r = sub.add_parser("run", help="Replay path files and capture every snap.")
    device_opts(r)
    r.add_argument("paths", nargs="+")

    sub.add_parser("path", help="Start or continue a path (one flow).").add_argument("name")
    sub.add_parser("screen", help="Show the current screen without acting.")
    sub.add_parser("stop", help="Stop the serve process.")
    d = sub.add_parser("do", help="Run several steps in one call. YAML list, or - for stdin.")
    d.add_argument("steps")
    d.add_argument("--no-snap", action="store_true")
    for name in ("tap", "long_press", "wait_for", "type"):
        p = sub.add_parser(name)
        p.add_argument("target", nargs="?", help="Visible text of the element.")
        p.add_argument("--id")
        p.add_argument("--desc")
        p.add_argument("--index", type=int)
        p.add_argument("--no-snap", action="store_true", help="Do not save a screenshot after this step.")
        if name == "type":
            p.add_argument("--value", required=True, help="Text to type. Use ${NAME} for anything secret.")
    k = sub.add_parser("keypad")
    k.add_argument("--id-template", required=True, help='Key resource id with {} for the digit, e.g. "pkg:id/keyboard_key_{}".')
    k.add_argument("--value", required=True, help="Use ${NAME}, never the digits.")
    k.add_argument("--no-snap", action="store_true")
    b = sub.add_parser("back")
    b.add_argument("--no-snap", action="store_true")
    w = sub.add_parser("swipe")
    w.add_argument("direction", choices=["up", "down", "left", "right"])
    w.add_argument("--distance", type=float, default=0.5)
    w.add_argument("--no-snap", action="store_true")
    sub.add_parser("snap").add_argument("name")

    args = ap.parse_args(argv)
    if args.cmd == "serve":
        return cmd_serve(args)
    if args.cmd == "run":
        return cmd_run(args)
    return cmd_client(args)


if __name__ == "__main__":
    sys.exit(main())
