"""Live Emulator Activity Recorder: capture taps/inputs interactively as reusable flows & tests."""

from __future__ import annotations

import hashlib
import json
import io
import os
import uuid
import queue
import platform as py_platform
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from qalprit.recorder_paths import step_references_dir
from qalprit.runner import apply_app_reset
from qalprit.steps.base import read_toggle_state
from qalprit.appium_manager import AppiumManager, build_appium_env
from qalprit.doctor import find_android_tool, win_subprocess_kwargs
from qalprit.device_input import Gesture, GeteventParser, TouchDevice, parse_keyboard_top, pick_touch_device
from qalprit.datasets import is_sensitive_column, load_datasets, save_datasets
from qalprit.driver_factory import create_appium_driver
from qalprit.env_loader import SecretMasker
from qalprit.error_text import friendly_error
from qalprit.inspector import extract_interactive_elements
from qalprit.validator import validate_project
from qalprit.workbench_api import (
    _resolve_device_profile,
    _safe_slug,
    save_flow_spec,
    save_screen_locators,
    save_test_journey,
)



def _redact_sensitive_png(png_bytes: bytes, elements: list[dict[str, Any]]) -> bytes:
    """Mask bounding boxes of sensitive elements on preview PNG so secrets never appear."""
    sensitive_rects = [
        el["rect"]
        for el in elements
        if el.get("suggested_sensitive")
        and el.get("visible") is not False
        and isinstance(el.get("rect"), list)
        and len(el["rect"]) == 4
    ]
    if not sensitive_rects or not png_bytes:
        return png_bytes
    try:
        with Image.open(io.BytesIO(png_bytes)) as img:
            copy_img = img.convert("RGBA")
            draw = ImageDraw.Draw(copy_img)
            for x1, y1, x2, y2 in sensitive_rects:
                if x2 > x1 and y2 > y1:
                    draw.rectangle([x1, y1, x2, y2], fill=(15, 23, 42, 235))
            out = io.BytesIO()
            copy_img.save(out, format="PNG")
            return out.getvalue()
    except Exception:
        return png_bytes


# OS password-manager sheets ("Save Password?", "Save password to Google?") sit on top of
# the app after a login. They are not part of the flow under test, so the recorder closes them.
_SYSTEM_PROMPT_RE = re.compile(
    r"\b(save|update|remember)\b.{0,40}\b(password|passkey|username|login|sign-in info)\b",
    re.IGNORECASE,
)
_PROMPT_DISMISS_LABELS = ("not now", "no thanks", "no, thanks", "never", "never for this app", "cancel")


def _element_caption(el: dict[str, Any]) -> str:
    text = str(el.get("text") or "").strip()
    if text and text != SecretMasker.REPLACEMENT:
        return text
    if el.get("recommended_strategy") in {"accessibility_id", "text"}:
        return str(el.get("recommended_value") or "").strip()
    return ""


def find_system_prompt_dismiss(elements: list[dict[str, Any]]) -> tuple[str, dict[str, Any]] | None:
    """Return (prompt title, dismiss button) when an OS password-save sheet covers the app."""
    title = ""
    for el in elements:
        if el.get("visible") is False or el.get("editable"):
            continue
        caption = _element_caption(el)
        if caption and len(caption) <= 80 and caption.endswith("?") and _SYSTEM_PROMPT_RE.search(caption):
            title = caption
            break
    if not title:
        return None
    for wanted in _PROMPT_DISMISS_LABELS:
        for el in elements:
            rect = el.get("rect")
            if el.get("visible") is False or not isinstance(rect, list) or len(rect) != 4:
                continue
            if _element_caption(el).lower() == wanted:
                return title, el
    return None


# Android permission and installer dialogs. They show on the first run after a reset and not
# after the user answered them, so a tap on one is recorded as "only if shown".
_SYSTEM_DIALOG_RE = re.compile(
    r"permissioncontroller|packageinstaller|permission_allow|permission_deny|com\.android\.systemui:id/(?:allow|deny)",
    re.IGNORECASE,
)

# Steps that can be made conditional on their target being on screen.
OPTIONAL_STEP_ACTIONS = frozenset(
    {"tap", "long_press", "type", "set_toggle", "assert_visible", "assert_text", "enter_otp"}
)


def is_system_dialog_element(el: dict[str, Any]) -> bool:
    """True when the element belongs to an OS permission or installer dialog."""
    parts = [str(el.get("recommended_value") or ""), str(el.get("xml_snippet") or "")]
    parts += [str(a.get("value") or "") for a in (el.get("alternatives") or []) if isinstance(a, dict)]
    return bool(_SYSTEM_DIALOG_RE.search(" ".join(parts)))


def _label_optional(step: dict[str, Any]) -> None:
    base = str(step.get("label") or "").removesuffix(" (only if shown)")
    step["label"] = f"{base} (only if shown)" if step.get("optional") else base


def hit_test_element(elements: list[dict[str, Any]], x: float, y: float) -> tuple[int, dict[str, Any]] | None:
    """Return index and dict of the smallest-area visible element containing point (x, y)."""
    best: tuple[int, int, dict[str, Any]] | None = None
    for idx, el in enumerate(elements):
        if el.get("visible") is False:
            continue
        rect = el.get("rect")
        if not isinstance(rect, list) or len(rect) != 4:
            continue
        x1, y1, x2, y2 = rect
        if x1 <= x <= x2 and y1 <= y <= y2:
            area = max(1, (x2 - x1) * (y2 - y1))
            if best is None or area < best[0]:
                best = (area, idx, el)
    if best is None:
        return None
    return best[1], best[2]


def detect_changed_editable_inputs(
    prev_elements: list[dict[str, Any]],
    curr_elements: list[dict[str, Any]],
    require_previous: bool = False,
) -> list[tuple[dict[str, Any], str]]:
    """Detect editable fields whose visible text changed between two XML snapshots.

    With `require_previous`, a field only counts when it was already on the previous screen,
    so a prefilled field on a newly opened screen is not mistaken for typing.
    """
    prev_by_sig: dict[tuple[str, str], dict[str, Any]] = {}
    for el in prev_elements:
        sig = (str(el.get("recommended_strategy") or ""), str(el.get("recommended_value") or ""))
        if sig[1]:
            prev_by_sig[sig] = el

    changes: list[tuple[dict[str, Any], str]] = []
    for cur in curr_elements:
        if not cur.get("editable"):
            continue
        new_txt = str(cur.get("text") or "").strip()
        hint = str(cur.get("hint") or "").strip()
        if not new_txt or new_txt == hint or new_txt == SecretMasker.REPLACEMENT:
            continue
        sig = (str(cur.get("recommended_strategy") or ""), str(cur.get("recommended_value") or ""))
        prev = prev_by_sig.get(sig)
        if require_previous and prev is None:
            continue
        old_txt = str(prev.get("text") or "").strip() if prev else ""
        if new_txt != old_txt:
            changes.append((cur, new_txt))
    return changes


_GENERIC_FIELD_KEY_RE = re.compile(
    r"^(text|edit|input|field|secure|textfield|edittext|securetextfield|text_?input|edit_?text|text_?field|secure_?text_?field)?(_?\d+)?$"
)


def parameterize_typed_steps(
    steps: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], list[str], dict[str, str]]:
    """Swap literal typed values for ``${variable}`` references.

    Returns ``(steps, variables, secret_variables, recorded_values)``. The same field typed twice
    with the same value shares one variable. Values that already reference a variable are kept.
    """
    out: list[dict[str, Any]] = []
    variables: list[str] = []
    secrets: list[str] = []
    values: dict[str, str] = {}
    by_field: dict[tuple[str, str], str] = {}

    for step in steps:
        value = str(step.get("value") or "")
        if str(step.get("action") or "") != "type" or not value or "${" in value:
            out.append(dict(step))
            continue
        target = str(step.get("target") or "")
        secret = bool(step.get("secret", False))
        name = by_field.get((target, value), "")
        if not name:
            key = re.sub(r"[^a-z0-9]+", "_", target.rsplit(".", 1)[-1].lower()).strip("_")
            for suffix in ("_field", "_input", "_edit_text", "_text_field", "_textfield", "_edittext"):
                if key.endswith(suffix) and len(key) > len(suffix):
                    key = key[: -len(suffix)]
            if _GENERIC_FIELD_KEY_RE.match(key):
                key = "password" if secret else ("username" if "@" in value else "value")
            elif secret and not is_sensitive_column(key):
                key = f"{key}_password"
            if key[0].isdigit():
                key = f"v_{key}"
            name, n = key, 2
            while name in values:
                name = f"{key}_{n}"
                n += 1
            by_field[(target, value)] = name
            variables.append(name)
            values[name] = value
            if secret:
                secrets.append(name)
        out.append(dict(step, value="${" + name + "}"))
    return out, variables, secrets, values


