"""Fetch the Jira issues and Confluence pages a test points to, and keep a local copy.

A test lists them under `references:` in its front matter, for example::

    references:
      - SHOP-142
      - https://acme.atlassian.net/wiki/spaces/QA/pages/123456/Checkout+flow

Credentials come from the workspace `.env` (never from the spec):

    ATLASSIAN_BASE_URL=https://acme.atlassian.net
    ATLASSIAN_EMAIL=you@acme.com        # Atlassian Cloud; leave empty for a Data Center PAT
    ATLASSIAN_API_TOKEN=...

`JIRA_BASE_URL` and `CONFLUENCE_BASE_URL` override the base for sites where the two live on
different hosts. Jira and Confluence can also have their own credentials (a personal access
token for each, for example): `JIRA_EMAIL` / `JIRA_API_TOKEN` and `CONFLUENCE_EMAIL` /
`CONFLUENCE_API_TOKEN`. A product with its own token uses its own email (blank means a Bearer
PAT); a product without one falls back to the shared `ATLASSIAN_*` pair. The token is only ever
sent to those configured hosts. Fetched text is saved
under `references/docs/` so runs, reports and the AI check work offline and show exactly
which version of the requirement they were checked against.
"""

from __future__ import annotations

import base64
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

from qalprit.recorder_paths import reference_docs_dir

_JIRA_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]+-\d+$")
_JIRA_BROWSE_RE = re.compile(r"/browse/([A-Z][A-Z0-9_]+-\d+)")
_CONFLUENCE_PAGE_RE = re.compile(r"/pages/(\d+)|[?&]pageId=(\d+)")
# Jira fields worth sending along with the summary and description.
_USEFUL_FIELD_RE = re.compile(r"acceptance|criteria|steps|expected|scenario|test", re.IGNORECASE)
_MAX_DOC_CHARS = 40_000

Fetcher = Callable[[str, dict[str, str]], Any]


@dataclass
class Reference:
    kind: str  # jira | confluence
    key: str  # issue key or page id
    url: str = ""

    @property
    def doc_id(self) -> str:
        return self.key if self.kind == "jira" else f"confluence-{self.key}"


@dataclass
class ReferenceDoc:
    ref: str
    kind: str
    key: str
    title: str = ""
    url: str = ""
    text: str = ""
    fetched_at: str = ""
    error: str = ""
    cached: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref, "kind": self.kind, "key": self.key, "title": self.title, "url": self.url,
            "text": self.text, "fetched_at": self.fetched_at, "error": self.error, "cached": self.cached,
        }


@dataclass
class AtlassianConfig:
    jira_base: str = ""
    confluence_base: str = ""
    email: str = ""
    token: str = ""
    allowed_hosts: set[str] = field(default_factory=set)
    jira_email: str = ""
    jira_token: str = ""
    confluence_email: str = ""
    confluence_token: str = ""

    @classmethod
    def from_env(cls, env: dict[str, str]) -> "AtlassianConfig":
        def val(key: str) -> str:
            return (env.get(key) or "").strip()

        base = val("ATLASSIAN_BASE_URL").rstrip("/")
        jira = (val("JIRA_BASE_URL") or base).rstrip("/")
        conf = (val("CONFLUENCE_BASE_URL") or (f"{base}/wiki" if base else "")).rstrip("/")
        hosts = {urllib.parse.urlparse(u).hostname or "" for u in (jira, conf) if u}
        return cls(
            jira_base=jira,
            confluence_base=conf,
            email=val("ATLASSIAN_EMAIL"),
            token=val("ATLASSIAN_API_TOKEN"),
            allowed_hosts={h.lower() for h in hosts if h},
            jira_email=val("JIRA_EMAIL"),
            jira_token=val("JIRA_API_TOKEN"),
            confluence_email=val("CONFLUENCE_EMAIL"),
            confluence_token=val("CONFLUENCE_API_TOKEN"),
        )

    def credentials(self, kind: str) -> tuple[str, str]:
        """(email, token) for `jira` or `confluence`. A product's own token brings its own email."""
        if kind == "jira" and self.jira_token:
            return self.jira_email, self.jira_token
        if kind == "confluence" and self.confluence_token:
            return self.confluence_email, self.confluence_token
        return self.email, self.token

    def configured_for(self, kind: str) -> bool:
        base = self.jira_base if kind == "jira" else self.confluence_base
        return bool(base and self.credentials(kind)[1])

    @property
    def configured(self) -> bool:
        return self.configured_for("jira") or self.configured_for("confluence")

    def auth_header(self, kind: str = "") -> str:
        email, token = self.credentials(kind)
        if email:
            raw = f"{email}:{token}".encode("utf-8")
            return "Basic " + base64.b64encode(raw).decode("ascii")
        return f"Bearer {token}"


