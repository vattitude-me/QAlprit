#!/usr/bin/env python3
"""QAlprit Web Capture.

Walks a web portal through a fixed list of steps and saves a screenshot at each
marked point. The same steps repeat once per CSV row, so a search field can be
filled with a different value on every pass.

Login happens once and the session is saved to a file, so later runs skip it
until the session expires. Login tries SSO with the email from .env first. If
the identity provider asks for a password, MFA or anything else the script
cannot answer, the browser stays open for the person to sign in by hand, and
the run continues once the portal shows its signed-in page.

Usage:
    python web_capture.py --config capture.yaml
    python web_capture.py --config capture.yaml --check
    python web_capture.py --config capture.yaml --rows 1-3 --fresh-login

See README.md for the config format.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin

import yaml

VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
SECRET_NAME_RE = re.compile(r"(PASS|SECRET|TOKEN|API_?KEY|OTP|PIN)", re.IGNORECASE)

ACTIONS = (
    "goto", "click", "fill", "type", "press", "select", "check", "uncheck",
    "hover", "scroll", "wait_for", "wait_for_url", "wait", "screenshot",
    "extract", "if_visible",
)
STEP_OPTIONS = {
    "value", "selector", "name", "full_page", "mask", "optional", "timeout",
    "then", "else", "attribute", "all", "state", "delay", "secret", "settle",
}
LOCATOR_KEYS = ("role", "label", "placeholder", "text", "test_id", "title", "alt", "css")
LOGIN_MODES = ("auto", "sso", "manual", "none")

DEFAULT_EMAIL_FIELDS = (
    "input[type=email], input[name=loginfmt], input[name=identifier], "
    "input[name=username], input[name=email], input[autocomplete=username]"
)
DEFAULT_PASSWORD_FIELDS = "input[type=password]"


class CaptureError(Exception):
    """A problem the person running the tool needs to fix or know about."""


# --------------------------------------------------------------------------
# Config, .env, CSV and variables
# --------------------------------------------------------------------------

def load_env_file(path: Path) -> dict[str, str]:
    """Read KEY=VALUE lines. Comments, blank lines and `export ` are allowed."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def substitute(value: Any, variables: dict[str, Any], where: str = "") -> Any:
    """Replace ${NAME} in every string inside value. Unknown names fail."""
    if isinstance(value, str):
        def repl(match: re.Match) -> str:
            name = match.group(1)
            if name not in variables:
                raise CaptureError(
                    f"{where or 'config'}: ${{{name}}} is not set. Add it to .env "
                    f"or as a column in the CSV."
                )
            return str(variables[name])
        return VAR_RE.sub(repl, value)
    if isinstance(value, list):
        return [substitute(item, variables, where) for item in value]
    if isinstance(value, dict):
        return {key: substitute(item, variables, where) for key, item in value.items()}
    return value


def mask_text(text: str, secrets: list[str]) -> str:
    for secret in sorted(secrets, key=len, reverse=True):
        if secret and len(secret) >= 3:
            text = text.replace(secret, "***")
    return text


def slug(text: str, fallback: str = "item") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(text)).strip("-.")
    return cleaned[:80] or fallback


def parse_row_selection(spec: str | None, total: int) -> list[int]:
    """'2,5-7' -> [1, 4, 5, 6] (0-based). None means every row."""
    if not spec:
        return list(range(total))
    picked: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_s, _, end_s = part.partition("-")
            start, end = int(start_s), int(end_s)
        else:
            start = end = int(part)
        if start < 1 or end > total or start > end:
            raise CaptureError(f"--rows {spec}: the CSV has {total} rows (numbered from 1).")
        picked.extend(range(start - 1, end))
    return sorted(set(picked))


def load_rows(csv_path: Path | None) -> list[dict[str, str]]:
    if csv_path is None:
        return [{}]
    if not csv_path.is_file():
        raise CaptureError(f"CSV file not found: {csv_path}")
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        rows = [
            {key.strip(): (value or "").strip() for key, value in row.items() if key}
            for row in csv.DictReader(handle)
        ]
    rows = [row for row in rows if any(row.values())]
    if not rows:
        raise CaptureError(f"{csv_path} has a header but no data rows.")
    return rows


