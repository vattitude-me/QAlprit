"""Tests for AppiumDriverAdapter keyboard fallback and iOS simulator options."""

from __future__ import annotations

from types import SimpleNamespace

from qalprit.config_loader import DeviceProfile, EnvironmentConfig
from qalprit.devices import DiscoveredDevice
from qalprit.driver_factory import AppiumDriverAdapter, create_appium_driver


class _FakeElement:
    def __init__(self, displayed: bool = True) -> None:
        self._displayed = displayed
        self.clicked = 0

    def is_displayed(self) -> bool:
        return self._displayed

    def click(self) -> None:
        self.clicked += 1


class _FakeRawDriver:
    def __init__(
        self,
        *,
        hide_raises: bool = False,
        keyboards: list[_FakeElement] | None = None,
        text_fields: list[_FakeElement] | None = None,
    ) -> None:
        self.hide_raises = hide_raises
        self.hide_calls = 0
        self._keyboards = keyboards or []
        self._text_fields = text_fields or []

    def hide_keyboard(self) -> None:
        self.hide_calls += 1
        if self.hide_raises:
            raise RuntimeError("No 'Done' button")

    def find_elements(self, _by: str, value: str) -> list[_FakeElement]:
        if value == "XCUIElementTypeKeyboard":
            return self._keyboards
        if value == "XCUIElementTypeTextField":
            return self._text_fields
        return []


def test_hide_keyboard_ios_falls_back_to_visible_textfield() -> None:
    hidden_field = _FakeElement(displayed=False)
    visible_field = _FakeElement(displayed=True)
    raw = _FakeRawDriver(
        hide_raises=True,
        keyboards=[_FakeElement(displayed=True)],
        text_fields=[hidden_field, visible_field],
    )
    adapter = AppiumDriverAdapter(raw, platform="ios")

    adapter.hide_keyboard()

    assert raw.hide_calls == 1
    assert hidden_field.clicked == 0
    assert visible_field.clicked == 1


def test_hide_keyboard_android_ignores_ios_fallback() -> None:
    visible_field = _FakeElement(displayed=True)
    raw = _FakeRawDriver(
        hide_raises=True,
        keyboards=[_FakeElement(displayed=True)],
        text_fields=[visible_field],
    )
    adapter = AppiumDriverAdapter(raw, platform="android")

    adapter.hide_keyboard()

    assert raw.hide_calls == 1
    assert visible_field.clicked == 0


def test_hide_keyboard_ios_continues_when_silent_no_op() -> None:
    kb = _FakeElement(displayed=True)

    class _SilentRawDriver(_FakeRawDriver):
        def __init__(self) -> None:
            super().__init__(hide_raises=False, keyboards=[kb], text_fields=[_FakeElement(displayed=True)])
            self.scripts: list[tuple[str, object]] = []

        def is_keyboard_shown(self) -> bool:
            return kb.is_displayed()

        def execute_script(self, script: str, arg: object = None) -> None:
            self.scripts.append((script, arg))
            if script == "mobile: hideKeyboard":
                kb._displayed = False

    raw = _SilentRawDriver()
    adapter = AppiumDriverAdapter(raw, platform="ios")

    adapter.hide_keyboard()

    assert raw.hide_calls == 1
    assert any(s == "mobile: hideKeyboard" for s, _ in raw.scripts)
    assert raw.is_keyboard_shown() is False


