"""QAlprit CLI: doctor, devices, inspect, validate, run, report, ui."""

from __future__ import annotations

import json
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

import click

from qalprit import __version__
from qalprit.appium_manager import AppiumManager
from qalprit.devices import list_android_devices, list_ios_simulators
from qalprit.doctor import run_doctor
from qalprit.driver_factory import create_appium_driver
from qalprit.inspector import format_inspect_output
from qalprit.report.generator import generate_reports
from qalprit.results import load_results_json, write_results_json
from qalprit.runner import TestRunner
from qalprit.ui_server import create_ui_server
from qalprit.validator import validate_project


def _resolve_root(project_root: Path | None) -> Path:
    return (project_root or Path.cwd()).resolve()


@click.group()
@click.version_option(version=__version__, prog_name="qalprit")
def main() -> None:
    """QAlprit — Mobile QA. Bugs caught with proof."""


@main.command()
@click.option("--json-output", is_flag=True, help="Print diagnostics as JSON.")
def doctor(json_output: bool) -> None:
    """Check Node, Appium 2, drivers, Java, Android SDK, Xcode (macOS), and devices."""
    checks = run_doctor()
    if json_output:
        click.echo(json.dumps([c.__dict__ for c in checks], indent=2))
    else:
        click.echo(f"QAlprit Doctor (v{__version__})")
        click.echo("=" * 60)
        for item in checks:
            tag = {
                "ok": "[ OK ]",
                "warn": "[WARN]",
                "fail": "[FAIL]",
                "skip": "[SKIP]",
            }[item.status]
            click.echo(f"{tag} {item.name:<28} {item.detail}")
            if item.remediation:
                click.echo(f"       -> {item.remediation}")

    if any(c.status == "fail" for c in checks):
        sys.exit(1)


@main.command()
def devices() -> None:
    """List connected Android devices/emulators and available iOS simulators."""
    android_devs, android_err = list_android_devices()
    ios_devs, ios_err = list_ios_simulators()

    click.echo("Android Devices / AVDs:")
    if android_devs:
        for d in android_devs:
            click.echo(f"  - {d.name} ({d.kind}) [{d.state}] id={d.identifier}")
    else:
        click.echo(f"  None ({android_err or 'no connected devices or AVDs'})")

    click.echo("\niOS Simulators:")
    if ios_devs:
        for d in ios_devs[:20]:
            click.echo(f"  - {d.name} [{d.state}] udid={d.identifier}")
        if len(ios_devs) > 20:
            click.echo(f"  ... and {len(ios_devs) - 20} more")
    else:
        click.echo(f"  None ({ios_err or 'no simulators found'})")


@main.command()
@click.option("--platform", type=click.Choice(["android", "ios"]), required=True)
@click.option("--device", "device_profile", required=False, help="Device profile name in config/devices.yaml.")
@click.option("--app", "app_path", type=click.Path(path_type=Path), default=None, help="Path to .apk or .app.")
@click.option("--env", "env_name", default="qa", show_default=True, help="Environment name in environments.yaml.")
@click.option("--screen", "screen_name", default="screen", show_default=True, help="Screen key for suggested YAML.")
@click.option("--wait", "wait_seconds", type=float, default=3.0, show_default=True, help="Seconds to wait for screen settle before dumping hierarchy.")
@click.option("--appium-url", default=None, help="Existing Appium 2 server URL.")
@click.option("--from-xml", type=click.Path(exists=True, path_type=Path), default=None, help="Inspect a saved XML page source file without a device.")
@click.option("--project-root", type=click.Path(path_type=Path), default=None, hidden=True)
def inspect(
    platform: str,
    device_profile: str | None,
    app_path: Path | None,
    env_name: str,
    screen_name: str,
    wait_seconds: float,
    appium_url: str | None,
    from_xml: Path | None,
    project_root: Path | None,
) -> None:
    """Dump current screen accessibility IDs, text, bounds, and suggested screens.yaml."""
    if from_xml is not None:
        xml_text = from_xml.read_text(encoding="utf-8")
        click.echo(format_inspect_output(xml_text, screen_name=screen_name))
        return

    if not device_profile:
        raise click.UsageError("--device is required when inspecting a live device/simulator.")

    root = _resolve_root(project_root)
    val = validate_project(root, env_name=env_name)
    if device_profile not in val.devices:
        raise click.ClickException(
            f"Unknown device profile '{device_profile}'. Available: {', '.join(sorted(val.devices))}"
        )
    profile = val.devices[device_profile]
    if profile.platform != platform:
        raise click.ClickException(
            f"Device profile '{device_profile}' is for '{profile.platform}', not '{platform}'."
        )
    if env_name not in val.environments:
        raise click.ClickException(f"Unknown environment '{env_name}'.")
    env_cfg = val.environments[env_name]

    log_path = root / "results" / "inspect_appium.log"
    with AppiumManager(log_path=log_path, appium_url=appium_url) as mgr:
        driver = create_appium_driver(mgr.url, profile, env_cfg, app_path=app_path)
        try:
            if wait_seconds > 0:
                import time

                time.sleep(wait_seconds)
            xml_text = driver.page_source
            click.echo(format_inspect_output(xml_text, screen_name=screen_name))
        finally:
            driver.quit()


