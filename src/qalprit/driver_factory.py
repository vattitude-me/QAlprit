"""Appium driver factory and normalized driver protocol for real + fake drivers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from qalprit.config_loader import DeviceProfile, EnvironmentConfig
from qalprit.devices import list_android_devices


@runtime_checkable
class ElementProtocol(Protocol):
    @property
    def text(self) -> str: ...

    @property
    def rect(self) -> dict[str, int | float]: ...

    def click(self) -> None: ...

    def send_keys(self, value: str) -> None: ...

    def clear(self) -> None: ...

    def is_displayed(self) -> bool: ...


@runtime_checkable
class DriverProtocol(Protocol):
    @property
    def page_source(self) -> str: ...

    @property
    def capabilities(self) -> dict[str, Any]: ...

    def find_element(self, strategy: str, value: str) -> ElementProtocol: ...

    def find_elements(self, strategy: str, value: str) -> list[ElementProtocol]: ...

    def get_screenshot_as_png(self) -> bytes: ...

    def get_window_size(self) -> dict[str, int]: ...

    def activate_app(self, app_id: str) -> None: ...

    def terminate_app(self, app_id: str) -> None: ...

    def remove_app(self, app_id: str) -> None: ...

    def install_app(self, app_path: str) -> None: ...

    def background_app(self, seconds: int | float) -> None: ...

    def back(self) -> None: ...

    def hide_keyboard(self) -> None: ...

    def swipe(self, start_x: int, start_y: int, end_x: int, end_y: int, duration: int = 300) -> None: ...

    def execute_script(self, script: str, *args: Any) -> Any: ...

    def quit(self) -> None: ...


class AppiumDriverAdapter:
    """Adapts Appium-Python-Client Remote WebDriver to qalprit's DriverProtocol."""

    def __init__(self, raw_driver: Any, platform: str) -> None:
        self._driver = raw_driver
        self.platform = platform

    @property
    def page_source(self) -> str:
        return self._driver.page_source

    @property
    def capabilities(self) -> dict[str, Any]:
        return dict(getattr(self._driver, "capabilities", {}) or {})

    def _to_by_and_val(self, strategy: str, value: str) -> tuple[str, str]:
        from appium.webdriver.common.appiumby import AppiumBy

        if strategy == "accessibility_id":
            return AppiumBy.ACCESSIBILITY_ID, value
        if strategy == "id":
            return AppiumBy.ID, value
        if strategy == "xpath":
            return AppiumBy.XPATH, value
        if strategy == "class_name":
            return AppiumBy.CLASS_NAME, value
        if strategy == "ios_predicate":
            return AppiumBy.IOS_PREDICATE, value
        if strategy == "android_uiautomator":
            return AppiumBy.ANDROID_UIAUTOMATOR, value
        if strategy == "text":
            if self.platform == "android":
                escaped = value.replace('"', '\\"')
                return (
                    AppiumBy.ANDROID_UIAUTOMATOR,
                    f'new UiSelector().text("{escaped}")',
                )
            escaped = value.replace("'", "\\'")
            return (
                AppiumBy.IOS_PREDICATE,
                f"label == '{escaped}' OR name == '{escaped}' OR value == '{escaped}'",
            )
        raise ValueError(f"Unsupported locator strategy: {strategy}")

    def find_element(self, strategy: str, value: str) -> Any:
        by, loc = self._to_by_and_val(strategy, value)
        return self._driver.find_element(by, loc)

    def find_elements(self, strategy: str, value: str) -> list[Any]:
        by, loc = self._to_by_and_val(strategy, value)
        return list(self._driver.find_elements(by, loc))

    def get_screenshot_as_png(self) -> bytes:
        return self._driver.get_screenshot_as_png()

    def get_window_size(self) -> dict[str, int]:
        return dict(self._driver.get_window_size())

    def activate_app(self, app_id: str) -> None:
        self._driver.activate_app(app_id)

    def terminate_app(self, app_id: str) -> None:
        self._driver.terminate_app(app_id)

    def remove_app(self, app_id: str) -> None:
        self._driver.remove_app(app_id)

    def install_app(self, app_path: str) -> None:
        self._driver.install_app(app_path)

    def background_app(self, seconds: int | float) -> None:
        self._driver.background_app(int(seconds))

    def back(self) -> None:
        self._driver.back()

    def hide_keyboard(self) -> None:
        try:
            self._driver.hide_keyboard()
            return
        except Exception:
            pass

        # On iOS Simulator with AutomaticMinimizationEnabled=true, tapping a text field
        # minimizes the software keyboard when WDA's hideKeyboard finds no 'Done' button.
        if self.platform == "ios":
            try:
                from appium.webdriver.common.appiumby import AppiumBy

                if self._driver.find_elements(AppiumBy.CLASS_NAME, "XCUIElementTypeKeyboard"):
                    for field in self._driver.find_elements(
                        AppiumBy.CLASS_NAME, "XCUIElementTypeTextField"
                    ):
                        try:
                            if field.is_displayed():
                                field.click()
                                return
                        except Exception:
                            continue
            except Exception:
                pass

    def swipe(self, start_x: int, start_y: int, end_x: int, end_y: int, duration: int = 300) -> None:
        self._driver.swipe(start_x, start_y, end_x, end_y, duration)

    def execute_script(self, script: str, *args: Any) -> Any:
        return self._driver.execute_script(script, *args)

    def quit(self) -> None:
        self._driver.quit()


