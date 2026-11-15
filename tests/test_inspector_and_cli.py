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


def test_inspector_human_readable_names_hints_and_sibling_labels() -> None:
    from qalprit.inspector import extract_interactive_elements

    xml = """
    <hierarchy>
      <android.widget.LinearLayout bounds="[0,0][1080,2400]">
        <android.widget.TextView text="Customer Email" bounds="[60,200][500,260]" />
        <android.widget.EditText resource-id="com.bank:id/et_01" text="" clickable="true" bounds="[60,270][1000,390]" />
        <android.widget.EditText resource-id="com.bank:id/et_02" hint="Enter PIN" password="true" clickable="true" bounds="[60,420][1000,540]" />
        <android.widget.Button resource-id="com.bank:id/btn1" text="Continue" clickable="true" bounds="[60,580][1000,700]" />
        <android.widget.Button resource-id="com.bank:id/btn2" text="Continue" clickable="true" bounds="[60,740][1000,860]" />
      </android.widget.LinearLayout>
    </hierarchy>
    """
    items = extract_interactive_elements(xml, platform="android")
    by_val = {el["recommended_value"]: el for el in items}

    email_el = by_val["com.bank:id/et_01"]
    assert email_el["key"] == "customer_email_input"
    assert "Customer Email" in email_el["display_label"]
    assert email_el["rect"] == [60, 270, 1000, 390]

    pin_el = by_val["com.bank:id/et_02"]
    assert pin_el["key"] == "enter_pin_input"
    assert pin_el["suggested_sensitive"] is True

    btn1 = by_val["com.bank:id/btn1"]
    btn2 = by_val["com.bank:id/btn2"]
    assert btn1["key"] == "continue_btn"
    assert btn2["key"] == "continue_btn_2"