def _step_to_yaml_dict(step: dict[str, Any]) -> Any:
    """One recorded step as it is written in the workflow file.

    A step marked optional runs only when its element is on screen: a tap becomes
    ``tap_if_visible`` and anything else is wrapped in ``if_visible``.
    """
    action = str(step.get("action") or "").strip()
    target = str(step.get("target") or "").strip()
    if step.get("optional") and target and action in OPTIONAL_STEP_ACTIONS:
        if action == "tap":
            return {"tap_if_visible": target}
        inner = _plain_step_to_yaml_dict(step)
        return {"if_visible": {"target": target, "then": [inner]}}
    return _plain_step_to_yaml_dict(step)


def _plain_step_to_yaml_dict(step: dict[str, Any]) -> Any:
    action = str(step.get("action") or "").strip()
    target = str(step.get("target") or "").strip()
    value = str(step.get("value") or "")
    secret = bool(step.get("secret", False))
    if action == "type":
        inner: dict[str, Any] = {"target": target, "value": value}
        if secret:
            inner["secret"] = True
        return {"type": inner}
    if action == "enter_otp":
        return {"enter_otp": target}
    if action == "wait_for_user":
        inner_w: dict[str, Any] = {"prompt": value or "Finish this step on the device"}
        if step.get("until"):
            inner_w["until"] = str(step["until"])
        return {"wait_for_user": inner_w}
    if action in ("set_toggle", "assert_toggle"):
        return {action: {"target": target, "state": "off" if value.strip().lower() in {"off", "false", "0"} else "on"}}
    if action == "assert_text":
        return {"assert_text": {"target": target, "contains": value}}
    if action == "swipe":
        if step.get("distance"):
            return {"swipe": {"direction": value or "up", "distance": float(step["distance"])}}
        return {"swipe": value or "up"}
    if action == "long_press":
        try:
            hold = int(float(value))
        except ValueError:
            hold = 0
        return {"long_press": {"target": target, "duration": hold}} if hold > 0 else {"long_press": target}
    if action == "launch":
        if step.get("restart", True):
            return {"launch": {"app": value, "restart": True}}
        return {"launch": value}
    if action == "wait":
        try:
            sec = float(value or 1.0)
        except ValueError:
            sec = 1.0
        return {"wait": sec}
    if action == "back":
        return "back"
    if action == "hide_keyboard":
        return "hide_keyboard"
    return {action: target}


def _connect_phase_text(platform: str, profile: Any) -> str:
    """Tell the user what the slow part of starting a session is, so a long wait doesn't look like a hang."""
    if platform == "ios":
        return (
            "Connecting to the iOS simulator and opening the app. "
            "The first session on a simulator builds WebDriverAgent and can take a few minutes."
        )
    try:
        from qalprit.devices import list_android_devices

        devs, _ = list_android_devices()
        running = any(not d.identifier.startswith("avd:") and d.state == "device" for d in devs)
    except Exception:
        running = True
    if not running and getattr(profile, "target_type", "") == "simulator":
        return (
            "Starting the Android emulator in the background and opening the app. "
            "This can take up to 2 minutes."
        )
    return "Connecting to the device and opening the app..."


_HOME_APPS = {
    "com.android.systemui",
    "com.apple.springboard",
    "com.miui.home",
    "com.android.quickstep",
    "com.google.android.apps.nexuslauncher",
    "com.sec.android.app.launcher",
}


def is_home_app(app_id: str) -> bool:
    """True for the home screen, recents and system UI, which are not apps under test."""
    low = (app_id or "").strip().lower()
    return bool(low) and (low in _HOME_APPS or "launcher" in low)


def _current_app(driver: Any, platform: str) -> str:
    """Package (Android) or bundle id (iOS) of whatever is in front, home screen included, or ''."""
    script = "mobile: getCurrentPackage" if platform == "android" else "mobile: activeAppInfo"
    try:
        res = driver.execute_script(script, {})
    except Exception:
        res = None
    if isinstance(res, dict):
        res = res.get("bundleId") or res.get("package") or ""
    return str(res or "").strip()


def _foreground_app_id(driver: Any, platform: str) -> str:
    """Return the app in front, or '' when it is the home screen or unknown."""
    app_id = _current_app(driver, platform)
    return "" if is_home_app(app_id) else app_id


