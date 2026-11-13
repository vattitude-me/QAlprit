"""Tests for qalprit inspector and CLI commands."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from qalprit.cli import main
from qalprit.inspector import format_inspect_output


def test_inspector_extracts_elements_and_flags_webview() -> None:
    xml = """
    <hierarchy>
      <android.webkit.WebView bounds="[0,0][1080,2400]">
        <android.widget.Button text="Sign in" content-desc="sign_in_btn" clickable="true" bounds="[100,200][300,280]" />
        <android.widget.EditText resource-id="com.app:id/email" text="" clickable="true" bounds="[100,300][900,380]" />
      </android.webkit.WebView>
    </hierarchy>
    """
    out = format_inspect_output(xml, screen_name="login")
    assert "WebView detected" in out
    assert "sign_in_btn" in out
    assert "com.app:id/email" in out
    assert "login:" in out


def test_cli_validate_and_inspect_from_xml(tmp_path: Path) -> None:
    runner = CliRunner()
    xml_path = tmp_path / "dump.xml"
    xml_path.write_text(
        '<hierarchy><android.widget.TextView text="QAlprit Demo" bounds="[0,0][200,50]" /></hierarchy>',
        encoding="utf-8",
    )
    res_inspect = runner.invoke(
        main,
        ["inspect", "--platform", "android", "--from-xml", str(xml_path), "--screen", "catalog"],
    )
    assert res_inspect.exit_code == 0
    assert "QAlprit Demo" in res_inspect.output

    res_doc = runner.invoke(main, ["doctor", "--json-output"])
    assert "Python" in res_doc.output
