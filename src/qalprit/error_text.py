"""Turn raw Appium / Selenium / OS exceptions into short messages a tester can act on."""

from __future__ import annotations

import re

_NOISE_MARKERS = ("\nStacktrace:", "; For documentation on this error", "\n  (Session info", "\n    at ")

_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"NoSuchElement|could not be located on the page|no such element", re.I),
        "That element is no longer on screen. The app probably moved on. Refresh the mirror and pick it again.",
    ),
    (
        re.compile(r"StaleElement|stale element", re.I),
        "The screen changed while the action ran. Refresh the mirror and try again.",
    ),
    (
        re.compile(r"InvalidSessionId|session is either terminated|invalid session id|A session is either", re.I),
        "The device session ended (the app or emulator closed, or it sat idle too long). Start the session again.",
    ),
    (
        re.compile(r"Connection refused|Max retries exceeded|RemoteDisconnected|Connection aborted|ECONNREFUSED", re.I),
        "Lost the connection to Appium or the device. Check that the emulator is still running, then start again.",
    ),
    (
        re.compile(r"ECONNRESET|socket hang up|timed out|Timeout", re.I),
        "The device took too long to answer. It may be busy or showing a system dialog. Refresh the mirror and retry.",
    ),
    (
        re.compile(r"xcodebuild failed|WebDriverAgent|Unable to launch WebDriverAgent", re.I),
        "WebDriverAgent could not start on the iOS Simulator. Open Settings & Setup, run the toolchain check, then retry.",
    ),
    (
        re.compile(r"Could not find a connected Android device|device .* (offline|not found)|adb: no devices", re.I),
        "No Android device is connected. Boot an emulator from the device menu first.",
    ),
    (
        re.compile(r"not installed|Cannot find any (free|installed)|bundle .* (is not|was not) (found|installed)", re.I),
        "The app is not installed on this device. Choose an app binary (.apk or .app) in the top bar and retry.",
    ),
)


def clean_error(exc: BaseException | str, limit: int = 320) -> str:
    """Strip stack traces and doc links from a driver error and keep the first useful line."""
    raw = str(exc) if not isinstance(exc, str) else exc
    raw = raw.strip()
    if raw.startswith("Message:"):
        raw = raw[len("Message:"):].strip()
    for marker in _NOISE_MARKERS:
        cut = raw.find(marker)
        if cut > 0:
            raw = raw[:cut]
    raw = re.sub(r"\s+", " ", raw).strip()
    if not raw and not isinstance(exc, str):
        raw = type(exc).__name__
    return raw if len(raw) <= limit else raw[: limit - 1].rstrip() + "…"


def friendly_error(exc: BaseException | str) -> str:
    """Return an actionable sentence for known failures, else the cleaned original message."""
    raw = str(exc) if not isinstance(exc, str) else exc
    probe = f"{type(exc).__name__ if not isinstance(exc, str) else ''} {raw}"
    for pattern, hint in _HINTS:
        if pattern.search(probe):
            detail = clean_error(exc, limit=140)
            return f"{hint} ({detail})" if detail and detail.lower() not in hint.lower() else hint
    return clean_error(exc)