@main.command()
@click.option("--suite", default=None, help="Filter specs by suite directory/name.")
@click.option("--env", "env_name", default=None, help="Environment name in config/environments.yaml.")
@click.option("--platform", type=click.Choice(["android", "ios"]), default=None)
@click.option("--tag", "tags", multiple=True, help="Filter specs by tag (repeatable).")
@click.option("--project-root", type=click.Path(path_type=Path), default=None, hidden=True)
def validate(
    suite: str | None,
    env_name: str | None,
    platform: str | None,
    tags: tuple[str, ...],
    project_root: Path | None,
) -> None:
    """Validate specs, flows, locators, and environment variables (no device needed)."""
    root = _resolve_root(project_root)
    res = validate_project(
        root,
        env_name=env_name,
        suite=suite,
        tags=list(tags) if tags else None,
        platform=platform,
    )
    for issue in res.issues:
        click.echo(issue.format(), err=(issue.level == "error"))

    if not res.ok:
        click.echo(
            f"Validation FAILED: {len(res.errors)} error(s), {len(res.warnings)} warning(s).",
            err=True,
        )
        sys.exit(1)

    click.echo(
        f"Validation PASSED: {len(res.specs)} spec(s), {len(res.flows)} flow(s), "
        f"{len(res.warnings)} warning(s)."
    )


