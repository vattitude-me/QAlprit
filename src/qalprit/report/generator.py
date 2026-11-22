"""HTML and Markdown report generator using Jinja2 and Pillow compression."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from jinja2 import Environment, FileSystemLoader, select_autoescape

from qalprit.env_loader import SecretMasker
from qalprit.evidence import compress_image_for_html
from qalprit.results import RunResult

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _format_duration(ms: int) -> str:
    if ms < 1000:
        return f"{ms}ms"
    secs = ms / 1000.0
    if secs < 60:
        return f"{secs:.2f}s"
    mins = int(secs // 60)
    rem = secs % 60
    return f"{mins}m {rem:.1f}s"


def _safe_resolve_within(base_dir: Path, rel_path: str | None) -> Path | None:
    """Resolve rel_path safely within base_dir, rejecting absolute paths and traversal."""
    if not rel_path or not isinstance(rel_path, str):
        return None
    raw_path = Path(rel_path)
    if raw_path.is_absolute() or ".." in raw_path.parts:
        return None
    try:
        base_resolved = base_dir.resolve()
        candidate = base_dir / raw_path
        if candidate.is_symlink():
            target_resolved = candidate.resolve()
            if not target_resolved.is_relative_to(base_resolved):
                return None
        resolved = candidate.resolve()
        if not resolved.is_relative_to(base_resolved):
            return None
        return resolved
    except Exception:
        return None


def _escape_md_cell(text: str, masker: SecretMasker | None = None) -> str:
    """Escape pipe delimiters and newlines for GitHub-Flavored Markdown table cells."""
    val = text or ""
    if masker is not None:
        val = masker.mask(val)
    return val.replace("\r\n", " ").replace("\r", " ").replace("\n", " ").replace("|", r"\|").strip()


def _image_src(run_dir: Path, rel: str | None, external_assets: bool) -> str | None:
    """Inline (or link) an image that lives inside the run folder; None when it is missing."""
    if not rel:
        return None
    path = _safe_resolve_within(run_dir, rel)
    if path is None or not path.exists():
        return None
    if external_assets:
        return Path(rel).as_posix()
    try:
        mime, b64 = compress_image_for_html(path)
    except Exception:
        return None
    return f"data:{mime};base64,{b64}"


_MANUAL_LABELS = {
    "done": "Done by a person",
    "auto": "Done by a person, run carried on by itself",
    "skip": "Skipped by a person",
    "fail": "Marked failed by a person",
    "preset": "Value from dataset or .env",
    "none": "Nobody answered",
}


def _manual_label(diag: dict[str, Any]) -> str:
    """Badge text for a step a person finished by hand, so nobody reads it as automated."""
    if not isinstance(diag, dict) or not diag.get("manual"):
        return ""
    label = _MANUAL_LABELS.get(str(diag.get("answer") or ""), "Needs a person")
    waited = diag.get("waited_sec")
    if isinstance(waited, (int, float)) and waited > 0:
        label += f" after {waited:.0f}s"
    return label


def _build_step_view(
    step: Any,
    run_dir: Path,
    external_assets: bool,
) -> dict[str, Any]:
    data = step.model_dump() if hasattr(step, "model_dump") else dict(step)
    data["duration_fmt"] = _format_duration(int(data.get("duration_ms", 0)))
    data["manual_label"] = _manual_label(data.get("diagnostics") or {})
    shot_rel = data.get("screenshot_path")
    data["image_src"] = None
    data["image_error"] = ""
    if shot_rel:
        shot_path = _safe_resolve_within(run_dir, shot_rel)
        if shot_path is None:
            data["image_error"] = "Blocked invalid or outside-run screenshot path"
        elif shot_path.exists():
            if external_assets:
                data["image_src"] = Path(shot_rel).as_posix()
            else:
                try:
                    mime, b64 = compress_image_for_html(shot_path)
                    data["image_src"] = f"data:{mime};base64,{b64}"
                except Exception as exc:
                    data["image_error"] = f"Unreadable screenshot image: {exc}"

    diff_rel = data.get("visual_diff_path")
    data["visual_diff_src"] = None
    if diff_rel:
        diff_path = _safe_resolve_within(run_dir, diff_rel)
        if diff_path is not None and diff_path.exists():
            if external_assets:
                data["visual_diff_src"] = Path(diff_rel).as_posix()
            else:
                try:
                    mime, b64 = compress_image_for_html(diff_path)
                    data["visual_diff_src"] = f"data:{mime};base64,{b64}"
                except Exception:
                    data["visual_diff_src"] = None

    data["reference_src"] = _image_src(run_dir, data.get("reference_screenshot_path"), external_assets)

    ps_rel = data.get("page_source_path")
    data["page_source_preview"] = None
    if ps_rel:
        ps_path = _safe_resolve_within(run_dir, ps_rel)
        if ps_path is not None and ps_path.exists():
            data["page_source_preview"] = ps_path.read_text(encoding="utf-8", errors="replace")[:8000]
    return data


def generate_summary_markdown(
    run_result: RunResult,
    masker: SecretMasker | None = None,
) -> str:
    """Build a concise Markdown summary for chat / PR comments."""
    s = run_result.summary
    pass_rate = (s.passed / s.total * 100.0) if s.total else 0.0
    interrupted_note = " | **INTERRUPTED**" if run_result.interrupted else ""
    lines: list[str] = [
        f"# QAlprit Test Report — `{run_result.run_id}`",
        "",
        f"- **Status:** {s.passed}/{s.total} passed ({pass_rate:.0f}%) | {s.failed} failed | {s.skipped} skipped | {s.flaky} flaky{interrupted_note}",
        f"- **Environment / Platform:** `{run_result.environment}` / `{run_result.platform}` ({run_result.device_name}, `{run_result.target_type}`)",
        f"- **Suite:** `{run_result.suite}` | **Duration:** {_format_duration(s.duration_ms)}",
        f"- **Started:** {run_result.started_at} | **Tool:** qalprit v{run_result.tool_version}",
    ]
    if s.failure_categories:
        cats = ", ".join(f"`{k}`: {v}" for k, v in sorted(s.failure_categories.items()))
        lines.append(f"- **Failure Categories:** {cats}")
    if s.redaction_warnings > 0:
        lines.append(
            f"- **Redaction Warnings:** {s.redaction_warnings} step(s) skipped or warned during evidence redaction"
        )
    if s.webview_tests > 0:
        lines.append(
            f"- **WebView Notice (C8):** {s.webview_tests} test(s) encountered WebView elements"
        )
    lines.extend(
        [
            "",
            "| ID | Title | Status | Category | Duration | Notes |",
            "|---|---|---|---|---|---|",
        ]
    )
    for t in run_result.tests:
        notes: list[str] = []
        if t.flaky:
            notes.append("flaky")
        if t.webview_detected:
            notes.append("WebView detected")
        if t.redaction_warnings > 0:
            notes.append(f"{t.redaction_warnings} redaction warning(s)")
        if t.skip_reason:
            notes.append(t.skip_reason)
        if t.failure_reason:
            notes.append(t.failure_reason[:120])
        badge = t.status.upper()
        cat = t.failure_category or "-"
        safe_id = _escape_md_cell(t.id, masker=masker)
        safe_title = _escape_md_cell(t.title, masker=masker)
        safe_cat = _escape_md_cell(cat, masker=masker)
        safe_notes = _escape_md_cell("; ".join(notes) or "-", masker=masker)
        lines.append(
            f"| `{safe_id}` | {safe_title} | **{badge}** | `{safe_cat}` | {_format_duration(t.duration_ms)} | {safe_notes} |"
        )
    lines.append("")
    return "\n".join(lines)


def generate_junit_xml(
    run_result: RunResult,
    output_path: Path,
    masker: SecretMasker | None = None,
) -> Path:
    """Write standard JUnit XML report for CI tools (GitHub Actions, Jenkins, GitLab)."""
    def _mask(val: str) -> str:
        return masker.mask(val) if masker is not None else val

    s = run_result.summary
    testsuites = ET.Element(
        "testsuites",
        attrib={
            "name": f"qalprit-{run_result.suite}",
            "tests": str(s.total),
            "failures": str(s.failed),
            "skipped": str(s.skipped),
            "time": f"{s.duration_ms / 1000.0:.3f}",
        },
    )
    testsuite = ET.SubElement(
        testsuites,
        "testsuite",
        attrib={
            "name": f"{run_result.suite}.{run_result.platform}",
            "tests": str(s.total),
            "failures": str(s.failed),
            "skipped": str(s.skipped),
            "time": f"{s.duration_ms / 1000.0:.3f}",
            "timestamp": run_result.started_at,
        },
    )
    props = ET.SubElement(testsuite, "properties")
    for prop_name, prop_val in (
        ("run_id", run_result.run_id),
        ("environment", run_result.environment),
        ("platform", run_result.platform),
        ("device_name", run_result.device_name),
        ("target_type", run_result.target_type),
        ("os_version", run_result.os_version),
        ("app_version", run_result.app_version),
        ("interrupted", str(run_result.interrupted).lower()),
    ):
        if prop_val:
            ET.SubElement(props, "property", attrib={"name": prop_name, "value": _mask(str(prop_val))})

    for t in run_result.tests:
        tc = ET.SubElement(
            testsuite,
            "testcase",
            attrib={
                "classname": f"qalprit.{run_result.suite}.{run_result.platform}",
                "name": _mask(f"{t.id}: {t.title}"),
                "time": f"{t.duration_ms / 1000.0:.3f}",
            },
        )
        if t.status == "failed":
            msg = _mask((t.failure_reason or "Test failed").replace("\n", " "))
            detail_lines = [
                f"Category: {t.failure_category or 'unknown'}",
                f"Exception: {t.exception_type or 'Error'}",
            ]
            if t.failed_step_index is not None:
                detail_lines.append(f"Failed Step: #{t.failed_step_index} ({t.failed_step_action})")
            detail_lines.append(f"Details: {_mask(t.failure_reason or '')}")
            failure_el = ET.SubElement(
                tc,
                "failure",
                attrib={
                    "type": t.failure_category or t.exception_type or "AssertionError",
                    "message": msg,
                },
            )
            failure_el.text = "\n".join(detail_lines)
        elif t.status == "skipped":
            ET.SubElement(
                tc,
                "skipped",
                attrib={"message": _mask(t.skip_reason or "Skipped")},
            )

    tree = ET.ElementTree(testsuites)
    ET.indent(tree, space="  ")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    xml_bytes = ET.tostring(testsuites, encoding="unicode", xml_declaration=True)
    if masker is not None:
        xml_bytes = masker.mask(xml_bytes)
    output_path.write_text(xml_bytes, encoding="utf-8")
    return output_path


def generate_reports(
    run_result: RunResult,
    run_dir: Path,
    external_assets: bool = False,
    masker: SecretMasker | None = None,
    home_url: str | None = None,
) -> tuple[Path, Path]:
    """Render report.html, summary.md, and junit.xml into run_dir."""
    run_dir.mkdir(parents=True, exist_ok=True)

    all_tags: list[str] = sorted({tag for t in run_result.tests for tag in t.tags})
    all_categories: list[str] = sorted(
        {t.failure_category for t in run_result.tests if t.failure_category}
    )
    test_views: list[dict[str, Any]] = []
    for t in run_result.tests:
        td = t.model_dump()
        td["duration_fmt"] = _format_duration(t.duration_ms)
        setup_steps = [
            _build_step_view(s, run_dir, external_assets)
            for s in t.steps
            if s.phase == "setup"
        ]
        main_steps = [
            _build_step_view(s, run_dir, external_assets)
            for s in t.steps
            if s.phase == "steps"
        ]
        teardown_steps = [
            _build_step_view(s, run_dir, external_assets)
            for s in t.steps
            if s.phase == "teardown"
        ]
        td["setup_steps"] = setup_steps
        td["main_steps"] = main_steps
        td["teardown_steps"] = teardown_steps
        td["attempt_count"] = len(t.attempts)
        attempt_views: list[dict[str, Any]] = []
        for att in t.attempts:
            att_d = att.model_dump()
            att_d["duration_fmt"] = _format_duration(att.duration_ms)
            att_d["steps"] = [_build_step_view(s, run_dir, external_assets) for s in att.steps]
            attempt_views.append(att_d)
        td["attempt_views"] = attempt_views
        test_views.append(td)

    s = run_result.summary
    pass_rate = f"{(s.passed / s.total * 100.0):.0f}%" if s.total else "0%"

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template("report.html.j2")
    html_text = template.render(
        run=run_result,
        summary=s,
        pass_rate=pass_rate,
        total_duration_fmt=_format_duration(s.duration_ms),
        tests=test_views,
        all_tags=all_tags,
        all_categories=all_categories,
        external_assets=external_assets,
        home_url=home_url,
    )
    md_text = generate_summary_markdown(run_result, masker=masker)

    if masker is not None:
        html_text = masker.mask(html_text)
        md_text = masker.mask(md_text)

    html_path = run_dir / "report.html"
    md_path = run_dir / "summary.md"
    junit_path = run_dir / "junit.xml"
    html_path.write_text(html_text, encoding="utf-8")
    md_path.write_text(md_text, encoding="utf-8")
    generate_junit_xml(run_result, junit_path, masker=masker)
    return html_path, md_path


def generate_batch_report(
    batch_payload: dict[str, Any],
    batch_dir: Path,
    masker: SecretMasker | None = None,
) -> Path:
    """Write batch.json and a concise standalone batch_report.html linking dataset reports."""
    import html as html_lib
    import json

    batch_dir.mkdir(parents=True, exist_ok=True)
    safe_payload = masker.mask_obj(batch_payload) if masker is not None else batch_payload
    (batch_dir / "batch.json").write_text(json.dumps(safe_payload, indent=2), encoding="utf-8")

    batch_id = html_lib.escape(str(safe_payload.get("batch_id") or batch_dir.name))
    target_label = html_lib.escape(str(safe_payload.get("target_label") or "Dataset Batch"))
    env_name = html_lib.escape(str(safe_payload.get("environment") or ""))
    platform_name = html_lib.escape(str(safe_payload.get("platform") or ""))
    summary = safe_payload.get("summary") or {}
    total = int(summary.get("total") or 0)
    passed = int(summary.get("passed") or 0)
    failed = int(summary.get("failed") or 0)
    dur = _format_duration(int(summary.get("duration_ms") or 0))

    rows_html: list[str] = []
    for item in safe_payload.get("runs") or []:
        ds = html_lib.escape(str(item.get("dataset_name") or "-"))
        exp = html_lib.escape(str(item.get("dataset_expected") or "-"))
        st = str(item.get("status") or "unknown")
        st_cls = "ok" if st == "passed" else ("fail" if st == "failed" else "skip")
        rd = html_lib.escape(str(item.get("dir_name") or ""))
        item_dur = _format_duration(int(item.get("duration_ms") or 0))
        err = html_lib.escape(str(item.get("error") or ""))
        rows_html.append(
            f"<tr>"
            f"<td><strong>{ds}</strong></td>"
            f"<td>{exp}</td>"
            f"<td><span class='pill {st_cls}'>{html_lib.escape(st.title())}</span></td>"
            f"<td>{item_dur}</td>"
            f"<td>{err or '-'}</td>"
            f"<td>"
            f"<a href='../{rd}/report.html'>HTML</a> &middot; "
            f"<a href='../{rd}/summary.md'>MD</a> &middot; "
            f"<a href='../{rd}/junit.xml'>JUnit</a>"
            f"</td>"
            f"</tr>"
        )

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="color-scheme" content="dark light" />
<title>QAlprit Batch Report — {batch_id}</title>
<style>
  :root {{ --bg:#0b0e14; --card:#151b24; --border:#242e3d; --text:#eef2f7; --muted:#8b97a8; --accent:#2dd4bf; --ok:#34d399; --fail:#f87171; }}
  body {{ margin:0; font-family:system-ui,-apple-system,sans-serif; background:var(--bg); color:var(--text); padding:28px; }}
  .wrap {{ max-width:1080px; margin:0 auto; }}
  h1 {{ margin:0 0 6px; font-size:1.4rem; }}
  .meta {{ color:var(--muted); font-size:0.9rem; margin-bottom:20px; }}
  .kpis {{ display:flex; gap:12px; flex-wrap:wrap; margin-bottom:20px; }}
  .kpi {{ background:var(--card); border:1px solid var(--border); border-radius:10px; padding:12px 16px; min-width:130px; }}
  .kpi small {{ color:var(--muted); display:block; font-size:0.75rem; }}
  .kpi strong {{ font-size:1.25rem; }}
  table {{ width:100%; border-collapse:collapse; background:var(--card); border:1px solid var(--border); border-radius:10px; overflow:hidden; }}
  th, td {{ text-align:left; padding:10px 14px; border-bottom:1px solid var(--border); font-size:0.88rem; }}
  th {{ color:var(--muted); font-size:0.78rem; text-transform:uppercase; letter-spacing:0.04em; }}
  .pill {{ padding:2px 8px; border-radius:999px; font-size:0.75rem; font-weight:600; }}
  .pill.ok {{ background:rgba(52,211,153,0.16); color:var(--ok); }}
  .pill.fail {{ background:rgba(248,113,113,0.16); color:var(--fail); }}
  .pill.skip {{ background:rgba(139,151,168,0.16); color:var(--muted); }}
  a {{ color:var(--accent); text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
  footer {{ margin-top:24px; color:var(--muted); font-size:0.8rem; display:flex; justify-content:space-between; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Batch Report · {target_label}</h1>
  <div class="meta">Batch <code>{batch_id}</code> &middot; {platform_name} / {env_name}</div>
  <div class="kpis">
    <div class="kpi"><small>Datasets</small><strong>{total}</strong></div>
    <div class="kpi"><small>Passed</small><strong style="color:var(--ok)">{passed}</strong></div>
    <div class="kpi"><small>Failed</small><strong style="color:var(--fail)">{failed}</strong></div>
    <div class="kpi"><small>Duration</small><strong>{dur}</strong></div>
  </div>
  <table>
    <thead><tr><th>Dataset</th><th>Expected</th><th>Status</th><th>Time</th><th>Error</th><th>Artifacts</th></tr></thead>
    <tbody>{''.join(rows_html)}</tbody>
  </table>
  <footer>
    <span>QAlprit Batch Report</span>
    <span>Managed by <a href="https://vattitude.ca" target="_blank" rel="noopener">vattitude.ca</a></span>
  </footer>
</div>
</body>
</html>"""
    out = batch_dir / "batch_report.html"
    out.write_text(doc, encoding="utf-8")
    return out
