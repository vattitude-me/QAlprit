"""HTML and Markdown report generator using Jinja2 and Pillow compression."""

from __future__ import annotations

from pathlib import Path
from typing import Any

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


def _build_step_view(
    step: Any,
    run_dir: Path,
    external_assets: bool,
) -> dict[str, Any]:
    data = step.model_dump() if hasattr(step, "model_dump") else dict(step)
    data["duration_fmt"] = _format_duration(int(data.get("duration_ms", 0)))
    shot_rel = data.get("screenshot_path")
    data["image_src"] = None
    if shot_rel:
        shot_path = run_dir / shot_rel
        if shot_path.exists():
            if external_assets:
                data["image_src"] = shot_rel
            else:
                mime, b64 = compress_image_for_html(shot_path)
                data["image_src"] = f"data:{mime};base64,{b64}"

    ps_rel = data.get("page_source_path")
    data["page_source_preview"] = None
    if ps_rel:
        ps_path = run_dir / ps_rel
        if ps_path.exists():
            data["page_source_preview"] = ps_path.read_text(encoding="utf-8", errors="replace")[:8000]
    return data


def generate_summary_markdown(run_result: RunResult) -> str:
    """Build a concise Markdown summary for chat / PR comments."""
    s = run_result.summary
    pass_rate = (s.passed / s.total * 100.0) if s.total else 0.0
    lines: list[str] = [
        f"# QAlprit Test Report — `{run_result.run_id}`",
        "",
        f"- **Status:** {s.passed}/{s.total} passed ({pass_rate:.0f}%) | {s.failed} failed | {s.skipped} skipped | {s.flaky} flaky",
        f"- **Environment / Platform:** `{run_result.environment}` / `{run_result.platform}` ({run_result.device_name}, `{run_result.target_type}`)",
        f"- **Suite:** `{run_result.suite}` | **Duration:** {_format_duration(s.duration_ms)}",
        f"- **Started:** {run_result.started_at} | **Tool:** qalprit v{run_result.tool_version}",
    ]
    if s.webview_tests > 0:
        lines.append(
            f"- **WebView Notice (C8):** {s.webview_tests} test(s) encountered WebView elements"
        )
    lines.extend(
        [
            "",
            "| ID | Title | Status | Duration | Notes |",
            "|---|---|---|---|---|",
        ]
    )
    for t in run_result.tests:
        notes: list[str] = []
        if t.flaky:
            notes.append("flaky")
        if t.webview_detected:
            notes.append("WebView detected")
        if t.skip_reason:
            notes.append(t.skip_reason)
        if t.failure_reason:
            notes.append(t.failure_reason.replace("\n", " ")[:120])
        badge = t.status.upper()
        lines.append(
            f"| `{t.id}` | {t.title} | **{badge}** | {_format_duration(t.duration_ms)} | {'; '.join(notes) or '-'} |"
        )
    lines.append("")
    return "\n".join(lines)


def generate_reports(
    run_result: RunResult,
    run_dir: Path,
    external_assets: bool = False,
    masker: SecretMasker | None = None,
) -> tuple[Path, Path]:
    """Render report.html and summary.md into run_dir."""
    run_dir.mkdir(parents=True, exist_ok=True)

    all_tags: list[str] = sorted({tag for t in run_result.tests for tag in t.tags})
    test_views: list[dict[str, Any]] = []
    for t in run_result.tests:
        td = t.model_dump()
        td["duration_fmt"] = _format_duration(t.duration_ms)
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
        td["main_steps"] = main_steps
        td["teardown_steps"] = teardown_steps
        td["attempt_count"] = len(t.attempts)
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
        external_assets=external_assets,
    )
    md_text = generate_summary_markdown(run_result)

    if masker is not None:
        html_text = masker.mask(html_text)
        md_text = masker.mask(md_text)

    html_path = run_dir / "report.html"
    md_path = run_dir / "summary.md"
    html_path.write_text(html_text, encoding="utf-8")
    md_path.write_text(md_text, encoding="utf-8")
    return html_path, md_path