def parse_step(step: Any, where: str) -> tuple[str, Any, dict[str, Any]]:
    if not isinstance(step, dict):
        raise CaptureError(f"{where}: each step must be a mapping such as `- click: \"#save\"`.")
    found = [key for key in step if key in ACTIONS]
    if len(found) != 1:
        raise CaptureError(
            f"{where}: a step needs exactly one action ({', '.join(ACTIONS)}). Found: {list(step)}"
        )
    action = found[0]
    options = {key: value for key, value in step.items() if key != action}
    unknown = set(options) - STEP_OPTIONS
    if unknown:
        raise CaptureError(f"{where}: unknown option(s) {sorted(unknown)} on `{action}`.")
    return action, step[action], options


def validate_steps(steps: Any, where: str = "steps") -> None:
    if not isinstance(steps, list) or not steps:
        raise CaptureError(f"{where}: needs a list of steps.")
    for index, step in enumerate(steps, 1):
        label = f"{where}[{index}]"
        action, target, options = parse_step(step, label)
        if action in ("fill", "type", "select") and "value" not in options:
            raise CaptureError(f"{label}: `{action}` needs a `value`.")
        if action == "extract" and "selector" not in options:
            raise CaptureError(f"{label}: `extract` needs a `selector` to read from.")
        if action == "if_visible":
            validate_steps(options.get("then") or [{"wait": 0}], f"{label}.then")
            if options.get("else"):
                validate_steps(options["else"], f"{label}.else")


def load_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise CaptureError(f"Config file not found: {path}")
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(config, dict):
        raise CaptureError(f"{path}: the top level must be a mapping.")
    if not config.get("base_url"):
        raise CaptureError(f"{path}: set `base_url`, the portal's address.")
    validate_steps(config.get("steps"))
    login = config.setdefault("login", {})
    mode = login.setdefault("mode", "auto")
    if mode not in LOGIN_MODES:
        raise CaptureError(f"login.mode must be one of {LOGIN_MODES}, not {mode!r}.")
    if mode != "none":
        rule = login.get("logged_in_when") or {}
        if not (rule.get("selector") or rule.get("url_contains")):
            raise CaptureError(
                "login.logged_in_when needs a `selector` or `url_contains` that only "
                "shows after sign-in, so the tool knows when login has finished."
            )
        if login.get("sso_steps"):
            validate_steps(login["sso_steps"], "login.sso_steps")
    return config


class Settings:
    """Resolved paths and variables for one run."""

    def __init__(self, config_path: Path, args: argparse.Namespace):
        self.config_path = config_path.resolve()
        self.base_dir = self.config_path.parent
        raw = load_config(self.config_path)

        env_path = Path(args.env) if args.env else self.base_dir / ".env"
        self.env = {**load_env_file(env_path), **{k: v for k, v in os.environ.items() if k.isupper()}}

        # Top level values may use ${ENV} but not CSV columns.
        steps = raw.pop("steps")
        raw_label = (raw.get("data") or {}).pop("label", None)
        self.config = substitute(raw, self.env, "config")
        self.raw_steps = steps
        self.login = self.config["login"]
        if args.login:
            self.login["mode"] = args.login
        self.mode = self.login["mode"]
        self.base_url = str(self.config["base_url"]).rstrip("/") + "/"
        self.headless = bool(args.headless or self.config.get("headless", False))
        self.timeout_ms = int(float(self.config.get("timeout", 15)) * 1000)

        data = self.config.get("data") or {}
        csv_value = args.csv or data.get("csv")
        self.csv_path = self.resolve(csv_value) if csv_value else None
        self.label_template = raw_label or "row-${row}"
        secret_columns = set(data.get("secret_columns") or [])
        self.rows = load_rows(self.csv_path)
        self.selected = parse_row_selection(args.rows, len(self.rows))

        session = self.config.get("session") or {}
        self.session_file = self.resolve(session.get("file", "auth/session.json"))
        self.session_max_age_h = float(session.get("max_age_hours", 8))

        self.output_root = self.resolve(self.config.get("output_dir", "captures"))

        self.secrets = [v for k, v in self.env.items() if SECRET_NAME_RE.search(k) and v]
        for row in self.rows:
            for key, value in row.items():
                if value and (key in secret_columns or SECRET_NAME_RE.search(key)):
                    self.secrets.append(value)

    def resolve(self, value: str | Path) -> Path:
        path = Path(value).expanduser()
        return path if path.is_absolute() else (self.base_dir / path).resolve()

    def row_variables(self, index: int, run_id: str) -> dict[str, Any]:
        return {**self.env, **self.rows[index], "row": index + 1, "run_id": run_id}

    def row_steps(self, index: int, run_id: str) -> tuple[str, list[dict]]:
        variables = self.row_variables(index, run_id)
        where = f"CSV row {index + 1}" if self.csv_path else "steps"
        steps = substitute(self.raw_steps, variables, where)
        label = slug(substitute(self.label_template, variables, f"{where} label"), f"row-{index + 1}")
        return label, steps

    @property
    def landing_file(self) -> Path:
        return self.session_file.with_name(self.session_file.stem + ".landing.json")

    def landing_url(self) -> str | None:
        """The page the last sign-in ended on, used to test a saved session."""
        try:
            return json.loads(self.landing_file.read_text(encoding="utf-8")).get("landing_url")
        except (OSError, ValueError):
            return None

    def session_is_fresh(self) -> bool:
        if not self.session_file.is_file():
            return False
        age_h = (time.time() - self.session_file.stat().st_mtime) / 3600
        return age_h < self.session_max_age_h


