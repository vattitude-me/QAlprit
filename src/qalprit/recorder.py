"""Live Emulator Activity Recorder: capture taps/inputs interactively as reusable flows & tests."""

from __future__ import annotations

import io
import os
import platform as py_platform
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from qalprit.appium_manager import AppiumManager, build_appium_env
from qalprit.doctor import find_android_tool, win_subprocess_kwargs
from qalprit.driver_factory import create_appium_driver
from qalprit.env_loader import SecretMasker
from qalprit.inspector import extract_interactive_elements
from qalprit.validator import validate_project
from qalprit.workbench_api import (
    _resolve_device_profile,
    _safe_slug,
    save_flow_spec,
    save_screen_locators,
    save_test_journey,
)

_WM_SIZE_RE = re.compile(r"(?:Override|Physical)\s+size:\s*(\d+)x(\d+)")
_ABS_AXIS_RE = re.compile(r"(ABS_MT_POSITION_[XY]|ABS_[XY])\s*:\s*.*?min\s+(-?\d+),\s*max\s+(-?\d+)")
_GETEVENT_DEV_RE = re.compile(r"add device \d+:\s*(/dev/input/event\d+)")


def _redact_sensitive_png(png_bytes: bytes, elements: list[dict[str, Any]]) -> bytes:
    """Mask bounding boxes of sensitive elements on preview PNG so secrets never appear."""
    sensitive_rects = [
        el["rect"]
        for el in elements
        if el.get("suggested_sensitive") and isinstance(el.get("rect"), list) and len(el["rect"]) == 4
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


def hit_test_element(elements: list[dict[str, Any]], x: float, y: float) -> tuple[int, dict[str, Any]] | None:
    """Return index and dict of the smallest-area visible element containing point (x, y)."""
    best: tuple[int, int, dict[str, Any]] | None = None
    for idx, el in enumerate(elements):
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
) -> list[tuple[dict[str, Any], str]]:
    """Detect editable fields whose visible text changed between two XML snapshots."""
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
        old_txt = str(prev.get("text") or "").strip() if prev else ""
        if new_txt != old_txt:
            changes.append((cur, new_txt))
    return changes


