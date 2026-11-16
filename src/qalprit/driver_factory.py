"""Appium driver factory and normalized driver protocol for real + fake drivers."""

from __future__ import annotations

import platform
import plistlib
import re
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from qalprit.config_loader import DeviceProfile, EnvironmentConfig
from qalprit.devices import _macos_xcode_env, list_android_devices, list_ios_simulators
from qalprit.doctor import find_xcode_developer_dir


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

    def is_enabled(self) -> bool: ...


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
                escaped = value.replace("\\", "\\\\").replace('"', '\\"')
                return (
                    AppiumBy.ANDROID_UIAUTOMATOR,
                    f'new UiSelector().text("{escaped}")',
                )
            escaped = value.replace("\\", "\\\\").replace("'", "\\'")
            return (
                AppiumBy.IOS_PREDICATE,
                f"label == '{escaped}' OR name == '{escaped}' OR value == '{escaped}'",
            )
        raise ValueError(f"Unsupported locator strategy: {strategy}")

    def is_keyboard_shown(self) -> bool:
        try:
            shown = self._driver.is_keyboard_shown()
            if shown is not None:
                return bool(shown)
        except Exception:
            pass
        if self.platform == "ios":
            try:
                from appium.webdriver.common.appiumby import AppiumBy

                return any(
                    el.is_displayed()
                    for el in self._driver.find_elements(AppiumBy.CLASS_NAME, "XCUIElementTypeKeyboard")
                )
            except Exception:
                return False
        return False

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

        if self.platform != "ios":
            return

        # On iOS Simulator, try explicit keyboard dismissal keys first, then AutomaticMinimizationEnabled
        # (tapping the visible text field), then WDA mobile: hideKeyboard / safe top chrome tap.
        try:
            from appium.webdriver.common.appiumby import AppiumBy

            if not self._driver.find_elements(AppiumBy.CLASS_NAME, "XCUIElementTypeKeyboard"):
                return
            for key_name in ("Hide keyboard", "Done", "Return", "Go", "Search", "Next", "done", "return"):
                for b in self._driver.find_elements(AppiumBy.ACCESSIBILITY_ID, key_name):
                    try:
                        if b.is_displayed():
                            b.click()
                            return
                    except Exception:
                        continue
            for field in self._driver.find_elements(AppiumBy.CLASS_NAME, "XCUIElementTypeTextField"):
                try:
                    if field.is_displayed():
                        field.click()
                        return
                except Exception:
                    continue
        except Exception:
            pass

        try:
            self._driver.execute_script(
                "mobile: hideKeyboard",
                {"keys": ["Hide keyboard", "Done", "Return", "Go", "Search"]},
            )
            return
        except Exception:
            pass

        try:
            win = self.get_window_size()
            w = int(win.get("width") or 390)
            h = int(win.get("height") or 844)
            self._driver.execute_script("mobile: tap", {"x": w // 2, "y": max(64, int(h * 0.14))})
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


def _xcode_has_legacy_simulator_app() -> bool:
    """Return True on macOS if Xcode includes Developer/Applications/Simulator.app (pre-Xcode 27)."""
    if platform.system() != "Darwin":
        return True
    dev_dir = find_xcode_developer_dir()
    if dev_dir is None:
        return True
    return (dev_dir / "Applications" / "Simulator.app").exists()


def _read_ios_info_plist(app_path: Path) -> dict[str, Any]:
    """Read Info.plist dictionary from an iOS .app directory, .ipa, or zipped .app."""
    try:
        if app_path.is_dir():
            plist_file = app_path / "Info.plist"
            if plist_file.is_file():
                data = plistlib.loads(plist_file.read_bytes())
                return data if isinstance(data, dict) else {}
            return {}
        if app_path.is_file() and app_path.suffix.lower() in {".ipa", ".zip"}:
            with zipfile.ZipFile(app_path, "r") as zf:
                plists = [
                    name
                    for name in zf.namelist()
                    if name.endswith("/Info.plist") and ".app/" in name
                ]
                if not plists:
                    return {}
                plists.sort(key=lambda n: (n.count("/"), len(n)))
                data = plistlib.loads(zf.read(plists[0]))
                return data if isinstance(data, dict) else {}
    except Exception:
        return {}
    return {}


def _validate_ios_simulator_app_binary(app_path: Path | None) -> None:
    """Fail fast with a helpful error if a device-only .ipa (iphoneos) is targeted at iOS Simulator."""
    if app_path is None or not app_path.exists():
        return
    if app_path.suffix.lower() == ".apk":
        raise ValueError(
            f"'{app_path.name}' is an Android .apk binary and cannot be installed on an iOS Simulator."
        )
    plist = _read_ios_info_plist(app_path)
    if not plist:
        return
    supported = {str(item).lower() for item in (plist.get("CFBundleSupportedPlatforms") or [])}
    dt_plat = str(plist.get("DTPlatformName") or "").strip().lower()
    if (dt_plat == "iphoneos" or "iphoneos" in supported) and "iphonesimulator" not in supported:
        raise ValueError(
            f"'{app_path.name}' is a physical-device iOS build (iphoneos) and cannot run on an iOS Simulator. "
            "Upload a zipped Simulator .app bundle built with '-sdk iphonesimulator', or select a physical iPhone profile."
        )


def _extract_ios_version_from_name(name: str) -> str:
    """Parse runtime version like '26.5' from 'iPhone 17 Pro (iOS 26 5)'."""
    m = re.search(r"\(iOS\s+(\d+(?:[\s._-]\d+)+)\)", name or "", re.IGNORECASE)
    if not m:
        return ""
    return re.sub(r"[\s_-]+", ".", m.group(1).strip())


def _ensure_ios_simulator_ready(profile: DeviceProfile) -> tuple[str, str, str]:
    """Ensure a macOS iOS Simulator is booted and return `(udid, platform_version, device_name)`."""
    udid = profile.udid or ""
    plat_ver = profile.platform_version or ""
    dev_name = profile.device_name or "iPhone Simulator"

    if platform.system() != "Darwin" or profile.target_type != "simulator":
        return udid, plat_ver, dev_name

    sims, _ = list_ios_simulators()
    if not sims:
        return udid, plat_ver, dev_name

    target = None
    if udid:
        target = next((s for s in sims if s.identifier == udid), None)
    if target is None:
        booted = [s for s in sims if str(s.state or "").lower() == "booted"]
        if dev_name:
            target = next((s for s in booted if dev_name.lower() in s.name.lower()), None)
        if target is None and booted:
            target = booted[0]
        if target is None and dev_name:
            target = next((s for s in sims if dev_name.lower() in s.name.lower()), None)
        if target is None:
            target = sims[0]

    udid = target.identifier
    if not plat_ver:
        plat_ver = _extract_ios_version_from_name(target.name)
    base_name = re.sub(r"\s*\(iOS[^)]*\)\s*$", "", target.name).strip()
    if base_name:
        dev_name = base_name

    if str(target.state or "").lower() != "booted":
        xc_env = _macos_xcode_env()
        boot_proc = subprocess.run(
            ["xcrun", "simctl", "boot", udid],
            capture_output=True,
            text=True,
            env=xc_env,
            timeout=30,
        )
        if boot_proc.returncode != 0 and "Unable to boot device in current state: Booted" not in (boot_proc.stderr or ""):
            raise RuntimeError((boot_proc.stderr or boot_proc.stdout).strip() or f"Failed to boot iOS Simulator {target.name}.")
        try:
            subprocess.run(["xcrun", "simctl", "bootstatus", udid, "-b"], capture_output=True, text=True, env=xc_env, timeout=45)
        except Exception:
            for _ in range(15):
                cur, _ = list_ios_simulators()
                if any(s.identifier == udid and str(s.state or "").lower() == "booted" for s in cur):
                    break
                time.sleep(1.0)
        if _xcode_has_legacy_simulator_app():
            subprocess.run(["open", "-a", "Simulator"], capture_output=True, text=True, env=xc_env, timeout=10)
        else:
            xcode_dev = find_xcode_developer_dir(xc_env)
            if xcode_dev is not None:
                device_hub = xcode_dev.parent / "Applications" / "DeviceHub.app"
                if device_hub.exists():
                    subprocess.run(["open", "-a", str(device_hub)], capture_output=True, text=True, timeout=10)

    return udid, plat_ver, dev_name


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
        resolved_udid, resolved_ver, resolved_dev_name = _ensure_ios_simulator_ready(profile)
        opts = XCUITestOptions()
        opts.platform_name = "iOS"
        opts.automation_name = profile.automation_name or "XCUITest"
        opts.device_name = resolved_dev_name
        if resolved_ver:
            opts.platform_version = resolved_ver
        if resolved_udid:
            opts.udid = resolved_udid
        if app_path is not None:
            opts.app = str(app_path.resolve())
        if env_cfg.ios_bundle_id:
            opts.bundle_id = env_cfg.ios_bundle_id
        opts.auto_accept_alerts = accept_alerts
        opts.no_reset = True
        opts.new_command_timeout = 180
        if profile.target_type == "simulator":
            _validate_ios_simulator_app_binary(app_path)
            opts.connect_hardware_keyboard = True
            opts.force_simulator_software_keyboard_presence = False
            has_headless_override = any(
                k in profile.capabilities for k in ("appium:isHeadless", "isHeadless")
            )
            # Xcode 27+ replaced Developer/Applications/Simulator.app with DeviceHub.app;
            # headless mode prevents appium-xcuitest-driver from shutting down booted simulators
            # and failing when trying to `open` the removed Simulator.app bundle.
            if not has_headless_override and not _xcode_has_legacy_simulator_app():
                opts.set_capability("appium:isHeadless", True)
        for k, v in profile.capabilities.items():
            opts.set_capability(k, v)

    raw_driver = webdriver.Remote(appium_url, options=opts)
    return AppiumDriverAdapter(raw_driver, platform=profile.platform)
