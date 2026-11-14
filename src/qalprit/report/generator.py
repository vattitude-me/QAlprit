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


def _build_step_view(
    step: Any,
    run_dir: Path,
    external_assets: bool,
) -> dict[str, Any]:
    data = step.model_dump() if hasattr(step, "model_dump") else dict(step)
    data["duration_fmt"] = _format_duration(int(data.get("duration_ms", 0)))
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