# --------------------------------------------------------------------------
# Browser helpers
# --------------------------------------------------------------------------

def locate(page: Any, target: Any) -> Any:
    """Turn a selector string or a locator mapping into a Playwright locator.

    A string is any Playwright selector (CSS, `text=...`, `role=...`, XPath).
    A mapping uses one of: role (+ name), label, placeholder, text, test_id,
    title, alt or css, with optional `nth` and `exact`.
    """
    if isinstance(target, str):
        loc = page.locator(target)
        return loc.first
    if not isinstance(target, dict):
        raise CaptureError(f"Cannot use {target!r} as an element. Give a selector or a mapping.")
    spec = dict(target)
    nth = spec.pop("nth", None)
    exact = spec.pop("exact", None)
    kinds = [key for key in LOCATOR_KEYS if key in spec]
    if len(kinds) != 1:
        raise CaptureError(f"Element {target!r} needs exactly one of {', '.join(LOCATOR_KEYS)}.")
    kind = kinds[0]
    value = spec.pop(kind)
    name = spec.pop("name", None)
    if spec:
        raise CaptureError(f"Element {target!r}: unknown key(s) {sorted(spec)}.")
    if kind == "role":
        loc = page.get_by_role(value, name=name, exact=exact)
    elif kind == "label":
        loc = page.get_by_label(value, exact=exact)
    elif kind == "placeholder":
        loc = page.get_by_placeholder(value, exact=exact)
    elif kind == "text":
        loc = page.get_by_text(value, exact=exact)
    elif kind == "test_id":
        loc = page.get_by_test_id(value)
    elif kind == "title":
        loc = page.get_by_title(value, exact=exact)
    elif kind == "alt":
        loc = page.get_by_alt_text(value, exact=exact)
    else:
        loc = page.locator(value)
    return loc.nth(int(nth)) if nth is not None else loc.first


def describe(target: Any) -> str:
    if isinstance(target, dict):
        return ", ".join(f"{k}={v}" for k, v in target.items())
    return str(target)


def settle(page: Any, timeout_ms: int = 5000) -> None:
    """Give the page a moment to finish loading. Never fails."""
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        pass


def is_logged_in(page: Any, rule: dict[str, Any]) -> bool:
    url_part = rule.get("url_contains")
    if url_part and url_part not in page.url:
        return False
    selector = rule.get("selector")
    if selector:
        try:
            return bool(locate(page, selector).is_visible())
        except Exception:
            return False
    return True


def visible(page: Any, selector: str) -> Any | None:
    try:
        loc = page.locator(selector).first
        return loc if loc.is_visible() else None
    except Exception:
        return None


