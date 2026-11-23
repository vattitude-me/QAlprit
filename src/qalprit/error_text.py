"""Turn raw Appium / Selenium / OS exceptions into short messages a tester can act on."""

from __future__ import annotations

import re

_NOISE_MARKERS = (" Consider checking the driver's troubleshooting", " Stacktrace:", "\nStacktrace:", "; For documentation on this error", "\n  (Session info", "\n    at ")

_SESSION_DEAD = re.compile(
    r"instrumentation process is not running|cannot be proxied to UiAutomator2"
    r"|InvalidSessionId|invalid session id|session is either terminated|A session is either"
    r"|Connection refused|ECONNREFUSED|Max retries exceeded|Failed to establish a new connection"
    r"|RemoteDisconnected|Connection aborted",
    re.I,
)

_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"Activity name .{0,200}used to start the app doesn't exist|Cannot start the '[^']+' application", re.I),
        "The app could not be opened. The app file and the environment's app id may be two different apps. "
        "Pick the matching app file, or set the app id in Settings & setup > Apps & environments.",
    ),
    (
        re.compile(r"instrumentation process is not running|cannot be proxied to UiAutomator2", re.I),
        "The UiAutomator2 helper on the phone stopped. Close any other recording or run on this phone, then run again.",
    ),
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
        re.compile(r"Could not find a connected Android device|device .* (offline|not found)|not in the list of connected devices|adb: no devices", re.I),
        "That Android device is not connected. Plug in the phone or boot an emulator from the device menu, then run again.",
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


def is_session_dead(exc: BaseException | str | None) -> bool:
    """True when the error means Appium or the on-device helper is gone, so waiting longer cannot help."""
    if exc is None:
        return False
    raw = str(exc) if not isinstance(exc, str) else exc
    probe = f"{type(exc).__name__ if not isinstance(exc, str) else ''} {raw}"
    return bool(_SESSION_DEAD.search(probe))


_TIMEOUT_RE = re.compile(
    r"Timed out after (?P<secs>[\d.]+)s waiting for '(?P<target>[^']+)' on \w+ "
    r"\[(?P<strategy>[\w ]+)=(?P<value>'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")\] \(state=(?P<state>\w+)"
)
_GENERATED_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_INDEXED_XPATH = re.compile(r"^\(//[\w.]+\)\[\d+\]$")

_STATE_HEADLINES = {
    "hidden": "{target} was found but not visible",
    "disabled": "{target} was on screen but disabled",
    "multiple_matches": "More than one element matched {target}",
    "stale_reference": "{target} kept changing while QAlprit tried to use it",
}


def explain_failure(reason: str | None, category: str | None = None) -> dict[str, str]:
    """Split a step failure into a short headline, a next step, and the cleaned raw detail.

    The Workbench shows the headline and hint, and keeps the detail behind "Show details".
    """
    raw = (reason or "").strip()
    if not raw:
        return {"headline": "", "hint": "", "detail": ""}
    detail = raw.replace("\nStacktrace:", "\n\nStacktrace:")

    if is_session_dead(raw) or category == "appium_session_failure":
        if re.search(r"instrumentation process is not running|cannot be proxied to UiAutomator2", raw, re.I):
            headline = "The connection to the phone dropped"
            hint = (
                "The UiAutomator2 helper on the phone stopped. Close any other recording or run "
                "on this phone, then run again."
            )
        elif re.search(r"Connection refused|ECONNREFUSED|Failed to establish a new connection", raw, re.I):
            headline = "Appium stopped during the run"
            hint = "Run again. QAlprit starts a fresh Appium server for each run."
        else:
            headline = "The device session ended"
            hint = "The app or device closed during the run. Check the phone is unlocked and connected, then run again."
        return {"headline": headline, "hint": hint, "detail": detail}

    m = _TIMEOUT_RE.search(raw)
    if m:
        target = m.group("target")
        secs = float(m.group("secs"))
        state = m.group("state")
        value = m.group("value")[1:-1]
        headline = _STATE_HEADLINES.get(state, "Could not find {target}").format(target=target)
        headline += f" within {secs:g} s"
        if _GENERATED_ID.search(value):
            hint = (
                "This locator includes a generated id that changes on every run. "
                "Pick a steadier locator for it in Elements, such as its text or content description."
            )
        elif _INDEXED_XPATH.match(value):
            hint = (
                "This locator is a position on screen, like the 7th view, so it breaks when the layout shifts. "
                "Check the screenshot. If the right screen is showing, pick a steadier locator in Elements."
            )
        elif state == "not_found":
            hint = (
                "Check the screenshot. If the app shows a different screen, the app or an earlier step "
                "went somewhere else. If the element is there, update its locator in Elements."
            )
        else:
            hint = "Check the screenshot to see what was on screen at that moment."
        return {"headline": headline, "hint": hint, "detail": detail}

    friendly = friendly_error(raw)
    headline, _, rest = friendly.partition(". ")
    return {"headline": headline.rstrip("."), "hint": rest.strip(), "detail": detail}