def parse_reference(raw: str) -> Reference | None:
    """Understand a Jira key, a Jira or Confluence link, or `confluence:<page id>`."""
    text = str(raw or "").strip()
    if not text:
        return None
    if _JIRA_KEY_RE.match(text):
        return Reference("jira", text)
    if text.lower().startswith("confluence:") and text.split(":", 1)[1].strip().isdigit():
        return Reference("confluence", text.split(":", 1)[1].strip())
    if text.lower().startswith(("http://", "https://")):
        m = _JIRA_BROWSE_RE.search(text)
        if m:
            return Reference("jira", m.group(1), text)
        m = _CONFLUENCE_PAGE_RE.search(text)
        if m:
            return Reference("confluence", m.group(1) or m.group(2), text)
    return None


class _HtmlToText(HTMLParser):
    """Confluence storage format to readable text: headings, list items and table rows on their own lines."""

    _BLOCK = {"p", "div", "br", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol", "pre"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip += 1
        elif tag == "li":
            self.out.append("\n- ")
        elif tag in {"td", "th"}:
            self.out.append(" | ")
        elif tag in self._BLOCK:
            self.out.append("\n")
            if tag.startswith("h") and len(tag) == 2:
                self.out.append("#" * int(tag[1]) + " ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1
        elif tag in self._BLOCK:
            self.out.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.out.append(data)

    def text(self) -> str:
        joined = "".join(self.out)
        lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in joined.splitlines()]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def html_to_text(html: str) -> str:
    parser = _HtmlToText()
    parser.feed(html or "")
    parser.close()
    return parser.text()


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _http_get_json(url: str, headers: dict[str, str]) -> Any:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20, context=_ssl_context()) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        hint = {
            401: "Check the email and token in Settings & setup > Jira and Confluence.",
            403: "Your Atlassian account cannot open it.",
            404: "It does not exist, or your account cannot see it.",
        }.get(exc.code, "")
        raise RuntimeError(f"Atlassian returned {exc.code}. {hint}".strip()) from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach Atlassian: {exc.reason}") from None


class AtlassianClient:
    def __init__(self, config: AtlassianConfig, fetch: Fetcher | None = None) -> None:
        self.config = config
        self._fetch = fetch or _http_get_json

    def _get(self, url: str, kind: str = "") -> Any:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
        if host not in self.config.allowed_hosts:
            raise RuntimeError(f"{host or url} is not the Atlassian site you set in Settings & setup, so the token is not sent there.")
        return self._fetch(url, {"Authorization": self.config.auth_header(kind), "Accept": "application/json"})

    def fetch_jira(self, key: str) -> tuple[str, str, str]:
        base = self.config.jira_base
        if not base:
            raise RuntimeError("Add your Jira site in Settings & setup to fetch Jira issues.")
        if not self.config.credentials("jira")[1]:
            raise RuntimeError("Add a Jira token in Settings & setup to fetch Jira issues.")
        data = self._get(f"{base}/rest/api/2/issue/{urllib.parse.quote(key)}?expand=names", "jira")
        fields = data.get("fields") or {}
        names = data.get("names") or {}
        title = f"{key}: {fields.get('summary') or ''}".strip()
        parts = [f"# {title}"]
        for label, val in (("Type", (fields.get("issuetype") or {}).get("name")), ("Status", (fields.get("status") or {}).get("name"))):
            if val:
                parts.append(f"{label}: {val}")
        if fields.get("description"):
            parts.append("## Description\n" + str(fields["description"]).strip())
        for fid, val in fields.items():
            name = str(names.get(fid) or "")
            if fid.startswith("customfield_") and isinstance(val, str) and val.strip() and _USEFUL_FIELD_RE.search(name):
                parts.append(f"## {name}\n{val.strip()}")
        return title, "\n\n".join(parts), f"{base}/browse/{key}"

    def fetch_confluence(self, page_id: str) -> tuple[str, str, str]:
        base = self.config.confluence_base
        if not base:
            raise RuntimeError("Add your Confluence site in Settings & setup to fetch Confluence pages.")
        if not self.config.credentials("confluence")[1]:
            raise RuntimeError("Add a Confluence token in Settings & setup to fetch Confluence pages.")
        data = self._get(f"{base}/rest/api/content/{urllib.parse.quote(page_id)}?expand=body.storage,version", "confluence")
        title = str(data.get("title") or f"Page {page_id}")
        html = ((data.get("body") or {}).get("storage") or {}).get("value") or ""
        link = ((data.get("_links") or {}).get("webui") or "")
        url = f"{base}{link}" if link else f"{base}/pages/viewpage.action?pageId={page_id}"
        return title, f"# {title}\n\n{html_to_text(html)}", url