# --------------------------------------------------------------------------
# Step runner
# --------------------------------------------------------------------------

class StepRunner:
    def __init__(self, page: Any, settings: Settings, out_dir: Path | None,
                 log: Callable[[str], None]):
        self.page = page
        self.settings = settings
        self.out_dir = out_dir
        self.log = log
        self.shot_count = 0
        self.screenshots: list[dict[str, Any]] = []
        self.extracted: dict[str, str] = {}

    def run(self, steps: list[dict], where: str = "step") -> None:
        for index, step in enumerate(steps, 1):
            action, target, options = parse_step(step, f"{where} {index}")
            label = f"{where} {index} {action} {describe(target)}"
            try:
                self.run_one(action, target, options, f"{where} {index}")
            except CaptureError:
                raise
            except Exception as exc:
                message = mask_text(str(exc).splitlines()[0] if str(exc) else repr(exc),
                                    self.settings.secrets)
                if options.get("optional"):
                    self.log(f"    skipped (optional): {mask_text(label, self.settings.secrets)}: {message}")
                    continue
                self.failure_shot()
                raise CaptureError(f"{mask_text(label, self.settings.secrets)} failed: {message}") from None

    def run_one(self, action: str, target: Any, options: dict[str, Any], where: str) -> None:
        page = self.page
        timeout = int(float(options.get("timeout", self.settings.timeout_ms / 1000)) * 1000)
        secret = bool(options.get("secret"))
        shown = "***" if secret else mask_text(describe(options.get("value", "")), self.settings.secrets)

        if action == "goto":
            url = urljoin(self.settings.base_url, str(target).lstrip("/")) if not re.match(r"^[a-z]+://", str(target)) else str(target)
            self.log(f"    goto {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            settle(page)
        elif action == "click":
            self.log(f"    click {describe(target)}")
            locate(page, target).click(timeout=timeout)
            if options.get("settle", True):
                settle(page, 3000)
        elif action == "fill":
            self.log(f"    fill {describe(target)} = {shown}")
            locate(page, target).fill(str(options["value"]), timeout=timeout)
        elif action == "type":
            self.log(f"    type {describe(target)} = {shown}")
            loc = locate(page, target)
            loc.click(timeout=timeout)
            loc.press_sequentially(str(options["value"]), delay=int(options.get("delay", 40)), timeout=timeout)
        elif action == "press":
            key = str(target)
            self.log(f"    press {key}")
            if options.get("selector"):
                locate(page, options["selector"]).press(key, timeout=timeout)
            else:
                page.keyboard.press(key)
            if options.get("settle", True):
                settle(page, 3000)
        elif action == "select":
            self.log(f"    select {describe(target)} = {shown}")
            locate(page, target).select_option(str(options["value"]), timeout=timeout)
        elif action in ("check", "uncheck"):
            self.log(f"    {action} {describe(target)}")
            getattr(locate(page, target), action)(timeout=timeout)
        elif action == "hover":
            self.log(f"    hover {describe(target)}")
            locate(page, target).hover(timeout=timeout)
        elif action == "scroll":
            self.log(f"    scroll {describe(target)}")
            if target == "bottom":
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            elif target == "top":
                page.evaluate("window.scrollTo(0, 0)")
            else:
                locate(page, target).scroll_into_view_if_needed(timeout=timeout)
        elif action == "wait_for":
            self.log(f"    wait for {describe(target)}")
            locate(page, target).wait_for(state=options.get("state", "visible"), timeout=timeout)
        elif action == "wait_for_url":
            self.log(f"    wait for url containing {target}")
            part = str(target)
            page.wait_for_url(lambda url: part in url, timeout=timeout)
        elif action == "wait":
            page.wait_for_timeout(float(target) * 1000)
        elif action == "screenshot":
            self.screenshot(str(target), options, timeout)
        elif action == "extract":
            self.extract(str(target), options, timeout)
        elif action == "if_visible":
            loc = locate(page, target)
            wait_ms = int(float(options.get("timeout", 3)) * 1000)
            try:
                loc.wait_for(state="visible", timeout=wait_ms)
                shown_now = True
            except Exception:
                shown_now = False
            branch = options.get("then") if shown_now else options.get("else")
            self.log(f"    if visible {describe(target)}: {'yes' if shown_now else 'no'}")
            if branch:
                self.run(branch, f"{where}.{'then' if shown_now else 'else'}")

    def screenshot(self, name: str, options: dict[str, Any], timeout: int) -> None:
        if self.out_dir is None:
            self.log(f"    screenshot {name} (not saved during login)")
            return
        settle(self.page)
        self.shot_count += 1
        file_name = f"{self.shot_count:02d}_{slug(name, 'screen')}.png"
        path = self.out_dir / file_name
        # A string mask covers every match, not only the first.
        masks = [self.page.locator(item) if isinstance(item, str) else locate(self.page, item)
                 for item in (options.get("mask") or [])]
        if options.get("selector"):
            locate(self.page, options["selector"]).screenshot(
                path=str(path), mask=masks, mask_color="#000000", timeout=timeout)
        else:
            self.page.screenshot(path=str(path), full_page=bool(options.get("full_page", True)),
                                 mask=masks, mask_color="#000000", timeout=timeout)
        self.screenshots.append({
            "name": name,
            "file": file_name,
            "url": self.page.url,
            "title": self.page.title(),
        })
        self.log(f"    saved {file_name}")

    def extract(self, name: str, options: dict[str, Any], timeout: int) -> None:
        loc = locate(self.page, options["selector"])
        if options.get("all"):
            raw = options["selector"]
            many = self.page.locator(raw) if isinstance(raw, str) else loc
            texts = [t.strip() for t in many.all_inner_texts() if t.strip()]
            value = " | ".join(texts)
        elif options.get("attribute"):
            value = loc.get_attribute(str(options["attribute"]), timeout=timeout) or ""
        else:
            value = loc.inner_text(timeout=timeout)
        value = mask_text(" ".join(value.split()), self.settings.secrets)
        self.extracted[name] = value
        self.log(f"    extract {name} = {value[:80]}")

    def failure_shot(self) -> None:
        if self.out_dir is None:
            return
        try:
            path = self.out_dir / f"{self.shot_count + 1:02d}_FAIL.png"
            self.page.screenshot(path=str(path), full_page=True)
            self.screenshots.append({"name": "FAIL", "file": path.name, "url": self.page.url,
                                     "title": self.page.title()})
        except Exception:
            pass


# --------------------------------------------------------------------------
# Login
# --------------------------------------------------------------------------

def launch_browser(pw: Any, settings: Settings, headless: bool) -> Any:
    kind = settings.config.get("browser", "chromium")
    if kind not in ("chromium", "firefox", "webkit"):
        raise CaptureError("browser must be chromium, firefox or webkit. Use `channel` for Chrome or Edge.")
    kwargs: dict[str, Any] = {"headless": headless}
    if settings.config.get("channel"):
        kwargs["channel"] = settings.config["channel"]
    if settings.config.get("slow_mo"):
        kwargs["slow_mo"] = int(settings.config["slow_mo"])
    return getattr(pw, kind).launch(**kwargs)


def new_context(browser: Any, settings: Settings, use_session: bool) -> Any:
    viewport = settings.config.get("viewport") or {"width": 1440, "height": 900}
    kwargs: dict[str, Any] = {"viewport": viewport}
    if use_session and settings.session_file.is_file():
        kwargs["storage_state"] = str(settings.session_file)
    context = browser.new_context(**kwargs)
    context.set_default_timeout(settings.timeout_ms)
    return context


def wait_until_logged_in(page: Any, rule: dict[str, Any], seconds: float,
                         stop_when: Callable[[], bool] | None = None) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if is_logged_in(page, rule):
            return True
        if stop_when and stop_when():
            return False
        page.wait_for_timeout(1000)
    return is_logged_in(page, rule)


def try_sso(page: Any, settings: Settings, log: Callable[[str], None]) -> bool:
    """Type the SSO email into each login screen it meets until signed in.

    Returns False as soon as a password field shows up, because that means the
    identity provider wants something only the person can give.
    """
    login = settings.login
    rule = login["logged_in_when"]
    seconds = float(login.get("sso_timeout", 45))

    if login.get("sso_steps"):
        variables = {**settings.env}
        steps = substitute(login["sso_steps"], variables, "login.sso_steps")
        StepRunner(page, settings, None, log).run(steps, "sso step")
        return wait_until_logged_in(
            page, rule, seconds, lambda: visible(page, login.get("password_field", DEFAULT_PASSWORD_FIELDS)) is not None
        )

    email = login.get("email") or settings.env.get("SSO_EMAIL")
    if not email:
        log("  No SSO email set (SSO_EMAIL in .env or login.email). Skipping SSO.")
        return False
    if login.get("sso_button"):
        button = visible(page, login["sso_button"]) if isinstance(login["sso_button"], str) else None
        try:
            (button or locate(page, login["sso_button"])).click(timeout=5000)
            settle(page, 3000)
        except Exception:
            log("  SSO button not found, carrying on.")

    email_fields = login.get("email_field", DEFAULT_EMAIL_FIELDS)
    password_fields = login.get("password_field", DEFAULT_PASSWORD_FIELDS)
    confirm = login.get("confirm_buttons") or []
    filled_on: set[str] = set()
    deadline = time.time() + seconds
    while time.time() < deadline:
        if is_logged_in(page, rule):
            return True
        if visible(page, password_fields):
            log("  The sign-in page asks for a password. SSO cannot finish on its own.")
            return False
        field = visible(page, email_fields)
        page_key = page.url.split("?")[0]
        if field is not None and page_key not in filled_on:
            current = (field.input_value() or "").strip()
            if current.lower() != email.lower():
                field.fill(email)
            log(f"  Entered the SSO email on {page_key}")
            field.press("Enter")
            filled_on.add(page_key)
            settle(page, 3000)
            continue
        for selector in confirm:
            button = visible(page, selector)
            if button is not None:
                log(f"  Clicked {selector}")
                button.click()
                settle(page, 3000)
                break
        page.wait_for_timeout(1000)
    return is_logged_in(page, rule)


def login_and_save(pw: Any, settings: Settings, log: Callable[[str], None]) -> None:
    login = settings.login
    mode = settings.mode
    rule = login["logged_in_when"]
    # Manual sign-in needs a window the person can see, even for headless runs.
    headless = settings.headless and mode == "sso"
    browser = launch_browser(pw, settings, headless)
    try:
        context = new_context(browser, settings, use_session=False)
        page = context.new_page()
        url = login.get("url") or settings.base_url
        url = url if re.match(r"^[a-z]+://", url) else urljoin(settings.base_url, url.lstrip("/"))
        log(f"Signing in at {url}")
        page.goto(url, wait_until="domcontentloaded")
        settle(page)

        done = wait_until_logged_in(page, rule, 3)
        if not done and mode in ("auto", "sso"):
            log("  Trying SSO.")
            done = try_sso(page, settings, log)
            if not done and mode == "sso":
                raise CaptureError(
                    "SSO did not finish. Run again with --login manual (or login.mode: auto) "
                    "to sign in by hand."
                )
        if not done:
            seconds = float(login.get("manual_timeout", 300))
            log("")
            log(f"  >>> Sign in to the portal in the browser window. Waiting up to {int(seconds)} s.")
            log("  >>> The capture starts by itself once the signed-in page shows.")
            done = wait_until_logged_in(page, rule, seconds)
            if not done:
                raise CaptureError("Timed out waiting for sign-in. Nothing was captured.")
        log("  Signed in.")
        settings.session_file.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(settings.session_file))
        settings.landing_file.write_text(json.dumps({"landing_url": page.url}), encoding="utf-8")
        for path in (settings.session_file, settings.landing_file):
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        log(f"  Session saved to {settings.session_file} (reused for {settings.session_max_age_h:g} h).")
    finally:
        browser.close()