def save_step_references(
    project_root: Path,
    flow_name: str,
    platform: str,
    steps: list[dict[str, Any]],
    shots: dict[str, bytes],
) -> dict[str, Any]:
    """Write each step's screenshot and a manifest.json that ties it to the flow step.

    The manifest records action and target per step, so a run can tell when the flow was
    edited after recording and skip a screenshot that no longer belongs to that step.
    """
    out_dir = step_references_dir(project_root, flow_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("step_*.png"):
        old.unlink()
    entries: list[dict[str, Any]] = []
    for idx, step in enumerate(steps):
        png = shots.get(str(step.get("uid") or ""), b"")
        name = ""
        if png:
            name = f"step_{idx:02d}.png"
            (out_dir / name).write_bytes(png)
        entries.append({
            "index": idx,
            "action": str(step.get("action") or ""),
            "target": str(step.get("target") or ""),
            "label": str(step.get("label") or ""),
            "screenshot": name,
        })
    manifest = {
        "flow": flow_name,
        "platform": platform,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "steps": entries,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {
        "dir": out_dir.relative_to(project_root).as_posix(),
        "screenshots": sum(1 for e in entries if e["screenshot"]),
    }


def _workspace_rel(project_root: Path, app: Path | None) -> str:
    """Workspace-relative POSIX path of the app, or the bare file name if it lives elsewhere."""
    if app is None:
        return ""
    try:
        return app.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return app.name


# Screen state kept when a finger lands: elements, screen name, keyboard top, redacted PNG, app in front.
_Before = tuple[list[dict[str, Any]], str, "int | None", bytes, str]


class RecorderManager:
    """Manages a single live interactive recording session against an Android Emulator / iOS Simulator."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._driver_lock = threading.Lock()
        self._appium_mgr: AppiumManager | None = None
        self._driver: Any = None
        self._masker: SecretMasker | None = None
        self._getevent_proc: subprocess.Popen[str] | None = None
        self._stop_event = threading.Event()
        self._last_ui_action_ts: float = 0.0
        self._gesture_queue: queue.Queue[tuple[str, Any, Any]] = queue.Queue()
        self._keyboard_top: int | None = None
        self._device_gestures = 0
        self._real_device = False
        self._start_mode = "clear_data"
        self._launch_app_id = ""
        # App in front at the last capture, so switching apps on the device becomes a step.
        self._front_app = ""
        self._apps_seen: set[str] = set()
        # Screenshot of the screen each step left behind, keyed by step uid.
        self._step_shots: dict[str, bytes] = {}
        self._shot_owner = ""
        self._last_ref_png: bytes = b""
        # While a person does a step by hand (selfie, ID scan), taps on the device and app
        # switches are not recorded, and no screenshot is kept for any step.
        self._handoff = False

        self._active = False
        self._starting = False
        self._status = "idle"
        self._phase = ""
        self._error = ""
        self._rev = 0
        self._project_root: Path | None = None
        self._platform = "android"
        self._app_rel = ""
        self._env_name = "qa"
        self._device_profile = ""
        self._serial = ""
        self._screen_name = "screen_1"
        self._flow_name = "recorded_flow"
        self._screen_width = 1080
        self._screen_height = 2400
        self._gesture_width = 1080
        self._gesture_height = 2400
        self._screenshot_png: bytes = b""
        self._elements: list[dict[str, Any]] = []
        self._locators: dict[str, dict[str, Any]] = {}
        self._steps: list[dict[str, Any]] = []
        self._logs: list[str] = []
        self._direct_tap_watch = False
        self._redact_sensitive = True
        self._auto_dismiss_prompts = True
        self._last_raw_hash = ""
        self._notice = ""
        self._notice_rev = 0
        self._refresh_failures = 0
        self._session_token = 0
        self._start_monotonic: float | None = None

    def _log(self, msg: str) -> None:
        with self._lock:
            self._logs.append(msg)
            if len(self._logs) > 80:
                self._logs = self._logs[-80:]

    def status(self) -> dict[str, Any]:
        with self._lock:
            starting_elapsed = 0.0
            if self._starting and self._start_monotonic is not None:
                starting_elapsed = round(time.monotonic() - self._start_monotonic, 1)
            return {
                "active": self._active,
                "starting": self._starting,
                "starting_elapsed_sec": starting_elapsed,
                "status": self._status,
                "phase": self._phase,
                "error": self._error,
                "rev": self._rev,
                "platform": self._platform,
                "env_name": self._env_name,
                "screen_name": self._screen_name,
                "flow_name": self._flow_name,
                "screen_width": self._screen_width,
                "screen_height": self._screen_height,
                "has_screenshot": bool(self._screenshot_png),
                "direct_tap_watch": self._direct_tap_watch,
                "real_device": self._real_device,
                "start_mode": self._start_mode,
                "device_gestures": self._device_gestures,
                "keyboard_up": self._keyboard_top is not None,
                "redact_sensitive": self._redact_sensitive,
                "auto_dismiss_prompts": self._auto_dismiss_prompts,
                "handoff": self._handoff,
                "notice": self._notice,
                "notice_rev": self._notice_rev,
                "elements": list(self._elements[:250]),
                "locators": list(self._locators.values()),
                "steps": [
                    dict(s, yaml_step=_step_to_yaml_dict(s), has_shot=s.get("uid") in self._step_shots)
                    for s in self._steps
                ],
                "logs": list(self._logs[-40:]),
            }

    def screenshot_bytes(self) -> bytes:
        with self._lock:
            return self._screenshot_png

    def step_shot_bytes(self, uid: str) -> bytes:
        """The redacted screenshot a recorded step left on screen, or b''."""
        with self._lock:
            return self._step_shots.get(uid, b"")

    def _set_notice(self, msg: str) -> None:
        with self._lock:
            self._notice = msg
            self._notice_rev += 1
            self._logs.append(msg)

    def _tap_point_locked(self, px: float, py: float) -> None:
        """Tap a screenshot-pixel coordinate on the device while holding `_driver_lock`."""
        driver = self._driver
        if driver is None:
            return
        gx = int(round(px * self._gesture_width / max(1, self._screen_width)))
        gy = int(round(py * self._gesture_height / max(1, self._screen_height)))
        try:
            if self._platform == "ios":
                driver.execute_script("mobile: tap", {"x": gx, "y": gy})
            else:
                driver.execute_script("mobile: clickGesture", {"x": gx, "y": gy})
        except Exception:
            driver.swipe(gx, gy, gx, gy, 60)

    def _capture_snapshot_locked(self) -> None:
        """Capture the screen, closing OS password-save sheets that would block the app."""
        self._capture_once_locked()
        for _ in range(2):
            with self._lock:
                enabled = self._auto_dismiss_prompts
                elements = list(self._elements)
            found = find_system_prompt_dismiss(elements) if enabled else None
            if found is None:
                return
            title, button = found
            x1, y1, x2, y2 = button["rect"]
            try:
                self._tap_point_locked((x1 + x2) / 2, (y1 + y2) / 2)
            except Exception as exc:
                self._log(f"Could not close system prompt '{title}': {friendly_error(exc)}")
                return
            time.sleep(0.9)
            self._set_notice(
                f"Closed the system prompt \"{title}\" ({_element_caption(button)}). It is not part of your flow."
            )
            self._capture_once_locked()

    def _capture_once_locked(self) -> None:
        """Read current XML + PNG while holding `_driver_lock` and scale rects to screenshot pixels."""
        driver = self._driver
        if driver is None:
            return
        xml_text = driver.page_source
        ranked = extract_interactive_elements(xml_text, platform=self._platform, masker=self._masker)
        png_raw = driver.get_screenshot_as_png()
        px_w, px_h = self._screen_width, self._screen_height
        try:
            with Image.open(io.BytesIO(png_raw)) as im:
                px_w, px_h = im.size
        except Exception:
            pass

        win_w, win_h = px_w, px_h
        try:
            ws = driver.get_window_size()
            rw = int(ws.get("width") or 0)
            rh = int(ws.get("height") or 0)
            if rw > 0 and rh > 0:
                win_w, win_h = rw, rh
        except Exception:
            pass

        scale_x = (px_w / win_w) if (win_w > 0 and px_w > 0) else 1.0
        scale_y = (px_h / win_h) if (win_h > 0 and px_h > 0) else 1.0
        if abs(scale_x - 1.0) > 0.01 or abs(scale_y - 1.0) > 0.01:
            rects = [
                el["rect"]
                for el in ranked
                if isinstance(el.get("rect"), list) and len(el["rect"]) == 4
            ]
            max_x2 = max((r[2] for r in rects), default=0)
            max_y2 = max((r[3] for r in rects), default=0)
            # XCUITest (iOS) XML always reports logical UIKit points even when scrollable
            # containers extend past win_h/win_w; never skip scaling iOS Retina rects.
            already_pixel_space = (
                self._platform != "ios"
                and max_x2 > win_w * 1.15
                and max_y2 > win_h * 1.15
                and max_x2 <= px_w * 1.05
                and max_y2 <= px_h * 1.05
            )
            if not already_pixel_space:
                for r_el in ranked:
                    rect = r_el.get("rect")
                    if isinstance(rect, list) and len(rect) == 4:
                        r_el["rect"] = [
                            int(round(rect[0] * scale_x)),
                            int(round(rect[1] * scale_y)),
                            int(round(rect[2] * scale_x)),
                            int(round(rect[3] * scale_y)),
                        ]

        # Prioritize visible on-screen elements ahead of scrolled off-screen items so dense
        # scroll views never crowd out bottom tab bars or fixed controls from status()[:250].
        def _on_screen_rank(el: dict[str, Any]) -> int:
            rect = el.get("rect")
            if el.get("visible") is False or not isinstance(rect, list) or len(rect) != 4:
                return 2
            x1, y1, x2, y2 = rect
            if x2 > 0 and y2 > 0 and x1 < px_w and y1 < px_h and x2 > x1 and y2 > y1:
                return 0
            return 1

        ranked.sort(key=_on_screen_rank)

        with self._lock:
            redact_enabled = self._redact_sensitive
        png_safe = _redact_sensitive_png(png_raw, ranked) if redact_enabled else png_raw
        # Saved step screenshots are always redacted, whatever the mirror shows.
        ref_png = png_safe if redact_enabled else _redact_sensitive_png(png_raw, ranked)
        with self._lock:
            self._last_ref_png = ref_png
            if self._shot_owner:
                self._step_shots[self._shot_owner] = ref_png
            self._elements = ranked
            self._screenshot_png = png_safe
            self._last_raw_hash = hashlib.sha1(png_raw).hexdigest()
            self._screen_width = px_w
            self._screen_height = px_h
            self._gesture_width = win_w
            self._gesture_height = win_h
            self._rev += 1
        self._update_keyboard_locked()
        self._track_front_app_locked()

    def _mark_session_lost(self, exc: BaseException) -> None:
        with self._lock:
            if not self._active:
                return
            self._active = False
            self._status = "failed"
            self._phase = "Session lost"
            self._error = friendly_error(exc)
            self._logs.append(f"Session lost: {self._error}")
            self._rev += 1

    def _refresh_loop(self, interval: float = 0.8) -> None:
        """Keep the mirror in step with the device and keep the Appium session alive while idle."""
        while not self._stop_event.wait(interval):
            if not self._active or self._driver is None:
                continue
            if time.monotonic() - self._last_ui_action_ts < 1.5:
                continue
            if not self._driver_lock.acquire(blocking=False):
                continue
            try:
                driver = self._driver
                if driver is None or not self._active:
                    continue
                raw = driver.get_screenshot_as_png()
                self._refresh_failures = 0
                if hashlib.sha1(raw).hexdigest() == self._last_raw_hash:
                    continue
                with self._lock:
                    prev_elems = list(self._elements)
                    cur_screen = self._screen_name
                self._capture_snapshot_locked()
                self._record_direct_typing(prev_elems, cur_screen, require_previous=True)
            except Exception as exc:
                self._refresh_failures += 1
                if self._refresh_failures >= 3:
                    self._mark_session_lost(exc)
            finally:
                self._driver_lock.release()

    def _record_direct_typing(
        self, prev_elems: list[dict[str, Any]], screen_name: str, require_previous: bool = False
    ) -> None:
        """Turn text typed straight into the emulator into `type` steps."""
        with self._lock:
            new_elems = list(self._elements)
        for changed_el, typed_val in detect_changed_editable_inputs(
            prev_elems, new_elems, require_previous=require_previous
        ):
            loc = self._register_locator_locked(changed_el, screen_name)
            step_val = "${SECRET_VALUE}" if loc["sensitive"] else typed_val
            shown = SecretMasker.REPLACEMENT if loc["sensitive"] else step_val
            self._append_or_merge_step({
                "action": "type",
                "target": loc["target"],
                "value": step_val,
                "secret": loc["sensitive"],
                "label": f"Type '{shown}' into {loc['display_label']}",
            })

    def _register_locator_locked(self, el: dict[str, Any], screen_name: str, key_override: str = "") -> dict[str, Any]:
        clean_screen = _safe_slug(screen_name or self._screen_name, "screen")
        base_key = _safe_slug(key_override or str(el.get("key") or "element"), "element")
        strat = str(el.get("recommended_strategy") or "xpath")
        val = str(el.get("recommended_value") or "")
        rect = el.get("rect") if isinstance(el.get("rect"), list) else None
        with self._lock:
            if el.get("editable") and rect and not key_override:
                # An input keeps its place while its text changes. Reuse the locator saved for
                # the same box so retyping does not create a second, text-based target.
                for existing in self._locators.values():
                    if existing["screen"] == clean_screen and existing.get("editable") and existing.get("rect") == rect:
                        return existing
            key = base_key
            target = f"{clean_screen}.{key}"
            suffix = 2
            while target in self._locators:
                existing = self._locators[target]
                if existing["strategy"] == strat and existing["value"] == val:
                    break
                key = f"{base_key[:35]}_{suffix}"
                target = f"{clean_screen}.{key}"
                suffix += 1
            entry = {
                "target": target,
                "screen": clean_screen,
                "key": key,
                "strategy": strat,
                "value": val,
                "sensitive": bool(el.get("suggested_sensitive", False)),
                "display_label": str(el.get("display_label") or key),
                "editable": bool(el.get("editable")),
                "rect": rect,
            }
            self._locators[target] = entry
        return entry

    def _adb(self, args: list[str], timeout: float = 6.0) -> str:
        adb_bin = find_android_tool("adb")
        if not adb_bin or not self._serial:
            return ""
        try:
            proc = subprocess.run(
                [adb_bin, "-s", self._serial, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=build_appium_env(dict(os.environ)),
                **win_subprocess_kwargs(),
            )
        except Exception:
            return ""
        return (proc.stdout or "") if proc.returncode == 0 else ""

    def _probe_android_touch_device(self, adb_bin: str, serial: str) -> TouchDevice | None:
        """Find the touchscreen among the device's input devices and its raw axis range."""
        try:
            lp = subprocess.run(
                [adb_bin, "-s", serial, "shell", "getevent", "-lp"],
                capture_output=True,
                text=True,
                timeout=6,
                env=build_appium_env(dict(os.environ)),
                **win_subprocess_kwargs(),
            )
        except Exception:
            return None
        if lp.returncode != 0:
            return None
        return pick_touch_device(lp.stdout or "")

    def _track_front_app_locked(self) -> None:
        """Record opening another app from the home screen or recents as a `launch` step.

        A switch the app makes on its own (a browser sign-in, a permission dialog) follows a
        tap that is already recorded, so it is not a step.
        """
        if self._driver is None or self._platform != "android":
            return
        now = _current_app(self._driver, self._platform)
        if not now:
            return
        with self._lock:
            before = self._front_app
            self._front_app = now
            if self._handoff:
                # The camera or another app the person opened by hand is not a step.
                return
            first_time = now not in self._apps_seen
            if not is_home_app(now):
                self._apps_seen.add(now)
        if now == before or is_home_app(now):
            return
        if before and not is_home_app(before):
            return
        self._append_or_merge_step({
            "action": "launch",
            "target": "",
            "value": now,
            "restart": first_time,
            "secret": False,
            "label": f"{'Open' if first_time else 'Switch to'} {now}",
        })
        self._log(f"Recorded opening {now}.")

    def _update_keyboard_locked(self) -> None:
        """Remember where the on-screen keyboard starts, so key presses are not recorded as taps."""
        if self._platform != "android" or not self._direct_tap_watch or self._driver is None:
            return
        top: int | None = None
        try:
            shown = bool(self._driver.is_keyboard_shown())
        except Exception:
            shown = False
        if shown:
            with self._lock:
                scr_h = self._screen_height
            parsed = parse_keyboard_top(self._adb(["shell", "dumpsys", "window", "InputMethod"]), scr_h)
            # Unknown layout: treat the lower 40% as keys rather than record key presses as taps.
            top = parsed if parsed is not None else int(scr_h * 0.6)
        with self._lock:
            self._keyboard_top = top

    def _start_android_getevent_watcher(self, serial: str) -> None:
        """Listen to the device's own touchscreen so taps, swipes and Back done by hand become steps."""
        adb_bin = find_android_tool("adb")
        if not adb_bin or not serial:
            return
        touch = self._probe_android_touch_device(adb_bin, serial)
        if touch is None:
            self._log("Could not find the device's touchscreen. Steps done on the device itself will not be recorded.")
            return
        with self._lock:
            scr_w, scr_h = self._screen_width, self._screen_height
        if scr_w > scr_h:
            self._log("The device is in landscape. Gestures done on the device are mapped for portrait, so keep it upright.")
        parser = GeteventParser(device=touch, display_width=scr_w, display_height=scr_h)
        try:
            # No device path: watch every input device, so hardware Back keys are seen too.
            proc = subprocess.Popen(
                [adb_bin, "-s", serial, "shell", "getevent", "-lt"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                env=build_appium_env(dict(os.environ)),
                **win_subprocess_kwargs(),
            )
        except Exception:
            return

        with self._lock:
            self._getevent_proc = proc
            self._direct_tap_watch = True
            token = self._session_token
        where = "your phone" if self._real_device else "the emulator"
        self._log(f"Listening for taps, swipes and Back on {where} ({serial} · {touch.path}).")
        self._update_keyboard_locked()

        def _reader() -> None:
            before: _Before | None = None
            assert proc.stdout is not None
            for raw_line in proc.stdout:
                if self._stop_event.is_set() or self._session_token != token:
                    break
                for kind, payload in parser.feed(raw_line):
                    if kind == "down":
                        # Keep the screen as it was when the finger landed. By the time the
                        # finger lifts, the app may already show the next screen.
                        with self._lock:
                            before = (
                                list(self._elements),
                                self._screen_name,
                                self._keyboard_top,
                                self._last_ref_png,
                                self._front_app,
                            )
                        continue
                    if time.monotonic() - self._last_ui_action_ts < 1.8:
                        # The recorder itself just drove the device; this is not a new step.
                        continue
                    if kind == "key":
                        self._gesture_queue.put(("back_key", None, None))
                    elif kind == "gesture":
                        self._gesture_queue.put(("gesture", payload, before))
                    before = None

        def _worker() -> None:
            while not self._stop_event.is_set() and self._session_token == token:
                try:
                    item = self._gesture_queue.get(timeout=0.3)
                except queue.Empty:
                    continue
                try:
                    self._handle_device_gesture(*item, token=token)
                except Exception as exc:
                    self._log(f"Could not record a step done on the device: {friendly_error(exc)}")

        threading.Thread(target=_reader, daemon=True).start()
        threading.Thread(target=_worker, daemon=True).start()

    def _handle_device_gesture(
        self,
        kind: str,
        gesture: Gesture | None,
        before: _Before | None,
        *,
        token: int,
    ) -> None:
        """Turn one gesture done on the device into a step, then refresh the mirror."""
        acquired = False
        while not acquired:
            if self._stop_event.is_set() or self._session_token != token:
                return
            acquired = self._driver_lock.acquire(timeout=0.5)
        try:
            if not self._active or self._driver is None:
                return
            with self._lock:
                prev_elems, screen_name, kb_top, before_png, front = before or (
                    list(self._elements), self._screen_name, self._keyboard_top, self._last_ref_png, self._front_app,
                )
                self._device_gestures += 1
                handoff = self._handoff
                prev_owner = self._shot_owner
                if before_png and prev_owner:
                    # The screen right before this gesture is what the previous step led to.
                    self._step_shots[prev_owner] = before_png

            step: dict[str, Any] | None = None
            if handoff:
                # The person is doing this part by hand. Nothing they do is recorded.
                pass
            elif kind == "back_key" or (gesture is not None and gesture.kind == "back"):
                step = {"action": "back", "target": "", "value": "", "secret": False, "label": "Press Back"}
            elif gesture is not None and gesture.kind == "system":
                self._set_notice("Skipped a system gesture (Home, recents or notifications). It is not part of the app's flow.")
            elif gesture is not None and kb_top is not None and gesture.y >= kb_top:
                # A key press on the on-screen keyboard. The typed text is read from the field instead.
                pass
            elif gesture is not None and is_home_app(front) and gesture.kind != "swipe":
                # Opening an app from the home screen is recorded as a `launch` step once it is
                # in front; the icon's position on the home screen is not part of the test.
                pass
            elif gesture is not None and gesture.kind == "swipe":
                step = {
                    "action": "swipe",
                    "target": "",
                    "value": gesture.direction,
                    "distance": gesture.distance,
                    "secret": False,
                    "label": f"Swipe {gesture.direction}",
                }
            elif gesture is not None:
                hit = hit_test_element(prev_elems, gesture.x, gesture.y)
                if hit is None:
                    self._set_notice(
                        f"That {'press' if gesture.kind == 'long_press' else 'tap'} at ({gesture.x}, {gesture.y}) did not land on "
                        "anything in the app, so no step was recorded. For Back, swipe in from the screen edge or click Back here."
                    )
                else:
                    tapped = hit[1]
                    loc = self._register_locator_locked(tapped, screen_name)
                    if gesture.kind == "long_press":
                        step = {
                            "action": "long_press",
                            "target": loc["target"],
                            "value": str(max(600, min(gesture.duration_ms, 5000))),
                            "secret": loc["sensitive"],
                            "label": f"Long press {loc['display_label']}",
                        }
                    elif not tapped.get("editable"):
                        # Taps on a text field are left out: the type step taps it on replay.
                        step = {
                            "action": "tap",
                            "target": loc["target"],
                            "value": "",
                            "secret": loc["sensitive"],
                            "label": f"Tap {loc['display_label']}",
                        }
                        if is_system_dialog_element(tapped):
                            step["optional"] = True
                            _label_optional(step)

            if not self._gesture_queue.empty():
                # More gestures are waiting and each carries its own screen, so skip the refresh.
                if step is not None:
                    self._append_or_merge_step(step)
                return
            time.sleep(0.7)
            if not self._active or self._driver is None:
                if step is not None:
                    self._append_or_merge_step(step)
                return
            with self._lock:
                # This capture shows what the gesture led to, not the previous step's result.
                self._shot_owner = ""
            self._capture_snapshot_locked()
            with self._lock:
                new_elems = list(self._elements)
                if not self._shot_owner:
                    self._shot_owner = prev_owner
            # Text typed on the device right before this gesture (the field may now be gone).
            for changed_el, typed_val in ([] if handoff else detect_changed_editable_inputs(prev_elems, new_elems)):
                loc = self._register_locator_locked(changed_el, screen_name)
                step_val = "${SECRET_VALUE}" if loc["sensitive"] else typed_val
                typed_step = {
                    "action": "type",
                    "target": loc["target"],
                    "value": step_val,
                    "secret": loc["sensitive"],
                    "label": f"Type '{SecretMasker.REPLACEMENT if loc['sensitive'] else step_val}' into {loc['display_label']}",
                }
                self._append_or_merge_step(typed_step)
                if before_png:
                    with self._lock:
                        self._step_shots[typed_step["uid"]] = before_png
            if step is not None:
                self._append_or_merge_step(step)
                self._log(f"Recorded from the device: {step['label']}")
        finally:
            self._driver_lock.release()

    def _append_or_merge_step(self, step: dict[str, Any]) -> None:
        with self._lock:
            # Avoid duplicate consecutive type on same target
            if (
                step["action"] == "type"
                and self._steps
                and self._steps[-1]["action"] == "type"
                and self._steps[-1]["target"] == step["target"]
            ):
                step["uid"] = self._steps[-1].get("uid") or uuid.uuid4().hex[:12]
                self._steps[-1] = step
            else:
                step.setdefault("uid", uuid.uuid4().hex[:12])
                self._steps.append(step)
            # Captures from now until the next step show what this step led to. Until one
            # comes, the latest screen stands in.
            self._shot_owner = step["uid"]
            if self._last_ref_png:
                self._step_shots[step["uid"]] = self._last_ref_png
            self._rev += 1

    def start(
        self,
        project_root: Path,
        *,
        platform: str,
        device_profile: str,
        env_name: str = "qa",
        app_path_str: str = "",
        screen_name: str = "screen_1",
        flow_name: str = "recorded_flow",
        settle_seconds: float = 0.8,
        redact_sensitive: bool | None = None,
        fresh_start: bool = True,
        start_mode: str = "",
    ) -> dict[str, Any]:
        """Attach a persistent Appium session to an emulator, simulator or USB phone and begin recording.

        ``start_mode`` picks how the app starts: ``clear_data`` (wipe it, like a test run),
        ``restart`` (close and reopen the installed app, keeping logins and data) or ``as_is``
        (record from whatever is on screen). Without it, ``fresh_start`` picks ``clear_data`` or
        ``as_is``. A USB phone never has its data wiped unless ``clear_data`` is asked for by name.
        """
        if self._active or self._starting:
            self.stop()

        val = validate_project(project_root, env_name=env_name)
        if env_name not in val.environments:
            raise ValueError(f"Unknown environment '{env_name}'.")
        profile = _resolve_device_profile(val.devices, device_profile, platform)
        env_cfg = val.environments[env_name]

        resolved_app: Path | None = None
        if app_path_str.strip():
            cand = Path(app_path_str.strip())
            resolved_app = cand if cand.is_absolute() else (project_root / cand)
            resolved_app = resolved_app.resolve()
            if not resolved_app.exists():
                raise ValueError(f"App path not found: {resolved_app}")

        self._stop_event.clear()
        with self._lock:
            self._session_token += 1
            token = self._session_token
            self._start_monotonic = time.monotonic()
            self._starting = True
            self._active = False
            self._status = "starting"
            self._phase = "Starting the Appium server..."
            self._error = ""
            self._project_root = project_root
            self._platform = platform
            self._app_rel = _workspace_rel(project_root, resolved_app)
            self._env_name = env_name
            self._device_profile = device_profile
            self._serial = profile.udid or ""
            self._screen_name = _safe_slug(screen_name or "login", "screen")
            self._flow_name = flow_name.strip() or "recorded_flow"
            self._elements = []
            self._locators = {}
            self._steps = []
            self._handoff = False
            self._screenshot_png = b""
            self._last_raw_hash = ""
            self._notice = ""
            self._refresh_failures = 0
            self._direct_tap_watch = False
            self._redact_sensitive = (
                env_cfg.redact_screenshots if redact_sensitive is None else bool(redact_sensitive)
            )
            self._logs = [
                f"Starting live recorder session on {platform} ({device_profile}, env={env_name}, redact={'on' if self._redact_sensitive else 'off'})..."
            ]
            self._masker = val.env_ctx.masker if val.env_ctx else SecretMasker([])

        real_device = profile.target_type == "device"
        mode = (start_mode or "").strip().lower()
        if mode not in {"clear_data", "restart", "as_is"}:
            mode = "restart" if real_device else ("clear_data" if fresh_start else "as_is")
        with self._lock:
            self._real_device = real_device
            self._start_mode = mode
            self._launch_app_id = ""
            self._front_app = ""
            self._apps_seen = set()
            self._step_shots = {}
            self._shot_owner = ""
            self._last_ref_png = b""
            self._keyboard_top = None
            self._device_gestures = 0
            self._gesture_queue = queue.Queue()
        configured_app_id = env_cfg.android_package if platform == "android" else env_cfg.ios_bundle_id

        connect_phase = _connect_phase_text(platform, profile)

        def _cancelled() -> bool:
            return self._stop_event.is_set() or self._session_token != token

        def _boot_worker() -> None:
            log_path = project_root / "results" / "workbench_recorder_appium.log"
            mgr: AppiumManager | None = None
            driver: Any = None
            try:
                mgr = AppiumManager(log_path=log_path, masker=self._masker)
                mgr.__enter__()
                with self._lock:
                    if _cancelled():
                        raise InterruptedError("Recorder start cancelled.")
                    # Stop can now shut Appium down even while the session is still being created.
                    self._appium_mgr = mgr
                    self._phase = connect_phase
                self._log(connect_phase)
                driver = create_appium_driver(mgr.url, profile, env_cfg, app_path=resolved_app)
                if _cancelled():
                    raise InterruptedError("Recorder start cancelled.")
                app_id = configured_app_id or ""
                front = _current_app(driver, platform)
                with self._lock:
                    self._front_app = app_id or front
                    if self._front_app and not is_home_app(self._front_app):
                        self._apps_seen.add(self._front_app)
                if not app_id and mode != "clear_data":
                    # No app named in the environment: use the one open on the device, so the
                    # saved test can reopen it.
                    app_id = _foreground_app_id(driver, platform)
                    with self._lock:
                        self._launch_app_id = app_id
                    if app_id:
                        self._log(f"Recording the app open on the device: {app_id}.")
                if mode == "clear_data":
                    # Test runs reset the app before each test. Do the same here so the first
                    # recorded screen is the one a replay starts from, not wherever the app was left.
                    with self._lock:
                        self._phase = "Resetting the app so recording starts from a clean state..."
                    self._log("Clearing the app's data so recording starts from its first screen.")
                    reset_how = "clear_data"
                elif mode == "restart" and app_id:
                    with self._lock:
                        self._phase = "Reopening the app. Your login and data are kept..."
                    self._log("Reopening the installed app so recording starts from its first screen. Its data is kept.")
                    reset_how = "none"
                else:
                    reset_how = ""
                if reset_how:
                    ok, why = apply_app_reset(
                        driver,
                        reset_how,
                        app_id=app_id,
                        profile=profile,
                        app_path=resolved_app,
                        masker=self._masker,
                    )
                    if not ok:
                        self._log(f"Could not reset the app ({why}). Recording from its current screen instead.")
                with self._lock:
                    self._phase = "Connected. Capturing the first screen..."
                if settle_seconds > 0:
                    time.sleep(min(settle_seconds, 5.0))
                with self._driver_lock:
                    if _cancelled():
                        raise InterruptedError("Recorder start cancelled.")
                    self._appium_mgr = mgr
                    self._driver = driver
                    self._capture_snapshot_locked()
                with self._lock:
                    self._starting = False
                    self._active = True
                    self._status = "recording"
                    self._phase = (
                        "Recording. Use your phone: taps, long presses, swipes, typing and Back become steps."
                        if real_device and platform == "android"
                        else "Recording. Click any control on the mirrored screen."
                    )
                    if not self._serial and isinstance(driver.capabilities, dict):
                        self._serial = str(driver.capabilities.get("deviceUDID") or driver.capabilities.get("deviceName") or "")
                self._log(f"Connected! Captured {len(self._elements)} elements on '{self._screen_name}'.")
                if platform == "android" and self._serial:
                    self._start_android_getevent_watcher(self._serial)
                threading.Thread(target=self._refresh_loop, daemon=True).start()
            except Exception as exc:
                if driver is not None:
                    try:
                        driver.quit()
                    except Exception:
                        pass
                if mgr is not None:
                    try:
                        mgr.__exit__(None, None, None)
                    except Exception:
                        pass
                with self._lock:
                    if self._appium_mgr is mgr:
                        self._appium_mgr = None
                    if self._session_token != token or self._stop_event.is_set():
                        # Stopped by the user, or replaced by a newer start. Stop already set the state.
                        return
                    self._starting = False
                    self._active = False
                    self._status = "failed"
                    self._phase = "Recorder Failed"
                    self._error = friendly_error(exc)
                    self._logs.append(f"Recorder error: {self._error}")

        threading.Thread(target=_boot_worker, daemon=True).start()
        return {"ok": True, "status": "starting"}

    def perform_action(
        self,
        *,
        action: str,
        element_index: int | None = None,
        x: float | None = None,
        y: float | None = None,
        value: str = "",
        secret: bool = False,
        direction: str = "up",
        screen_name: str = "",
        key_override: str = "",
        locator_strategy: str = "",
        locator_value: str = "",
    ) -> dict[str, Any]:
        """Execute a live interaction against the attached emulator and record it cleanly."""
        if not self._active or self._driver is None:
            if self._status == "failed" and self._error:
                raise RuntimeError(self._error)
            raise RuntimeError("No active recording session. Start a session first.")

        act = (action or "tap").strip().lower()
        cur_screen = _safe_slug(screen_name or self._screen_name, "screen")
        with self._lock:
            self._screen_name = cur_screen
            snapshot = list(self._elements)

        self._last_ui_action_ts = time.monotonic()
        with self._driver_lock:
            driver = self._driver
            if driver is None:
                raise RuntimeError("Recording session ended.")

            with self._lock:
                in_handoff = self._handoff
            if act == "wait_for_user":
                if in_handoff:
                    raise ValueError("A hand-off is already open. Do the step on the device, then press Done.")
                prompt = " ".join(str(value or "").split()) or "Finish this step on the device"
                step = {"action": "wait_for_user", "target": "", "value": prompt, "secret": False, "label": f"Wait for user: {prompt}"}
                self._append_or_merge_step(step)
                with self._lock:
                    self._handoff = True
                    # Screens from here on can show a face or an ID card. Keep none of them.
                    self._shot_owner = ""
                    self._rev += 1
                self._log("Hand-off started. Do the step on the device, then press Done. Nothing you do now is recorded.")
                return self.status()
            if act == "wait_for_user_done":
                if not in_handoff:
                    raise ValueError("No hand-off is open.")
                with self._lock:
                    self._handoff = False
                    self._shot_owner = ""
                    self._rev += 1
                # Learn which app is in front now, without recording the switch back as a step.
                self._track_front_app_locked()
                self._capture_snapshot_locked()
                self._log("Hand-off finished. Recording again.")
                return self.status()
            if in_handoff:
                if act == "tap" and x is not None and y is not None and element_index is None:
                    # Pass the tap through to the device (for example the camera shutter) but
                    # do not record it.
                    self._tap_point_locked(float(x), float(y))
                    time.sleep(0.6)
                    self._capture_snapshot_locked()
                    return self.status()
                raise ValueError("Finish the hand-off first: do the step on the device, then press Done.")

            if act in {"back", "swipe", "wait", "hide_keyboard"}:
                if act == "back":
                    driver.back()
                    time.sleep(0.6)
                    step = {"action": "back", "target": "", "value": "", "secret": False, "label": "Press Back"}
                elif act == "hide_keyboard":
                    try:
                        driver.hide_keyboard()
                    except Exception:
                        pass
                    time.sleep(0.5)
                    step = {"action": "hide_keyboard", "target": "", "value": "", "secret": False, "label": "Hide Keyboard"}
                elif act == "wait":
                    sec_val = str(value or "2").strip()
                    step = {"action": "wait", "target": "", "value": sec_val, "secret": False, "label": f"Wait {sec_val}s"}
                else:
                    dir_clean = (direction or value or "up").strip().lower()
                    w, h = self._gesture_width, self._gesture_height
                    if dir_clean == "up":
                        driver.swipe(w // 2, int(h * 0.72), w // 2, int(h * 0.28), 350)
                    elif dir_clean == "down":
                        driver.swipe(w // 2, int(h * 0.28), w // 2, int(h * 0.72), 350)
                    elif dir_clean == "left":
                        driver.swipe(int(w * 0.8), h // 2, int(w * 0.2), h // 2, 350)
                    else:
                        driver.swipe(int(w * 0.2), h // 2, int(w * 0.8), h // 2, 350)
                    time.sleep(0.5)
                    step = {"action": "swipe", "target": "", "value": dir_clean, "secret": False, "label": f"Swipe {dir_clean}"}
                self._append_or_merge_step(step)
                self._capture_snapshot_locked()
                return self.status()

            # Element-based action (tap, type, assert_visible, assert_text)
            target_el: dict[str, Any] | None = None
            if element_index is not None and 0 <= int(element_index) < len(snapshot):
                target_el = snapshot[int(element_index)]
                # The mirror refreshes on its own, so the index the UI sent may point at a
                # different element by now. Trust the locator it sent over the index.
                if locator_value and (
                    str(target_el.get("recommended_strategy") or "") != locator_strategy
                    or str(target_el.get("recommended_value") or "") != locator_value
                ):
                    target_el = next(
                        (
                            el
                            for el in snapshot
                            if str(el.get("recommended_strategy") or "") == locator_strategy
                            and str(el.get("recommended_value") or "") == locator_value
                        ),
                        None,
                    )
                    if target_el is None:
                        raise RuntimeError("The screen changed before the action ran. Pick the element again.")
            elif x is not None and y is not None:
                hit = hit_test_element(snapshot, float(x), float(y))
                if hit is not None:
                    target_el = hit[1]
            if target_el is None:
                if act == "tap" and x is not None and y is not None:
                    # Nothing with a locator sits here (system dialog, canvas, map). Still pass
                    # the tap through so the user is never stuck, but do not record a step.
                    self._tap_point_locked(float(x), float(y))
                    time.sleep(0.75)
                    self._capture_snapshot_locked()
                    self._set_notice(
                        f"Tapped the screen at ({int(float(x))}, {int(float(y))}). No element with a locator is there, so no step was recorded."
                    )
                    return self.status()
                raise ValueError("No element with a locator is at that spot. Pick a highlighted element instead.")

            loc = self._register_locator_locked(target_el, cur_screen, key_override=key_override)
            is_secret = bool(secret or loc["sensitive"])
            if is_secret and value and self._masker is not None:
                self._masker.add(value)

            strat = loc["strategy"]
            loc_val = loc["value"]
            rect = target_el.get("rect") if isinstance(target_el.get("rect"), list) else None

            # A tap on something hidden behind the keyboard would hit a key instead. Close the
            # keyboard first and record that, the same way a hand-written flow would.
            if act in {"tap", "type", "enter_otp"} and target_el.get("visible") is False:
                kb_shown = False
                try:
                    kb_shown = bool(driver.is_keyboard_shown())
                except Exception:
                    kb_shown = False
                if kb_shown:
                    try:
                        driver.hide_keyboard()
                    except Exception:
                        pass
                    time.sleep(0.4)
                    self._append_or_merge_step(
                        {"action": "hide_keyboard", "target": "", "value": "", "secret": False, "label": "Hide Keyboard"}
                    )
                    self._log("Keyboard was covering that element. Added a Hide Keyboard step first.")

            def _find() -> Any:
                try:
                    return driver.find_element(strat, loc_val)
                except Exception as exc:
                    raise RuntimeError(
                        f"'{loc['display_label']}' is no longer on screen. Refresh the mirror and pick it again."
                    ) from exc

            if act == "assert_visible":
                _find()
                step = {
                    "action": "assert_visible",
                    "target": loc["target"],
                    "value": "",
                    "secret": is_secret,
                    "label": f"Verify '{loc['display_label']}' is visible",
                }
            elif act == "assert_toggle":
                elem = _find()
                now_on = read_toggle_state(elem)
                if now_on is None:
                    raise ValueError("That element does not report an on/off state. Pick the switch itself.")
                word = "on" if now_on else "off"
                step = {
                    "action": "assert_toggle",
                    "target": loc["target"],
                    "value": word,
                    "secret": False,
                    "label": f"Verify {loc['display_label']} is {word}",
                }
            elif act == "assert_text":
                elem = _find()
                expected = value or str(target_el.get("text") or "")
                actual = str(getattr(elem, "text", "") or target_el.get("text") or "")
                if expected and expected not in actual:
                    raise ValueError(f"Element text '{actual}' does not contain '{expected}'.")
                step = {
                    "action": "assert_text",
                    "target": loc["target"],
                    "value": expected,
                    "secret": is_secret,
                    "label": f"Verify '{loc['display_label']}' contains '{expected}'",
                }
            elif act == "enter_otp":
                # The code is only typed into the device now. The saved step asks for a fresh
                # code on every run, so no code is stored in the flow, dataset or report.
                if value and self._masker is not None:
                    self._masker.add(value)
                elem = _find()
                elem.click()
                try:
                    elem.clear()
                except Exception:
                    pass
                elem.send_keys(value)
                step = {
                    "action": "enter_otp",
                    "target": loc["target"],
                    "value": "",
                    "secret": True,
                    "label": f"Enter one-time code into {loc['display_label']}",
                }
                time.sleep(0.6)
            elif act == "type":
                elem = _find()
                elem.click()
                try:
                    elem.clear()
                except Exception:
                    pass
                elem.send_keys(value)
                shown_val = SecretMasker.REPLACEMENT if is_secret else value
                step = {
                    "action": "type",
                    "target": loc["target"],
                    "value": value,
                    "secret": is_secret,
                    "label": f"Type '{shown_val}' into {loc['display_label']}",
                }
                time.sleep(0.6)
            else:
                try:
                    driver.find_element(strat, loc_val).click()
                except Exception as exc:
                    if not rect:
                        raise RuntimeError(
                            f"'{loc['display_label']}' is no longer on screen. Refresh the mirror and pick it again."
                        ) from exc
                    # The locator did not resolve but the user can see the element. Tap where it
                    # is drawn, and say so, because replay may need a better locator.
                    self._tap_point_locked((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2)
                    self._log(
                        f"Locator {strat}={loc_val} did not resolve. Tapped its position instead. Check this locator before replay."
                    )
                step = {
                    "action": "tap",
                    "target": loc["target"],
                    "value": "",
                    "secret": is_secret,
                    "label": f"Tap {loc['display_label']}",
                }
                if is_system_dialog_element(target_el):
                    step["optional"] = True
                    _label_optional(step)
                time.sleep(0.75)
                if str(target_el.get("role") or "") == "toggle":
                    # A bare tap replays as a flip, so a rerun can land on the wrong side.
                    # Record the state the switch ended in instead.
                    try:
                        now_on = read_toggle_state(driver.find_element(strat, loc_val))
                    except Exception:
                        now_on = None
                    if now_on is not None:
                        word = "on" if now_on else "off"
                        step = {
                            "action": "set_toggle",
                            "target": loc["target"],
                            "value": word,
                            "secret": is_secret,
                            "label": f"Turn {loc['display_label']} {word}",
                        }

            self._append_or_merge_step(step)
            self._capture_snapshot_locked()
        self._log(f"Recorded: {step['label']} -> {loc['target']}")
        return self.status()

    def sync_screen(self, screen_name: str = "") -> dict[str, Any]:
        """Refresh screenshot/elements and auto-detect any direct emulator text input changes."""
        if not self._active or self._driver is None:
            if self._status == "failed" and self._error:
                raise RuntimeError(self._error)
            raise RuntimeError("No active recording session.")
        self._last_ui_action_ts = time.monotonic()
        if screen_name.strip():
            with self._lock:
                self._screen_name = _safe_slug(screen_name, "screen")
        with self._driver_lock:
            with self._lock:
                prev_elems = list(self._elements)
                cur_screen = self._screen_name
            self._capture_snapshot_locked()
            self._record_direct_typing(prev_elems, cur_screen)
        return self.status()

    def undo(self) -> dict[str, Any]:
        with self._lock:
            if self._steps:
                removed = self._steps.pop()
                if removed.get("action") == "wait_for_user":
                    self._handoff = False
                self._rev += 1
                self._logs.append(f"Removed step: {removed.get('label')}")
        return self.status()

    def clear(self, flow_name: str = "") -> dict[str, Any]:
        """Drop every recorded step and the locators they captured. A live session keeps running.

        ``flow_name`` renames the test being recorded, so a new test does not reuse the old name.
        """
        with self._lock:
            had = len(self._steps)
            self._handoff = False
            if flow_name.strip():
                self._flow_name = flow_name.strip()
                self._screen_name = _safe_slug(self._flow_name.split("/")[-1], "screen")
            self._steps = []
            self._locators = {}
            self._step_shots = {}
            self._shot_owner = ""
            self._notice = ""
            self._rev += 1
            if had:
                self._logs.append(f"Cleared {had} recorded step(s).")
        return self.status()

    def update_step(
        self,
        index: int,
        *,
        value: str | None = None,
        secret: bool | None = None,
        delete: bool = False,
        duplicate: bool = False,
        move_to: int | None = None,
        optional: bool | None = None,
    ) -> dict[str, Any]:
        selected_idx: int | None = index
        with self._lock:
            if not (0 <= index < len(self._steps)):
                raise ValueError("Invalid step index.")
            if delete:
                self._steps.pop(index)
                selected_idx = None
            elif duplicate:
                cloned = dict(self._steps[index])
                cloned["uid"] = uuid.uuid4().hex[:12]
                if self._steps[index].get("uid") in self._step_shots:
                    self._step_shots[cloned["uid"]] = self._step_shots[self._steps[index]["uid"]]
                target_idx = index + 1
                self._steps.insert(target_idx, cloned)
                selected_idx = target_idx
            elif move_to is not None:
                step_item = self._steps.pop(index)
                target_idx = max(0, min(len(self._steps), int(move_to)))
                self._steps.insert(target_idx, step_item)
                selected_idx = target_idx
            else:
                st = self._steps[index]
                if value is not None:
                    st["value"] = value
                if secret is not None:
                    st["secret"] = secret
                if optional is not None:
                    if optional and (st["action"] not in OPTIONAL_STEP_ACTIONS or not st.get("target")):
                        raise ValueError(
                            f"A '{st['action']}' step has no element to look for, so it cannot be 'only if shown'."
                        )
                    st["optional"] = bool(optional)
                if st["action"] == "type":
                    shown = SecretMasker.REPLACEMENT if st["secret"] else st["value"]
                    st["label"] = f"Type '{shown}' into {st['target']}"
                elif st["action"] == "wait_for_user":
                    st["value"] = " ".join(str(st["value"] or "").split()) or "Finish this step on the device"
                    st["label"] = f"Wait for user: {st['value']}"
                _label_optional(st)
            self._rev += 1
        res = self.status()
        res["selected_index"] = selected_idx
        return res

    def save_recorded(
        self,
        *,
        flow_name: str,
        suite: str = "smoke",
        test_id: str = "",
        test_title: str = "",
        create_test: bool = True,
        stop_after: bool = True,
        parameterize: bool = False,
    ) -> dict[str, Any]:
        """Persist captured locators (`screens.yaml`), reusable workflow (`flows/`), and test journey (`specs/`)."""
        with self._lock:
            project_root = self._project_root
            platform = self._platform
            app_rel = self._app_rel
            steps = list(self._steps)
            locators = dict(self._locators)
            start_mode = self._start_mode
            launch_app_id = self._launch_app_id
            real_device = self._real_device
            shots = dict(self._step_shots)
        if project_root is None:
            raise RuntimeError("No active recording workspace.")
        if not steps:
            raise ValueError("Record at least one step before saving.")

        # 1. Group touched locators by screen and merge into screens/screens.yaml
        by_screen: dict[str, list[dict[str, Any]]] = {}
        used_targets = {s.get("target") for s in steps if s.get("target")}
        for target, loc in locators.items():
            if target not in used_targets and used_targets:
                continue
            by_screen.setdefault(loc["screen"], []).append({
                "key": loc["key"],
                "strategy": loc["strategy"],
                "value": loc["value"],
                "sensitive": loc["sensitive"],
            })

        saved_targets: list[str] = []
        for scr, entries in by_screen.items():
            res = save_screen_locators(
                project_root,
                screen_name=scr,
                platform=platform,
                entries=entries,
            )
            saved_targets.extend(res["saved_targets"])

        # 2. Save reusable workflow (`flows/<flow_name>.md`)
        variables: list[str] = []
        secret_vars: list[str] = []
        recorded_values: dict[str, str] = {}
        if parameterize:
            steps, variables, secret_vars, recorded_values = parameterize_typed_steps(steps)
        if start_mode == "restart" and launch_app_id and not (steps and steps[0].get("action") == "launch"):
            # The environment names no app, so the flow reopens the one it was recorded on.
            steps = [{"action": "launch", "target": "", "value": launch_app_id, "label": f"Open {launch_app_id}"}] + steps
        yaml_steps = [_step_to_yaml_dict(s) for s in steps]
        where = "USB device" if real_device else ("emulator" if platform == "android" else "simulator")
        flow_res = save_flow_spec(
            project_root,
            name=flow_name or "recorded_flow",
            params=variables,
            platforms=[platform] if platform in ("android", "ios") else None,
            apps={platform: app_rel} if app_rel and platform in ("android", "ios") else None,
            steps=yaml_steps,
            notes=f"Recorded live on {platform} {where} ({len(yaml_steps)} steps).",
        )

        # Keep what each step left on screen, so replays can be checked against the recording.
        references = save_step_references(project_root, flow_res["name"], platform, steps, shots)

        # 3. Save repeatable Test Journey (`specs/<suite>/<id>.md`)
        spec_res: dict[str, Any] | None = None
        if create_test:
            clean_id = _safe_slug(test_id or f"REC_{flow_res['name']}", "REC-001").upper().replace("_", "-")
            clean_title = (test_title or f"Replay {flow_res['name']} workflow").strip()
            spec_res = save_test_journey(
                project_root,
                {
                    "id": clean_id,
                    "title": clean_title,
                    "suite": suite or "smoke",
                    "tags": [suite or "smoke", "recorded"],
                    "platforms": [platform],
                    # Replays start the app the way the recording did. A test recorded on a phone
                    # with the app set up keeps its data, so a replay never wipes the login.
                    "reset": "clear_data" if start_mode == "clear_data" else "none",
                    "steps": [{"run_flow": flow_res["name"]}],
                    "expected": f"Recorded workflow '{flow_res['name']}' completes all {len(yaml_steps)} steps cleanly.",
                },
            )

        # 4. Keep the typed values as the first dataset row, so the workflow runs as recorded
        #    and more rows (or a CSV import) rerun it with other values.
        dataset_targets: list[tuple[str, str]] = []
        if variables:
            dataset_targets.append(("flow", flow_res["name"]))
            if spec_res is not None:
                dataset_targets.append(("spec", str(spec_res["id"])))
        for kind, name in dataset_targets:
            self._seed_recorded_dataset(project_root, kind, name, variables, secret_vars, recorded_values)

        if stop_after:
            self.stop()
        # The steps now live in flows/, so the recorder starts the next test empty.
        self.clear()

        return {
            "ok": True,
            "saved_targets": saved_targets,
            "flow": flow_res,
            "spec": spec_res,
            "variables": variables,
            "secret_variables": secret_vars,
            "references": references,
        }

    @staticmethod
    def _seed_recorded_dataset(
        project_root: Path,
        kind: str,
        name: str,
        variables: list[str],
        secret_vars: list[str],
        recorded_values: dict[str, str],
    ) -> None:
        existing = load_datasets(project_root, kind, name)  # type: ignore[arg-type]
        all_vars = list(existing.variables) + [v for v in variables if v not in existing.variables]
        rows = [
            {"name": r.name, "selected": r.selected, "expected": r.expected,
             "values": {v: r.values.get(v, recorded_values.get(v, "")) for v in all_vars}}
            for r in existing.rows
        ]
        if not rows:
            rows = [{"name": "recorded", "selected": True, "values": dict(recorded_values)}]
        save_datasets(
            project_root,
            kind,  # type: ignore[arg-type]
            name,
            {
                "variables": all_vars,
                "secret_columns": sorted(set(existing.secret_columns) | set(secret_vars)),
                "rows": rows,
            },
        )

    def set_redaction(self, enabled: bool) -> dict[str, Any]:
        """Toggle black-marker PII redaction on the live emulator mirror."""
        clean = bool(enabled)
        with self._lock:
            self._redact_sensitive = clean
            is_active = self._active and self._driver is not None
        self._log(f"Live emulator black-marker redaction {'enabled' if clean else 'disabled'}.")
        if is_active:
            with self._driver_lock:
                if self._driver is not None:
                    self._capture_snapshot_locked()
        return self.status()

    def stop(self) -> dict[str, Any]:
        """Close persistent Appium recorder session & Android tap listener cleanly."""
        self._stop_event.set()
        with self._lock:
            ge_proc = self._getevent_proc
            self._getevent_proc = None
            self._direct_tap_watch = False
            self._keyboard_top = None
            self._gesture_queue = queue.Queue()
        if ge_proc is not None:
            try:
                ge_proc.terminate()
            except Exception:
                pass

        with self._driver_lock:
            drv = self._driver
            mgr = self._appium_mgr
            self._driver = None
            self._appium_mgr = None
            if drv is not None:
                try:
                    drv.quit()
                except Exception:
                    pass
            if mgr is not None:
                try:
                    mgr.__exit__(None, None, None)
                except Exception:
                    pass

        with self._lock:
            was_active = self._active or self._starting
            self._active = False
            self._starting = False
            self._status = "idle"
            self._phase = "Recorder stopped."
            self._screenshot_png = b""
            self._elements = []
            self._rev += 1
        return {"ok": True, "stopped": was_active}