def _step_to_yaml_dict(step: dict[str, Any]) -> Any:
    action = str(step.get("action") or "").strip()
    target = str(step.get("target") or "").strip()
    value = str(step.get("value") or "")
    secret = bool(step.get("secret", False))
    if action == "type":
        inner: dict[str, Any] = {"target": target, "value": value}
        if secret:
            inner["secret"] = True
        return {"type": inner}
    if action == "assert_text":
        return {"assert_text": {"target": target, "contains": value}}
    if action == "swipe":
        return {"swipe": value or "up"}
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

        self._active = False
        self._starting = False
        self._status = "idle"
        self._phase = ""
        self._error = ""
        self._rev = 0
        self._project_root: Path | None = None
        self._platform = "android"
        self._env_name = "qa"
        self._device_profile = ""
        self._serial = ""
        self._screen_name = "screen_1"
        self._flow_name = "recorded_flow"
        self._screen_width = 1080
        self._screen_height = 2400
        self._screenshot_png: bytes = b""
        self._elements: list[dict[str, Any]] = []
        self._locators: dict[str, dict[str, Any]] = {}
        self._steps: list[dict[str, Any]] = []
        self._logs: list[str] = []
        self._direct_tap_watch = False

    def _log(self, msg: str) -> None:
        with self._lock:
            self._logs.append(msg)
            if len(self._logs) > 80:
                self._logs = self._logs[-80:]

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "active": self._active,
                "starting": self._starting,
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
                "elements": list(self._elements[:80]),
                "locators": list(self._locators.values()),
                "steps": [dict(s, yaml_step=_step_to_yaml_dict(s)) for s in self._steps],
                "logs": list(self._logs[-40:]),
            }

    def screenshot_bytes(self) -> bytes:
        with self._lock:
            return self._screenshot_png

    def _capture_snapshot_locked(self) -> None:
        """Read current XML + PNG while holding `_driver_lock` (never blocks waiting on driver)."""
        driver = self._driver
        if driver is None:
            return
        xml_text = driver.page_source
        ranked = extract_interactive_elements(xml_text, platform=self._platform, masker=self._masker)
        png_raw = driver.get_screenshot_as_png()
        png_safe = _redact_sensitive_png(png_raw, ranked)
        w, h = self._screen_width, self._screen_height
        try:
            with Image.open(io.BytesIO(png_raw)) as im:
                w, h = im.size
        except Exception:
            try:
                ws = driver.get_window_size()
                w = int(ws.get("width") or w)
                h = int(ws.get("height") or h)
            except Exception:
                pass
        with self._lock:
            self._elements = ranked
            self._screenshot_png = png_safe
            self._screen_width = w
            self._screen_height = h
            self._rev += 1

    def _register_locator_locked(self, el: dict[str, Any], screen_name: str, key_override: str = "") -> dict[str, Any]:
        clean_screen = _safe_slug(screen_name or self._screen_name, "screen")
        base_key = _safe_slug(key_override or str(el.get("key") or "element"), "element")
        strat = str(el.get("recommended_strategy") or "xpath")
        val = str(el.get("recommended_value") or "")
        with self._lock:
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
            }
            self._locators[target] = entry
        return entry

    def _probe_android_touch_device(self, adb_bin: str, serial: str) -> tuple[str, int, int, int, int, int, int] | None:
        """Discover Android touchscreen getevent device & raw axis bounds cleanly."""
        env = build_appium_env(dict(os.environ))
        try:
            wm = subprocess.run(
                [adb_bin, "-s", serial, "shell", "wm", "size"],
                capture_output=True,
                text=True,
                timeout=5,
                env=env,
                **win_subprocess_kwargs(),
            )
            m_sz = _WM_SIZE_RE.search(wm.stdout or "")
            disp_w = int(m_sz.group(1)) if m_sz else self._screen_width
            disp_h = int(m_sz.group(2)) if m_sz else self._screen_height

            lp = subprocess.run(
                [adb_bin, "-s", serial, "shell", "getevent", "-lp"],
                capture_output=True,
                text=True,
                timeout=6,
                env=env,
                **win_subprocess_kwargs(),
            )
            if lp.returncode != 0:
                return None
            blocks = re.split(r"(?=add device \d+:)", lp.stdout or "")
            for block in blocks:
                m_dev = _GETEVENT_DEV_RE.search(block)
                if not m_dev:
                    continue
                axes = {m.group(1): (int(m.group(2)), int(m.group(3))) for m in _ABS_AXIS_RE.finditer(block)}
                x_rng = axes.get("ABS_MT_POSITION_X") or axes.get("ABS_X")
                y_rng = axes.get("ABS_MT_POSITION_Y") or axes.get("ABS_Y")
                if x_rng and y_rng and x_rng[1] > x_rng[0] and y_rng[1] > y_rng[0]:
                    return (m_dev.group(1), x_rng[0], x_rng[1], y_rng[0], y_rng[1], disp_w, disp_h)
        except Exception:
            return None
        return None

    def _start_android_getevent_watcher(self, serial: str) -> None:
        adb_bin = find_android_tool("adb")
        if not adb_bin or not serial:
            return
        probe = self._probe_android_touch_device(adb_bin, serial)
        if not probe:
            return
        dev_path, min_x, max_x, min_y, max_y, disp_w, disp_h = probe
        env = build_appium_env(dict(os.environ))
        try:
            proc = subprocess.Popen(
                [adb_bin, "-s", serial, "shell", "getevent", "-lt", dev_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                env=env,
                **win_subprocess_kwargs(),
            )
        except Exception:
            return

        with self._lock:
            self._getevent_proc = proc
            self._direct_tap_watch = True
        self._log(f"Watching direct taps on Android emulator ({serial} · {dev_path}).")

        def _reader() -> None:
            cur_x: int | None = None
            cur_y: int | None = None
            assert proc.stdout is not None
            for raw_line in proc.stdout:
                if self._stop_event.is_set():
                    break
                line = raw_line.strip()
                if "ABS_MT_POSITION_X" in line or "ABS_X" in line:
                    try:
                        cur_x = int(line.split()[-1], 16)
                    except Exception:
                        pass
                elif "ABS_MT_POSITION_Y" in line or "ABS_Y" in line:
                    try:
                        cur_y = int(line.split()[-1], 16)
                    except Exception:
                        pass
                elif ("BTN_TOUCH" in line and "UP" in line) or (
                    "ABS_MT_TRACKING_ID" in line and "ffffffff" in line.lower()
                ):
                    if cur_x is None or cur_y is None:
                        continue
                    px = int(((cur_x - min_x) / max(1, max_x - min_x)) * disp_w)
                    py = int(((cur_y - min_y) / max(1, max_y - min_y)) * disp_h)
                    cur_x = None
                    cur_y = None
                    if time.monotonic() - self._last_ui_action_ts < 1.8:
                        continue
                    self._handle_direct_emulator_tap(px, py)

        threading.Thread(target=_reader, daemon=True).start()

    def _handle_direct_emulator_tap(self, px: int, py: int) -> None:
        if not self._driver_lock.acquire(timeout=1.0):
            return
        try:
            if not self._active or self._driver is None:
                return
            with self._lock:
                prev_elems = list(self._elements)
                screen_name = self._screen_name
            hit = hit_test_element(prev_elems, px, py)
            time.sleep(0.8)
            if not self._active or self._driver is None:
                return
            self._capture_snapshot_locked()
            with self._lock:
                new_elems = list(self._elements)

            # Check if text was typed into an editable field right before tapping Next/Submit
            for changed_el, typed_val in detect_changed_editable_inputs(prev_elems, new_elems):
                loc = self._register_locator_locked(changed_el, screen_name)
                if loc["sensitive"]:
                    self._log(f"Captured masked input on {loc['target']}")
                    step_val = "${SECRET_VALUE}"
                else:
                    step_val = typed_val
                self._append_or_merge_step({
                    "action": "type",
                    "target": loc["target"],
                    "value": step_val,
                    "secret": loc["sensitive"],
                    "label": f"Type '{SecretMasker.REPLACEMENT if loc['sensitive'] else step_val}' into {loc['display_label']}",
                })

            if hit is not None:
                _, tapped_el = hit
                loc = self._register_locator_locked(tapped_el, screen_name)
                if not tapped_el.get("editable"):
                    self._append_or_merge_step({
                        "action": "tap",
                        "target": loc["target"],
                        "value": "",
                        "secret": loc["sensitive"],
                        "label": f"Tap {loc['display_label']}",
                    })
                    self._log(f"Direct emulator tap recorded: {loc['target']} ({px}, {py})")
        except Exception as exc:
            self._log(f"Direct tap sync skipped: {exc}")
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
                self._steps[-1] = step
            else:
                self._steps.append(step)
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
    ) -> dict[str, Any]:
        """Attach persistent Appium session to emulator and begin recording."""
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
            self._starting = True
            self._active = False
            self._status = "starting"
            self._phase = "Launching Appium & connecting to emulator..."
            self._error = ""
            self._project_root = project_root
            self._platform = platform
            self._env_name = env_name
            self._device_profile = device_profile
            self._serial = profile.udid or ""
            self._screen_name = _safe_slug(screen_name or "login", "screen")
            self._flow_name = flow_name.strip() or "recorded_flow"
            self._elements = []
            self._locators = {}
            self._steps = []
            self._screenshot_png = b""
            self._direct_tap_watch = False
            self._logs = [f"Starting live recorder session on {platform} ({device_profile})..."]
            self._masker = val.env_ctx.masker if val.env_ctx else SecretMasker([])

        def _boot_worker() -> None:
            log_path = project_root / "results" / "workbench_recorder_appium.log"
            mgr: AppiumManager | None = None
            driver: Any = None
            try:
                mgr = AppiumManager(log_path=log_path, masker=self._masker)
                mgr.__enter__()
                driver = create_appium_driver(mgr.url, profile, env_cfg, app_path=resolved_app)
                if settle_seconds > 0:
                    time.sleep(min(settle_seconds, 5.0))
                with self._driver_lock:
                    self._appium_mgr = mgr
                    self._driver = driver
                    self._capture_snapshot_locked()
                with self._lock:
                    self._starting = False
                    self._active = True
                    self._status = "recording"
                    self._phase = "Recording active — click any element on screen or interact with the emulator."
                    if not self._serial and isinstance(driver.capabilities, dict):
                        self._serial = str(driver.capabilities.get("deviceUDID") or driver.capabilities.get("deviceName") or "")
                self._log(f"Connected! Captured {len(self._elements)} elements on '{self._screen_name}'.")
                if platform == "android" and self._serial:
                    self._start_android_getevent_watcher(self._serial)
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
                    self._starting = False
                    self._active = False
                    self._status = "failed"
                    self._phase = "Recorder Failed"
                    self._error = str(exc)
                    self._logs.append(f"Recorder error: {exc}")

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
    ) -> dict[str, Any]:
        """Execute a live interaction against the attached emulator and record it cleanly."""
        if not self._active or self._driver is None:
            raise RuntimeError("No active recording session. Click Start Recording first.")

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
                    step = {"action": "hide_keyboard", "target": "", "value": "", "secret": False, "label": "Hide Keyboard"}
                elif act == "wait":
                    sec_val = str(value or "2").strip()
                    step = {"action": "wait", "target": "", "value": sec_val, "secret": False, "label": f"Wait {sec_val}s"}
                else:
                    dir_clean = (direction or value or "up").strip().lower()
                    w, h = self._screen_width, self._screen_height
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
            elif x is not None and y is not None:
                hit = hit_test_element(snapshot, float(x), float(y))
                if hit is not None:
                    target_el = hit[1]
            if target_el is None:
                raise ValueError("No interactive element matched that selection on screen.")

            loc = self._register_locator_locked(target_el, cur_screen, key_override=key_override)
            is_secret = bool(secret or loc["sensitive"])
            if is_secret and value and self._masker is not None:
                self._masker.add(value)

            strat = loc["strategy"]
            loc_val = loc["value"]
            if act == "assert_visible":
                driver.find_element(strat, loc_val)
                step = {
                    "action": "assert_visible",
                    "target": loc["target"],
                    "value": "",
                    "secret": is_secret,
                    "label": f"Verify '{loc['display_label']}' is visible",
                }
            elif act == "assert_text":
                elem = driver.find_element(strat, loc_val)
                expected = value or str(target_el.get("text") or "")
                actual = str(getattr(elem, "text", "") or "")
                if expected and expected not in actual:
                    raise ValueError(f"Element text '{actual}' does not contain '{expected}'.")
                step = {
                    "action": "assert_text",
                    "target": loc["target"],
                    "value": expected,
                    "secret": is_secret,
                    "label": f"Verify {loc['target']} contains '{expected}'",
                }
            elif act == "type":
                elem = driver.find_element(strat, loc_val)
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
                time.sleep(0.5)
            else:
                elem = driver.find_element(strat, loc_val)
                elem.click()
                step = {
                    "action": "tap",
                    "target": loc["target"],
                    "value": "",
                    "secret": is_secret,
                    "label": f"Tap {loc['display_label']}",
                }
                time.sleep(0.75)

            self._append_or_merge_step(step)
            self._capture_snapshot_locked()
        self._log(f"Recorded: {step['label']} -> {loc['target']}")
        return self.status()

    def sync_screen(self, screen_name: str = "") -> dict[str, Any]:
        """Refresh screenshot/elements and auto-detect any direct emulator text input changes."""
        if not self._active or self._driver is None:
            raise RuntimeError("No active recording session.")
        if screen_name.strip():
            with self._lock:
                self._screen_name = _safe_slug(screen_name, "screen")
        with self._driver_lock:
            with self._lock:
                prev_elems = list(self._elements)
                cur_screen = self._screen_name
            self._capture_snapshot_locked()
            with self._lock:
                new_elems = list(self._elements)
            for changed_el, typed_val in detect_changed_editable_inputs(prev_elems, new_elems):
                loc = self._register_locator_locked(changed_el, cur_screen)
                step_val = "${SECRET_VALUE}" if loc["sensitive"] else typed_val
                self._append_or_merge_step({
                    "action": "type",
                    "target": loc["target"],
                    "value": step_val,
                    "secret": loc["sensitive"],
                    "label": f"Type '{SecretMasker.REPLACEMENT if loc['sensitive'] else step_val}' into {loc['display_label']}",
                })
        return self.status()

    def undo(self) -> dict[str, Any]:
        with self._lock:
            if self._steps:
                removed = self._steps.pop()
                self._rev += 1
                self._logs.append(f"Removed step: {removed.get('label')}")
        return self.status()

    def update_step(self, index: int, *, value: str | None = None, secret: bool | None = None, delete: bool = False) -> dict[str, Any]:
        with self._lock:
            if not (0 <= index < len(self._steps)):
                raise ValueError("Invalid step index.")
            if delete:
                self._steps.pop(index)
            else:
                st = self._steps[index]
                if value is not None:
                    st["value"] = value
                if secret is not None:
                    st["secret"] = secret
                if st["action"] == "type":
                    shown = SecretMasker.REPLACEMENT if st["secret"] else st["value"]
                    st["label"] = f"Type '{shown}' into {st['target']}"
            self._rev += 1
        return self.status()

    def save_recorded(
        self,
        *,
        flow_name: str,
        suite: str = "smoke",
        test_id: str = "",
        test_title: str = "",
        create_test: bool = True,
        stop_after: bool = True,
    ) -> dict[str, Any]:
        """Persist captured locators (`screens.yaml`), reusable workflow (`flows/`), and test journey (`specs/`)."""
        with self._lock:
            project_root = self._project_root
            platform = self._platform
            steps = list(self._steps)
            locators = dict(self._locators)
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
        yaml_steps = [_step_to_yaml_dict(s) for s in steps]
        flow_res = save_flow_spec(
            project_root,
            name=flow_name or "recorded_flow",
            params=[],
            steps=yaml_steps,
            notes=f"Recorded live on {platform} emulator ({len(yaml_steps)} steps).",
        )

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
                    "reset": "clear_data",
                    "steps": [{"run_flow": flow_res["name"]}],
                    "expected": f"Recorded workflow '{flow_res['name']}' completes all {len(yaml_steps)} steps cleanly.",
                },
            )

        if stop_after:
            self.stop()

        return {
            "ok": True,
            "saved_targets": saved_targets,
            "flow": flow_res,
            "spec": spec_res,
        }

    def stop(self) -> dict[str, Any]:
        """Close persistent Appium recorder session & Android tap listener cleanly."""
        self._stop_event.set()
        with self._lock:
            ge_proc = self._getevent_proc
            self._getevent_proc = None
            self._direct_tap_watch = False
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
            self._rev += 1
        return {"ok": True, "stopped": was_active}