def _resolve_android_avd(profile: DeviceProfile) -> str | None:
    """Resolve Android AVD name without blocking on a stale name when an emulator is already running."""
    if profile.target_type != "simulator" or profile.udid:
        return profile.avd
    devices, _ = list_android_devices()
    online_emulators = [d for d in devices if d.kind == "emulator" and d.state == "device"]
    available_avds = [d.name for d in devices if d.identifier.startswith("avd:")]
    if profile.avd:
        if profile.avd in available_avds or not online_emulators:
            return profile.avd
        return None
    if not online_emulators and available_avds:
        return available_avds[0]
    return None


def create_appium_driver(
    appium_url: str,
    profile: DeviceProfile,
    env_cfg: EnvironmentConfig,
    app_path: Path | None = None,
    auto_accept_alerts: bool | None = None,
) -> AppiumDriverAdapter:
    """Create a real Appium 2 Remote session configured for Android or iOS."""
    from appium import webdriver
    from appium.options.android import UiAutomator2Options
    from appium.options.ios import XCUITestOptions

    accept_alerts = (
        profile.auto_accept_alerts if auto_accept_alerts is None else auto_accept_alerts
    )

    if profile.platform == "android":
        opts = UiAutomator2Options()
        opts.platform_name = "Android"
        opts.automation_name = profile.automation_name or "UiAutomator2"
        opts.device_name = profile.device_name
        if profile.platform_version:
            opts.platform_version = profile.platform_version
        if profile.udid:
            opts.udid = profile.udid
        resolved_avd = _resolve_android_avd(profile)
        if resolved_avd:
            opts.avd = resolved_avd
            opts.set_capability("appium:avdLaunchTimeout", 120000)
            opts.set_capability("appium:avdReadyTimeout", 120000)
        if app_path is not None:
            opts.app = str(app_path.resolve())
        if env_cfg.android_package:
            opts.app_package = env_cfg.android_package
        opts.auto_grant_permissions = accept_alerts
        opts.no_reset = True
        opts.new_command_timeout = 180
        for k, v in profile.capabilities.items():
            opts.set_capability(k, v)
    else:
        opts = XCUITestOptions()
        opts.platform_name = "iOS"
        opts.automation_name = profile.automation_name or "XCUITest"
        opts.device_name = profile.device_name
        if profile.platform_version:
            opts.platform_version = profile.platform_version
        if profile.udid:
            opts.udid = profile.udid
        if app_path is not None:
            opts.app = str(app_path.resolve())
        if env_cfg.ios_bundle_id:
            opts.bundle_id = env_cfg.ios_bundle_id
        opts.auto_accept_alerts = accept_alerts
        opts.no_reset = True
        opts.new_command_timeout = 180
        if profile.target_type == "simulator":
            opts.connect_hardware_keyboard = True
            opts.force_simulator_software_keyboard_presence = False
        for k, v in profile.capabilities.items():
            opts.set_capability(k, v)

    raw_driver = webdriver.Remote(appium_url, options=opts)
    return AppiumDriverAdapter(raw_driver, platform=profile.platform)