def _doc_path(project_root: Path, ref: Reference) -> Path:
    return reference_docs_dir(project_root) / f"{ref.doc_id}.json"


def load_cached_doc(project_root: Path, raw: str) -> ReferenceDoc | None:
    ref = parse_reference(raw)
    if ref is None:
        return None
    path = _doc_path(project_root, ref)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return ReferenceDoc(
        ref=raw, kind=ref.kind, key=ref.key, title=str(data.get("title") or ""), url=str(data.get("url") or ""),
        text=str(data.get("text") or ""), fetched_at=str(data.get("fetched_at") or ""), cached=True,
    )


def fetch_reference_docs(
    project_root: Path,
    refs: list[str],
    env: dict[str, str],
    *,
    refresh: bool = False,
    client: AtlassianClient | None = None,
) -> list[ReferenceDoc]:
    """Return each reference's text, from the local copy unless `refresh` (or there is none)."""
    docs: list[ReferenceDoc] = []
    api = client or AtlassianClient(AtlassianConfig.from_env(env))
    for raw in refs:
        ref = parse_reference(raw)
        if ref is None:
            docs.append(ReferenceDoc(ref=raw, kind="", key="", error="Not a Jira issue key or a Jira/Confluence link."))
            continue
        cached = None if refresh else load_cached_doc(project_root, raw)
        if cached is not None:
            docs.append(cached)
            continue
        if not api.config.configured_for(ref.kind):
            stale = load_cached_doc(project_root, raw)
            docs.append(stale or ReferenceDoc(
                ref=raw, kind=ref.kind, key=ref.key,
                error=f"Add your {'Jira' if ref.kind == 'jira' else 'Confluence'} site and token in Settings & setup to fetch it.",
            ))
            continue
        try:
            if ref.kind == "jira":
                title, text, url = api.fetch_jira(ref.key)
            else:
                title, text, url = api.fetch_confluence(ref.key)
        except Exception as exc:
            stale = load_cached_doc(project_root, raw)
            doc = stale or ReferenceDoc(ref=raw, kind=ref.kind, key=ref.key)
            doc.error = str(exc)
            docs.append(doc)
            continue
        if len(text) > _MAX_DOC_CHARS:
            text = text[:_MAX_DOC_CHARS] + "\n\n[Shortened: the page is longer than QAlprit keeps.]"
        doc = ReferenceDoc(
            ref=raw, kind=ref.kind, key=ref.key, title=title, url=ref.url or url, text=text,
            fetched_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        path = _doc_path(project_root, ref)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({k: v for k, v in doc.as_dict().items() if k not in {"error", "cached"}}, indent=2), encoding="utf-8")
        docs.append(doc)
    return docs


# ---- Settings & setup: read, save and test the connection ------------------------------------

