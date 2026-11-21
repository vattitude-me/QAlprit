"""Fetch the Jira issues and Confluence pages a test points to, and keep a local copy.

A test lists them under `references:` in its front matter, for example::

    references:
      - SHOP-142
      - https://acme.atlassian.net/wiki/spaces/QA/pages/123456/Checkout+flow

Credentials come from the workspace `.env` (never from the spec):

    ATLASSIAN_BASE_URL=https://acme.atlassian.net
    ATLASSIAN_EMAIL=you@acme.com        # Atlassian Cloud; leave empty for a Data Center token
    ATLASSIAN_API_TOKEN=...

`JIRA_BASE_URL` and `CONFLUENCE_BASE_URL` override the base for sites where the two live on
different hosts. The token is only ever sent to those configured hosts. Fetched text is saved
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

    @classmethod
    def from_env(cls, env: dict[str, str]) -> "AtlassianConfig":
        base = (env.get("ATLASSIAN_BASE_URL") or "").strip().rstrip("/")
        jira = (env.get("JIRA_BASE_URL") or base).strip().rstrip("/")
        conf = (env.get("CONFLUENCE_BASE_URL") or (f"{base}/wiki" if base else "")).strip().rstrip("/")
        hosts = {urllib.parse.urlparse(u).hostname or "" for u in (jira, conf) if u}
        return cls(
            jira_base=jira,
            confluence_base=conf,
            email=(env.get("ATLASSIAN_EMAIL") or "").strip(),
            token=(env.get("ATLASSIAN_API_TOKEN") or "").strip(),
            allowed_hosts={h.lower() for h in hosts if h},
        )

    @property
    def configured(self) -> bool:
        return bool(self.token and (self.jira_base or self.confluence_base))

    def auth_header(self) -> str:
        if self.email:
            raw = f"{self.email}:{self.token}".encode("utf-8")
            return "Basic " + base64.b64encode(raw).decode("ascii")
        return f"Bearer {self.token}"


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
            401: "Check ATLASSIAN_EMAIL and ATLASSIAN_API_TOKEN in .env.",
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

    def _get(self, url: str) -> Any:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
        if host not in self.config.allowed_hosts:
            raise RuntimeError(f"{host or url} is not the Atlassian site set in ATLASSIAN_BASE_URL, so the token is not sent there.")
        return self._fetch(url, {"Authorization": self.config.auth_header(), "Accept": "application/json"})

    def fetch_jira(self, key: str) -> tuple[str, str, str]:
        base = self.config.jira_base
        if not base:
            raise RuntimeError("Set ATLASSIAN_BASE_URL (or JIRA_BASE_URL) in .env to fetch Jira issues.")
        data = self._get(f"{base}/rest/api/2/issue/{urllib.parse.quote(key)}?expand=names")
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
            raise RuntimeError("Set ATLASSIAN_BASE_URL (or CONFLUENCE_BASE_URL) in .env to fetch Confluence pages.")
        data = self._get(f"{base}/rest/api/content/{urllib.parse.quote(page_id)}?expand=body.storage,version")
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
        if not api.config.configured:
            stale = load_cached_doc(project_root, raw)
            docs.append(stale or ReferenceDoc(
                ref=raw, kind=ref.kind, key=ref.key,
                error="Add ATLASSIAN_BASE_URL, ATLASSIAN_EMAIL and ATLASSIAN_API_TOKEN to the workspace .env to fetch it.",
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
