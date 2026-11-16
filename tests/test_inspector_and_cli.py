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


def test_inspector_detects_ios_clickable_buttons_and_skips_app_shell() -> None:
    from qalprit.inspector import extract_interactive_elements

    ios_xml = """
    <AppiumAUT>
      <XCUIElementTypeApplication name="My Demo App" label="My Demo App" x="0" y="0" width="402" height="874">
        <XCUIElementTypeWindow x="0" y="0" width="402" height="874">
          <XCUIElementTypeButton name="BackButton Icons" label="Products" x="12" y="64" width="90" height="32" enabled="true" visible="true" />
          <XCUIElementTypeStaticText name="Sauce Labs Backpack - Black" label="Sauce Labs Backpack - Black" x="20" y="130" width="320" height="28" enabled="true" visible="true" />
          <XCUIElementTypeSecureTextField name="Password" placeholderValue="Enter password" x="20" y="250" width="362" height="44" enabled="true" visible="true" />
          <XCUIElementTypeButton name="Add To Cart" label="Add To Cart" x="60" y="750" width="282" height="50" enabled="true" visible="true" />
        </XCUIElementTypeWindow>
      </XCUIElementTypeApplication>
    </AppiumAUT>
    """
    items = extract_interactive_elements(ios_xml, platform="ios")
    tags = {item["tag"] for item in items}
    assert "XCUIElementTypeApplication" not in tags
    assert "XCUIElementTypeWindow" not in tags

    by_val = {item["recommended_value"]: item for item in items}
    assert by_val["BackButton Icons"]["clickable"] is True
    assert by_val["BackButton Icons"]["role"] == "button"
    assert by_val["Add To Cart"]["clickable"] is True
    assert by_val["Add To Cart"]["role"] == "button"
    assert by_val["Sauce Labs Backpack - Black"]["clickable"] is False
    assert by_val["Sauce Labs Backpack - Black"]["role"] == "label"
    assert by_val["Password"]["editable"] is True
    assert by_val["Password"]["suggested_sensitive"] is True