_SITE_KEYS = {
    "jira_site": "JIRA_BASE_URL",
    "confluence_site": "CONFLUENCE_BASE_URL",
}
_FIELD_KEYS = {
    "site": "ATLASSIAN_BASE_URL",
    "email": "ATLASSIAN_EMAIL",
    "token": "ATLASSIAN_API_TOKEN",
    "jira_email": "JIRA_EMAIL",
    "jira_token": "JIRA_API_TOKEN",
    "confluence_email": "CONFLUENCE_EMAIL",
    "confluence_token": "CONFLUENCE_API_TOKEN",
    **_SITE_KEYS,
}
_TOKEN_FIELDS = ("token", "jira_token", "confluence_token")


def _clean_site(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    if not text.lower().startswith(("http://", "https://")):
        text = "https://" + text
    parsed = urllib.parse.urlparse(text)
    if not parsed.hostname:
        raise ValueError(f"'{raw}' is not a web address. Use something like https://acme.atlassian.net.")
    return text.rstrip("/")


def read_connection(env: dict[str, str]) -> dict[str, Any]:
    """The saved connection for the UI. Tokens are never returned, only whether each one is set."""
    def val(key: str) -> str:
        return (env.get(key) or "").strip()

    out: dict[str, Any] = {name: val(key) for name, key in _FIELD_KEYS.items() if name not in _TOKEN_FIELDS}
    for name in _TOKEN_FIELDS:
        out[f"{name}_set"] = bool(val(_FIELD_KEYS[name]))
    cfg = AtlassianConfig.from_env(env)
    out["jira_ready"] = cfg.configured_for("jira")
    out["confluence_ready"] = cfg.configured_for("confluence")
    return out


def save_connection(project_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Write the connection to the workspace `.env`.

    A token field that is missing or blank keeps the saved token. Send `clear_<field>: true` to remove it.
    """
    from qalprit.env_loader import parse_env_file, update_env_file

    env_path = project_root / ".env"
    updates: dict[str, str | None] = {}
    for name, key in _FIELD_KEYS.items():
        if name in _TOKEN_FIELDS:
            if payload.get(f"clear_{name}"):
                updates[key] = None
            elif str(payload.get(name) or "").strip():
                updates[key] = str(payload[name]).strip()
            continue
        if name not in payload:
            continue
        raw = str(payload.get(name) or "").strip()
        if name in ("site", *_SITE_KEYS):
            raw = _clean_site(raw)
        updates[key] = raw or None
    if updates:
        update_env_file(env_path, updates)
    return read_connection(parse_env_file(env_path))


def check_connection(project_root: Path, payload: dict[str, Any], fetch: Fetcher | None = None) -> dict[str, Any]:
    """Try the saved connection (plus anything typed but not saved yet) against Jira and Confluence."""
    from qalprit.env_loader import parse_env_file

    env = dict(parse_env_file(project_root / ".env"))
    for name, key in _FIELD_KEYS.items():
        if name in payload and (name not in _TOKEN_FIELDS or str(payload.get(name) or "").strip()):
            raw = str(payload.get(name) or "").strip()
            env[key] = _clean_site(raw) if name in ("site", *_SITE_KEYS) else raw
    cfg = AtlassianConfig.from_env(env)
    client = AtlassianClient(cfg, fetch=fetch)
    results: dict[str, Any] = {}
    checks = (
        ("jira", cfg.jira_base, "/rest/api/2/myself", "Jira"),
        ("confluence", cfg.confluence_base, "/rest/api/user/current", "Confluence"),
    )
    for kind, base, path, label in checks:
        if not base and not cfg.credentials(kind)[1]:
            results[kind] = {"ok": None, "message": f"{label} is not set up."}
            continue
        if not base:
            results[kind] = {"ok": False, "message": f"Add your {label} site address."}
            continue
        if not cfg.credentials(kind)[1]:
            results[kind] = {"ok": False, "message": f"Add a {label} token."}
            continue
        try:
            data = client._get(f"{base}{path}", kind)
            who = ""
            if isinstance(data, dict):
                who = str(data.get("displayName") or data.get("publicName") or data.get("name") or "")
            results[kind] = {"ok": True, "message": f"Connected to {label}" + (f" as {who}." if who else ".")}
        except Exception as exc:
            results[kind] = {"ok": False, "message": str(exc)}
    return {"ok": all(r["ok"] is not False for r in results.values()) and any(r["ok"] for r in results.values()), "results": results}
