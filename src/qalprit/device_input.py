"""Turn raw Android touch events (`adb shell getevent -lt`) into recordable gestures.

Used by the recorder when someone works the phone or emulator with their own hands: each
finger-down/finger-up pair becomes a tap, long press, swipe, or edge-swipe Back. Nothing here
talks to a device, so the parsing and classification can be tested offline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# "[   12345.678901] /dev/input/event2: EV_ABS       ABS_MT_POSITION_X    00000215"
# The device path is only printed when getevent watches every device.
_LINE_RE = re.compile(
    r"^\[\s*(?P<ts>\d+(?:\.\d+)?)\]\s+(?:(?P<dev>/dev/input/event\d+):\s+)?(?P<type>\w+)\s+(?P<code>\w+)\s+(?P<value>\S+)"
)
_ADD_DEVICE_RE = re.compile(r"add device \d+:\s*(/dev/input/event\d+)")
_ABS_AXIS_RE = re.compile(r"(ABS_MT_POSITION_[XY]|ABS_[XY])\s*:\s*.*?min\s+(-?\d+),\s*max\s+(-?\d+)")
_NAME_RE = re.compile(r'name:\s*"([^"]*)"')
_FRAME_RE = re.compile(r"(?:mFrame|frame)=\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
_CONTENT_INSETS_RE = re.compile(r"mGivenContentInsets=\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")

# A finger that moves less than this (share of the shorter screen side) is a tap, not a swipe.
TAP_SLOP_RATIO = 0.03
LONG_PRESS_SECONDS = 0.6
# Gesture navigation: a horizontal swipe that starts this close to a side edge means Back.
EDGE_BACK_RATIO = 0.05
# A swipe up that starts this close to the bottom edge goes Home or opens recents.
EDGE_HOME_RATIO = 0.97


@dataclass
class TouchDevice:
    path: str
    min_x: int
    max_x: int
    min_y: int
    max_y: int
    name: str = ""
    multitouch: bool = True
    direct: bool = True


@dataclass
class Gesture:
    """One finger gesture in display pixels."""

    kind: str  # tap | long_press | swipe | back | system
    x: int
    y: int
    end_x: int
    end_y: int
    duration_ms: int
    direction: str = ""  # finger direction for swipes: up | down | left | right
    distance: float = 0.0  # share of the screen the finger travelled, 0.1..0.9

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "x": self.x,
            "y": self.y,
            "end_x": self.end_x,
            "end_y": self.end_y,
            "duration_ms": self.duration_ms,
            "direction": self.direction,
            "distance": self.distance,
        }


def pick_touch_device(getevent_lp: str) -> TouchDevice | None:
    """Pick the touchscreen from `getevent -lp` output.

    Phones list several input devices with X/Y axes (touchscreen, stylus, fingerprint sensor,
    mouse). Prefer a multi-touch device marked INPUT_PROP_DIRECT, which is the screen itself.
    """
    best: tuple[int, TouchDevice] | None = None
    for block in re.split(r"(?=add device \d+:)", getevent_lp or ""):
        m_dev = _ADD_DEVICE_RE.search(block)
        if not m_dev:
            continue
        axes = {m.group(1): (int(m.group(2)), int(m.group(3))) for m in _ABS_AXIS_RE.finditer(block)}
        mt_x, mt_y = axes.get("ABS_MT_POSITION_X"), axes.get("ABS_MT_POSITION_Y")
        x_rng = mt_x or axes.get("ABS_X")
        y_rng = mt_y or axes.get("ABS_Y")
        if not (x_rng and y_rng and x_rng[1] > x_rng[0] and y_rng[1] > y_rng[0]):
            continue
        name_m = _NAME_RE.search(block)
        name = name_m.group(1) if name_m else ""
        multitouch = bool(mt_x and mt_y)
        direct = "INPUT_PROP_DIRECT" in block
        score = (2 if multitouch else 0) + (2 if direct else 0)
        if re.search(r"touch|ts|screen", name, re.IGNORECASE):
            score += 1
        if re.search(r"finger|fpc|goodix_fp|stylus|pen|mouse|virtual", name, re.IGNORECASE):
            score -= 3
        dev = TouchDevice(m_dev.group(1), x_rng[0], x_rng[1], y_rng[0], y_rng[1], name, multitouch, direct)
        if best is None or score > best[0]:
            best = (score, dev)
    return best[1] if best else None


def classify_gesture(
    sx: int,
    sy: int,
    ex: int,
    ey: int,
    duration_s: float,
    width: int,
    height: int,
) -> Gesture:
    """Classify one finger-down to finger-up movement (display pixels)."""
    width = max(1, int(width))
    height = max(1, int(height))
    dx, dy = ex - sx, ey - sy
    slop = max(16.0, min(width, height) * TAP_SLOP_RATIO)
    dur_ms = max(0, int(round(duration_s * 1000)))
    base = {"x": sx, "y": sy, "end_x": ex, "end_y": ey, "duration_ms": dur_ms}
    if abs(dx) < slop and abs(dy) < slop:
        kind = "long_press" if duration_s >= LONG_PRESS_SECONDS else "tap"
        return Gesture(kind=kind, **base)

    horizontal = abs(dx) >= abs(dy)
    if horizontal:
        direction = "right" if dx > 0 else "left"
        distance = abs(dx) / width
        from_edge = sx <= width * EDGE_BACK_RATIO or sx >= width * (1 - EDGE_BACK_RATIO)
        if from_edge:
            return Gesture(kind="back", direction=direction, **base)
    else:
        direction = "down" if dy > 0 else "up"
        distance = abs(dy) / height
        if direction == "up" and sy >= height * EDGE_HOME_RATIO:
            return Gesture(kind="system", direction=direction, **base)
        if direction == "down" and sy <= height * (1 - EDGE_HOME_RATIO):
            # Pulling down from the top edge opens the notification shade.
            return Gesture(kind="system", direction=direction, **base)
    distance = round(min(0.9, max(0.1, distance)), 2)
    return Gesture(kind="swipe", direction=direction, distance=distance, **base)


def parse_keyboard_top(dumpsys_input_method_window: str, display_height: int) -> int | None:
    """Find the top edge (display pixels) of the on-screen keyboard from `dumpsys window InputMethod`.

    Android leaves the keyboard out of the app's element tree, so a key press would hit-test to
    whatever app element sits behind it. The recorder uses this edge to ignore key presses.
    """
    text = dumpsys_input_method_window or ""
    if not text.strip():
        return None
    disp_h = max(1, int(display_height))
    frames = [tuple(int(v) for v in m.groups()) for m in _FRAME_RE.finditer(text)]
    insets = _CONTENT_INSETS_RE.search(text)
    inset_top = int(insets.group(2)) if insets else 0
    best: int | None = None
    for left, top, right, bottom in frames:
        if right <= left or bottom <= top or bottom < disp_h * 0.85:
            continue
        cand = top + inset_top if 0 < top + inset_top < bottom else top
        if 0 < cand < disp_h and (best is None or cand > best):
            best = cand
    if best is None:
        return None
    # The IME window often spans the whole screen with the keys at the bottom; a top edge in
    # the upper fifth means only the window was found, not the keys.
    return best if best > disp_h * 0.2 else None


@dataclass
class _TouchState:
    down: bool = False
    slot: int = 0
    multi: bool = False
    cur_x: int | None = None
    cur_y: int | None = None
    start: tuple[int, int, float] | None = None
    last_ts: float = 0.0


@dataclass
class GeteventParser:
    """Stateful parser for `getevent -lt` lines.

    `feed()` returns events as `("down", None)`, `("gesture", Gesture)` or `("key", "back")`.
    "down" fires as soon as a finger touches, so the caller can keep the screen as it was before
    the touch for hit-testing.
    """

    device: TouchDevice
    display_width: int
    display_height: int
    _st: _TouchState = field(default_factory=_TouchState)

    def _to_display(self, rx: int, ry: int) -> tuple[int, int]:
        d = self.device
        px = (rx - d.min_x) / max(1, d.max_x - d.min_x) * self.display_width
        py = (ry - d.min_y) / max(1, d.max_y - d.min_y) * self.display_height
        return int(round(px)), int(round(py))

    def _begin(self, ts: float, events: list[tuple[str, object]]) -> None:
        st = self._st
        if st.down:
            return
        st.down = True
        st.multi = False
        st.start = None
        events.append(("down", None))

    def _maybe_set_start(self, ts: float) -> None:
        st = self._st
        if st.down and st.start is None and st.cur_x is not None and st.cur_y is not None:
            st.start = (st.cur_x, st.cur_y, ts)

    def _end(self, ts: float, events: list[tuple[str, object]]) -> None:
        st = self._st
        if not st.down:
            return
        st.down = False
        start, multi = st.start, st.multi
        st.start = None
        st.multi = False
        if start is None and st.cur_x is not None and st.cur_y is not None:
            # Down and up arrived in one frame.
            start = (st.cur_x, st.cur_y, ts)
        if multi or start is None or st.cur_x is None or st.cur_y is None:
            return
        sx, sy = self._to_display(start[0], start[1])
        ex, ey = self._to_display(st.cur_x, st.cur_y)
        gesture = classify_gesture(
            sx, sy, ex, ey, max(0.0, ts - start[2]), self.display_width, self.display_height
        )
        events.append(("gesture", gesture))

    def feed(self, line: str) -> list[tuple[str, object]]:
        m = _LINE_RE.match((line or "").strip())
        if not m:
            return []
        ts = float(m.group("ts"))
        dev = m.group("dev")
        ev_type, code, value = m.group("type"), m.group("code"), m.group("value")
        events: list[tuple[str, object]] = []
        st = self._st
        st.last_ts = ts

        if ev_type == "EV_KEY" and code == "KEY_BACK":
            if value == "UP":
                events.append(("key", "back"))
            return events

        if dev and dev != self.device.path:
            return events

        if ev_type == "EV_SYN" and code == "SYN_REPORT":
            # The first complete frame after a finger lands holds its start point. Axes that did
            # not change are not repeated, so the last known value still applies.
            self._maybe_set_start(ts)
            return events

        if ev_type == "EV_KEY" and code == "BTN_TOUCH":
            if value == "DOWN":
                self._begin(ts, events)
            elif value == "UP":
                self._end(ts, events)
            return events

        if ev_type != "EV_ABS":
            return events

        if code == "ABS_MT_SLOT":
            try:
                st.slot = int(value, 16)
            except ValueError:
                st.slot = 0
            if st.slot != 0 and st.down:
                st.multi = True
            return events

        if code == "ABS_MT_TRACKING_ID":
            if value.lower() == "ffffffff":
                if st.slot == 0:
                    self._end(ts, events)
            elif st.slot == 0:
                self._begin(ts, events)
            else:
                st.multi = True
            return events

        if st.slot != 0:
            return events
        try:
            num = int(value, 16)
        except ValueError:
            return events
        if code in ("ABS_MT_POSITION_X", "ABS_X"):
            st.cur_x = num
        elif code in ("ABS_MT_POSITION_Y", "ABS_Y"):
            st.cur_y = num
        return events