@main.command()
@click.option("--platform", type=click.Choice(["android", "ios"]), required=True)
@click.option("--device", "device_profile", required=True, help="Profile name in config/devices.yaml.")
@click.option("--app", "app_path", type=click.Path(exists=True, path_type=Path), required=True, help="Path to .apk or .app/.ipa.")
@click.option("--env", "env_name", default="qa", show_default=True)
@click.option("--suite", default=None, help="Filter specs by suite.")
@click.option("--tags", default=None, help="Comma-separated tag filter (e.g. smoke,login).")
@click.option("--retries", type=int, default=0, show_default=True)
@click.option("--appium-url", default=None, help="Attach to existing Appium 2 server instead of spawning.")
@click.option("--external-assets", is_flag=True, help="Link screenshots externally in report.html instead of embedding.")
@click.option("--project-root", type=click.Path(path_type=Path), default=None, hidden=True)
def run(
    platform: str,
    device_profile: str,
    app_path: Path,
    env_name: str,
    suite: str | None,
    tags: str | None,
    retries: int,
    appium_url: str | None,
    external_assets: bool,
    project_root: Path | None,
) -> None:
    """Execute automated UI tests and generate report.html, summary.md, and results.json."""
    root = _resolve_root(project_root)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None

    val = validate_project(
        root,
        env_name=env_name,
        suite=suite,
        tags=tag_list,
        platform=platform,
    )
    for issue in val.issues:
        click.echo(issue.format(), err=(issue.level == "error"))
    if not val.ok:
        raise click.ClickException(
            f"Validation failed with {len(val.errors)} error(s); aborting run."
        )
    if not val.specs:
        raise click.ClickException("No matching specs found to execute.")
    if device_profile not in val.devices:
        raise click.ClickException(
            f"Unknown device profile '{device_profile}'. Available: {', '.join(sorted(val.devices))}"
        )
    profile = val.devices[device_profile]
    if profile.platform != platform:
        raise click.ClickException(
            f"Device profile '{device_profile}' is for '{profile.platform}', not '{platform}'."
        )
    env_cfg = val.environments[env_name]

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + f"_{platform}_{env_name}"
    run_dir = root / "results" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    appium_log_path = run_dir / "appium.log"

    click.echo(f"Starting QAlprit run {run_id} ({len(val.specs)} spec(s))...")
    with AppiumManager(log_path=appium_log_path, appium_url=appium_url) as mgr:
        runner = TestRunner(
            driver_factory=lambda auto_accept: create_appium_driver(
                mgr.url,
                profile,
                env_cfg,
                app_path=app_path,
                auto_accept_alerts=auto_accept,
            ),
            profile=profile,
            profile_name=device_profile,
            env_name=env_name,
            env_cfg=env_cfg,
            env_ctx=val.env_ctx,  # type: ignore[arg-type]
            screens=val.screens,  # type: ignore[arg-type]
            flows=val.flows,
            run_dir=run_dir,
            run_id=run_id,
            app_path=app_path,
            suite=suite or "all",
            tags=tag_list or [],
            retries=retries,
        )
        run_result = runner.run(
            val.specs,
            appium_log_rel="appium.log" if appium_log_path.exists() else None,
        )

    results_json_path = write_results_json(
        run_result,
        run_dir / "results.json",
        masker=val.env_ctx.masker if val.env_ctx else None,
    )
    html_path, md_path = generate_reports(
        run_result,
        run_dir,
        external_assets=external_assets,
        masker=val.env_ctx.masker if val.env_ctx else None,
    )

    s = run_result.summary
    click.echo(
        f"Run complete: {s.passed} passed, {s.failed} failed, {s.skipped} skipped, {s.flaky} flaky"
    )
    if s.webview_tests > 0:
        click.echo(
            f"[WARNING] WebView detected in {s.webview_tests} test(s) (see report for C8 details)."
        )
    click.echo(f"Results JSON: {results_json_path}")
    click.echo(f"Report HTML : {html_path}")
    click.echo(f"Summary MD  : {md_path}")

    if s.failed > 0:
        sys.exit(1)


@main.command()
@click.argument("results_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--external-assets", is_flag=True, help="Link PNG files externally instead of embedding.")
def report(results_dir: Path, external_assets: bool) -> None:
    """(Re)generate report.html and summary.md from results/<run-id>/results.json."""
    json_path = results_dir / "results.json"
    if not json_path.exists():
        raise click.ClickException(f"results.json not found in {results_dir}")
    run_result = load_results_json(json_path)
    html_path, md_path = generate_reports(
        run_result,
        results_dir,
        external_assets=external_assets,
    )
    click.echo(f"Generated: {html_path}")
    click.echo(f"Generated: {md_path}")


@main.command()
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind host.")
@click.option("--port", type=int, default=8765, show_default=True, help="Port to serve the local workbench UI.")
@click.option("--results-dir", type=click.Path(path_type=Path), default=None, help="Directory containing run results.")
@click.option("--no-open", is_flag=True, help="Do not automatically open the browser.")
@click.option("--project-root", type=click.Path(path_type=Path), default=None, hidden=True)
def ui(
    host: str,
    port: int,
    results_dir: Path | None,
    no_open: bool,
    project_root: Path | None,
) -> None:
    """Launch the local QAlprit Workbench UI to browse runs, specs, and locators."""
    root = _resolve_root(project_root)
    server = create_ui_server(root, results_dir=results_dir, host=host, port=port)
    bound_host, bound_port = server.server_address[:2]
    url = f"http://{bound_host}:{bound_port}"
    click.echo(f"QAlprit Workbench running at {url} (Ctrl+C to stop)")
    if not no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        click.echo("\nShutting down QAlprit Workbench.")
    finally:
        server.server_close()