def session_works(context: Any, settings: Settings) -> bool:
    page = context.new_page()
    try:
        url = settings.login.get("check_url") or settings.landing_url() or settings.base_url
        url = url if re.match(r"^[a-z]+://", url) else urljoin(settings.base_url, url.lstrip("/"))
        page.goto(url, wait_until="domcontentloaded")
        settle(page)
        return wait_until_logged_in(page, settings.login["logged_in_when"], 8)
    except Exception:
        return False
    finally:
        page.close()


# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------

def write_outputs(run_dir: Path, manifest: dict[str, Any]) -> None:
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    fields = sorted({key for row in manifest["rows"] for key in row["extracted"]})
    if fields:
        with (run_dir / "extracted.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["row", "label", "status", *fields])
            for row in manifest["rows"]:
                writer.writerow([row["row"], row["label"], row["status"],
                                 *[row["extracted"].get(f, "") for f in fields]])

    parts = []
    for row in manifest["rows"]:
        shots = "".join(
            f'<figure><a href="{html.escape(row["label"])}/{html.escape(s["file"])}">'
            f'<img loading="lazy" src="{html.escape(row["label"])}/{html.escape(s["file"])}" alt=""></a>'
            f'<figcaption>{html.escape(s["name"])}<br><small>{html.escape(s["url"])}</small></figcaption></figure>'
            for s in row["screenshots"]
        )
        data = "".join(
            f"<tr><th>{html.escape(k)}</th><td>{html.escape(v)}</td></tr>"
            for k, v in row["extracted"].items()
        )
        error = f'<p class="err">{html.escape(row["error"])}</p>' if row.get("error") else ""
        parts.append(
            f'<section><h2>{row["row"]}. {html.escape(row["label"])} '
            f'<span class="{row["status"]}">{row["status"]}</span></h2>{error}'
            f'{f"<table>{data}</table>" if data else ""}<div class="grid">{shots}</div></section>'
        )
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Web capture {html.escape(manifest["run_id"])}</title>
<style>
:root{{--bg:#f3f6f9;--fg:#0f172a;--card:#fff;--line:#d6dde5;--accent:#0f766e;--bad:#b42318}}
@media (prefers-color-scheme:dark){{:root{{--bg:#090e1a;--fg:#e2e8f0;--card:#111827;--line:#263244;--accent:#2dd4bf;--bad:#f97066}}}}
body{{margin:0;padding:24px 16px;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,sans-serif}}
h1{{font-size:20px}} h2{{font-size:16px}} section{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:16px;margin:16px 0}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}}
figure{{margin:0}} img{{width:100%;border:1px solid var(--line);border-radius:4px}}
figcaption small{{opacity:.7;word-break:break-all}} .passed{{color:var(--accent)}} .failed,.err{{color:var(--bad)}}
table{{border-collapse:collapse;margin:8px 0}} th,td{{text-align:left;padding:4px 12px 4px 0;border-bottom:1px solid var(--line)}}
</style></head><body>
<h1>Web capture {html.escape(manifest["run_id"])}</h1>
<p>{html.escape(manifest["base_url"])} · {manifest["passed"]} passed, {manifest["failed"]} failed · {html.escape(manifest["started"])}</p>
{''.join(parts)}
</body></html>"""
    (run_dir / "index.html").write_text(page, encoding="utf-8")


def check_only(settings: Settings) -> int:
    run_id = "check"
    print(f"Config: {settings.config_path}")
    print(f"Portal: {settings.base_url}")
    print(f"Login: {settings.mode}" + (
        f" (session {'fresh' if settings.session_is_fresh() else 'missing or expired'})"
        if settings.mode != "none" else ""))
    if settings.mode in ("auto", "sso") and not settings.login.get("sso_steps"):
        has_email = bool(settings.login.get("email") or settings.env.get("SSO_EMAIL"))
        print(f"SSO email: {'set' if has_email else 'NOT SET (add SSO_EMAIL to .env)'}")
    for index in settings.selected:
        label, steps = settings.row_steps(index, run_id)
        validate_steps(steps)
        print(f"  row {index + 1}: {label} ({len(steps)} steps) ok")
    print("All rows resolve.")
    return 0


def run(settings: Settings) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise CaptureError(
            "Playwright is not installed. Run:\n"
            "  pip install -r tools/web-capture/requirements.txt\n"
            "  python -m playwright install chromium"
        ) from None

    # Resolve every row before opening a browser, so a missing column fails fast.
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    planned = [(index, *settings.row_steps(index, run_id)) for index in settings.selected]

    run_dir = settings.output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    log_file = (run_dir / "capture.log").open("w", encoding="utf-8")

    def log(message: str) -> None:
        message = mask_text(message, settings.secrets)
        print(message, flush=True)
        log_file.write(message + "\n")

    manifest: dict[str, Any] = {
        "run_id": run_id,
        "config": str(settings.config_path),
        "base_url": settings.base_url,
        "started": datetime.now().isoformat(timespec="seconds"),
        "rows": [],
    }

    try:
        with sync_playwright() as pw:
            logged_in_now = False
            if settings.mode != "none" and not settings.session_is_fresh():
                login_and_save(pw, settings, log)
                logged_in_now = True

            browser = launch_browser(pw, settings, settings.headless)
            context = new_context(browser, settings, use_session=settings.mode != "none")
            if settings.mode != "none" and not session_works(context, settings):
                if logged_in_now:
                    raise CaptureError(
                        "Signed in, but the saved session does not open the portal. The portal "
                        "may keep its login in sessionStorage. Check login.logged_in_when and check_url."
                    )
                log("Saved session has expired. Signing in again.")
                context.close()
                browser.close()
                login_and_save(pw, settings, log)
                browser = launch_browser(pw, settings, settings.headless)
                context = new_context(browser, settings, use_session=True)
                if not session_works(context, settings):
                    raise CaptureError("Still not signed in after a fresh login.")

            stop_on_error = bool(settings.config.get("stop_on_error", False))
            for index, label, steps in planned:
                row_dir = run_dir / label
                if row_dir.exists():
                    label = f"{label}-{index + 1}"
                    row_dir = run_dir / label
                row_dir.mkdir(parents=True)
                log(f"Row {index + 1}: {label}")
                page = context.new_page()
                runner = StepRunner(page, settings, row_dir, log)
                status, error = "passed", ""
                try:
                    runner.run(steps)
                except CaptureError as exc:
                    status, error = "failed", mask_text(str(exc), settings.secrets)
                    log(f"  FAILED: {error}")
                finally:
                    page.close()
                manifest["rows"].append({
                    "row": index + 1,
                    "label": label,
                    "status": status,
                    "error": error,
                    "screenshots": runner.screenshots,
                    "extracted": runner.extracted,
                })
                if status == "failed" and stop_on_error:
                    log("Stopping because stop_on_error is on.")
                    break

            # Keep the session fresh for the next run.
            if settings.mode != "none":
                context.storage_state(path=str(settings.session_file))
            context.close()
            browser.close()
    finally:
        manifest["finished"] = datetime.now().isoformat(timespec="seconds")
        manifest["passed"] = sum(1 for r in manifest["rows"] if r["status"] == "passed")
        manifest["failed"] = sum(1 for r in manifest["rows"] if r["status"] == "failed")
        write_outputs(run_dir, manifest)
        log(f"Done. {manifest['passed']} passed, {manifest['failed']} failed.")
        log(f"Open {run_dir / 'index.html'}")
        log_file.close()
    return 0 if manifest["failed"] == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="web_capture",
        description="QAlprit Web Capture: click through a web portal and save screenshots.",
    )
    parser.add_argument("--config", "-c", default="capture.yaml", help="Steps file (default: capture.yaml)")
    parser.add_argument("--env", help="Path to the .env file (default: .env next to the config)")
    parser.add_argument("--csv", help="CSV with one row per pass. Overrides data.csv in the config.")
    parser.add_argument("--rows", help="Only these CSV rows, numbered from 1, for example 1-3,7")
    parser.add_argument("--login", choices=LOGIN_MODES, help="Override login.mode")
    parser.add_argument("--fresh-login", action="store_true", help="Ignore the saved session and sign in again")
    parser.add_argument("--headless", action="store_true", help="Hide the browser (manual sign-in still opens a window)")
    parser.add_argument("--check", action="store_true", help="Check the config and CSV without opening a browser")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = Settings(Path(args.config), args)
        if args.check:
            return check_only(settings)
        if args.fresh_login and settings.session_file.is_file():
            settings.session_file.unlink()
        return run(settings)
    except CaptureError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