def test_create_appium_driver_sets_ios_simulator_keyboard_options(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_remote(url: str, *, options: object) -> SimpleNamespace:
        captured["url"] = url
        captured["options"] = options
        return SimpleNamespace()

    monkeypatch.setattr("appium.webdriver.Remote", _fake_remote)

    profile = DeviceProfile(
        name="ios_simulator",
        platform="ios",
        target_type="simulator",
        device_name="iPhone 17",
        platform_version="26.5",
        automation_name="XCUITest",
    )
    env_cfg = EnvironmentConfig(name="mda", ios_bundle_id="com.saucelabs.mydemo.app.ios")

    adapter = create_appium_driver("http://127.0.0.1:4723", profile, env_cfg)

    assert adapter.platform == "ios"
    caps = captured["options"].to_capabilities()
    assert caps["appium:connectHardwareKeyboard"] is True
    assert caps["appium:forceSimulatorSoftwareKeyboardPresence"] is False


def test_create_appium_driver_skips_unavailable_avd_when_emulator_is_booted(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "appium.webdriver.Remote",
        lambda url, *, options: captured.update({"url": url, "options": options}) or SimpleNamespace(),
    )
    monkeypatch.setattr(
        "qalprit.driver_factory.list_android_devices",
        lambda: (
            [
                DiscoveredDevice("android", "emulator-5554", "emulator-5554", "device", "emulator"),
                DiscoveredDevice("android", "mbtest", "avd:mbtest", "available (AVD)", "emulator"),
            ],
            None,
        ),
    )

    profile = DeviceProfile(
        name="android_emulator",
        platform="android",
        target_type="simulator",
        device_name="Android Emulator",
        avd="Pixel_8_API_35",
        automation_name="UiAutomator2",
    )
    env_cfg = EnvironmentConfig(name="mda", android_package="com.saucelabs.mydemoapp.android")

    create_appium_driver("http://127.0.0.1:4723", profile, env_cfg)

    caps = captured["options"].to_capabilities()
    assert "appium:avd" not in caps


def test_create_appium_driver_autopicks_first_avd_when_none_booted(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "appium.webdriver.Remote",
        lambda url, *, options: captured.update({"url": url, "options": options}) or SimpleNamespace(),
    )
    monkeypatch.setattr(
        "qalprit.driver_factory.list_android_devices",
        lambda: (
            [DiscoveredDevice("android", "mbtest", "avd:mbtest", "available (AVD)", "emulator")],
            None,
        ),
    )

    profile = DeviceProfile(
        name="android_emulator",
        platform="android",
        target_type="simulator",
        device_name="Android Emulator",
        automation_name="UiAutomator2",
    )
    env_cfg = EnvironmentConfig(name="mda", android_package="com.saucelabs.mydemoapp.android")

    create_appium_driver("http://127.0.0.1:4723", profile, env_cfg)

    caps = captured["options"].to_capabilities()
    assert caps["appium:avd"] == "mbtest"


def test_create_appium_driver_enables_headless_when_xcode_lacks_simulator_app(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "appium.webdriver.Remote",
        lambda url, *, options: captured.update({"url": url, "options": options}) or SimpleNamespace(),
    )
    monkeypatch.setattr("qalprit.driver_factory._xcode_has_legacy_simulator_app", lambda: False)

    profile = DeviceProfile(
        name="ios_simulator",
        platform="ios",
        target_type="simulator",
        device_name="iPhone 17 Pro",
        platform_version="26.5",
        automation_name="XCUITest",
    )
    env_cfg = EnvironmentConfig(name="mda", ios_bundle_id="com.saucelabs.mydemo.app.ios")

    create_appium_driver("http://127.0.0.1:4723", profile, env_cfg)
    caps = captured["options"].to_capabilities()
    assert caps["appium:isHeadless"] is True

    # Explicit user capability overrides auto-detection cleanly
    override_profile = DeviceProfile(
        name="ios_simulator",
        platform="ios",
        target_type="simulator",
        device_name="iPhone 17 Pro",
        platform_version="26.5",
        automation_name="XCUITest",
        capabilities={"appium:isHeadless": False},
    )
    create_appium_driver("http://127.0.0.1:4723", override_profile, env_cfg)
    override_caps = captured["options"].to_capabilities()
    assert override_caps["appium:isHeadless"] is False


def test_xcode_has_legacy_simulator_app_checks_developer_dir(tmp_path, monkeypatch) -> None:
    from qalprit.driver_factory import _xcode_has_legacy_simulator_app

    dev_dir = tmp_path / "Xcode.app" / "Contents" / "Developer"
    dev_dir.mkdir(parents=True)
    monkeypatch.setattr("qalprit.driver_factory.platform.system", lambda: "Darwin")
    monkeypatch.setenv("DEVELOPER_DIR", str(dev_dir))
    assert _xcode_has_legacy_simulator_app() is False

    (dev_dir / "Applications" / "Simulator.app").mkdir(parents=True)
    assert _xcode_has_legacy_simulator_app() is True


def test_create_appium_driver_rejects_device_ipa_and_apk_for_ios_simulator(tmp_path, monkeypatch) -> None:
    import plistlib
    import zipfile
    import pytest

    monkeypatch.setattr("appium.webdriver.Remote", lambda url, *, options: SimpleNamespace())

    profile = DeviceProfile(
        name="ios_simulator",
        platform="ios",
        target_type="simulator",
        device_name="iPhone 17 Pro",
        automation_name="XCUITest",
    )
    env_cfg = EnvironmentConfig(name="mda", ios_bundle_id="com.saucelabs.mydemo.app.ios")

    ipa = tmp_path / "DeviceBuild.ipa"
    with zipfile.ZipFile(ipa, "w") as zf:
        zf.writestr(
            "Payload/DeviceBuild.app/Info.plist",
            plistlib.dumps({"CFBundleIdentifier": "com.example.dev", "DTPlatformName": "iphoneos", "CFBundleSupportedPlatforms": ["iPhoneOS"]}),
        )

    with pytest.raises(ValueError, match="physical-device iOS build"):
        create_appium_driver("http://127.0.0.1:4723", profile, env_cfg, app_path=ipa)

    apk = tmp_path / "android.apk"
    apk.write_bytes(b"PK\x03\x04")
    with pytest.raises(ValueError, match="Android .apk"):
        create_appium_driver("http://127.0.0.1:4723", profile, env_cfg, app_path=apk)

    sim_zip = tmp_path / "SimulatorBuild.app.zip"
    with zipfile.ZipFile(sim_zip, "w") as zf:
        zf.writestr(
            "My Demo App.app/Info.plist",
            plistlib.dumps({"CFBundleIdentifier": "com.example.sim", "DTPlatformName": "iphonesimulator", "CFBundleSupportedPlatforms": ["iPhoneSimulator"]}),
        )
    adapter = create_appium_driver("http://127.0.0.1:4723", profile, env_cfg, app_path=sim_zip)
    assert adapter.platform == "ios"
