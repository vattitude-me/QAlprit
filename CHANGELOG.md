# Changelog

All notable backend, UI, packaging, and documentation changes to **QAlprit** are recorded in this file, along with root causes and prevention notes ("Lessons Learned") from resolved issues so past mistakes are never repeated.

---

## [Unreleased]

### Web Showcase (`web/index.html` · `v0.1.25`)
- Updated `web/index.html` hero copy, guarantees strip, Step 01 card, Workbench showcase (`#workbench`), and architecture flow summary to prominently highlight **live emulator screen capture** for building reusable `flows/` and smoke `specs/` visually while **keeping AI / LLM usage to the minimum** (zero LLM calls at capture or execution time).

---

## Engineering Lessons Learned (Read Before Modifying Installers, CI, or Runtimes)

1. **Never mutate files inside macOS `*.app/Contents/` at build or runtime (`v0.1.23`)**: Rewriting `extensions.yaml` inside an already-signed `.app` invalidates `CodeResources` and causes macOS Gatekeeper to report `"QAlprit" is damaged and can't be opened`. Keep `.app/Contents/` read-only/immutable (`get_qalprit_data_dir() / "appium_home"` symlinks), run `codesign --verify --deep --strict` after smoke tests and `ditto`, and keep bundled runtime files `u+w` so `xattr -dr` works cleanly (`v0.1.24`, `v0.1.25`).
2. **Gate frozen GUI/WebView imports in CI installer scripts (`v0.1.24`, `v0.1.25`)**: Always install `.[desktop]` and collect platform WebView/PyObjC frameworks in both `qalprit.spec` files, and execute `--check-desktop` inside the compiled binary during `build_mac.sh` and `build_win.ps1` so packaging never silently falls back to opening a web browser.
3. **Handle Apple Xcode layout changes & `CommandLineTools` (`v0.1.24`, `v0.1.25`)**: Xcode 27 removed `Developer/Applications/Simulator.app` in favor of `Contents/Applications/DeviceHub.app`. Auto-detect via shared `find_xcode_developer_dir()` across `doctor`, `devices`, `appium_manager`, `emulator_manager`, and `driver_factory`, pass `appium:isHeadless = true` when legacy `Simulator.app` is absent, and validate simulator binaries (`iphoneos` `.ipa` vs `iphonesimulator` `.app.zip`) before calling Appium.
4. **Inno Setup `{app}` & 6.6+ Pascal Script rules (`v0.1.18`, `v0.1.19`)**: Never reference `{app}` inside `Check:` functions or `InitializeSetup` before directory selection (`An attempt was made to expand the "app" constant before it was initialized`). Match Inno Setup 6.6+ `CreateCustomForm` signatures.
5. **Android `sdkmanager` & AVD/DLL consistency (`v0.1.20`, `v0.1.21`)**: Always prefer `cmdline-tools/latest/bin/sdkmanager` over legacy `tools/bin` (which crashes under Java 17 with `NoClassDefFoundError: javax/xml/bind/annotation/XmlSchema`), resolve AVD SDK root from `image.sysdir.1` to prevent `PANIC: Broken AVD system path`, and verify Windows `AdbWinApi.dll` + `AdbWinUsbApi.dll` alongside `adb.exe`.
6. **Follow OS storage conventions & selective cleanup (`v0.1.17`, `v0.1.18`)**: Never silently create multi-GB SDKs in unapproved home folders; discover existing runtimes first, use AppData / Application Support + `Documents/QAlpritWorkspace`, and let users selectively retain or remove tools, caches, and workspaces on uninstall.

---

## [v0.1.25] — 2026-11-15 (`cf0e8a6`)

### Backend & Core
- Unified macOS Xcode discovery via `find_xcode_developer_dir()` and `_macos_xcode_env()` in `src/qalprit/doctor.py`, reused by `devices.py`, `appium_manager.py`, `emulator_manager.py`, and `driver_factory.py` (ensuring `DEVELOPER_DIR` resolves even when `xcode-select` points to `CommandLineTools` or non-default `Xcode*.app` installs).
- Added early iOS Simulator binary guard `_validate_ios_simulator_app_binary()` in `driver_factory.py` to reject physical-device `iphoneos` `.ipa` or Android `.apk` files with an actionable error before Appium startup.
- Updated `qalprit doctor` to warn when Xcode has `0 available simulator(s)` with instructions to install a Simulator runtime.

### UI, Packaging & CI
- Added `--check-desktop` self-check to `src/qalprit/desktop.py` (`check_desktop_webview_runtime`) and invoked it inside both `packaging/macos/build_mac.sh` and `packaging/windows/build_win.ps1`.
- Changed `chmod -R u+w "${RUNTIME_TARGET}"` across all bundled macOS runtime files prior to `codesign`.
- Synced visible `v0.1.25` badge in `web/index.html` and added `tests/test_driver_factory.py` + `tests/test_workbench_and_desktop.py` coverage.

---

## [v0.1.24] — 2026-11-15 (`0fd2bfb`)

### Backend & Core
- Added Xcode 27+ support in `driver_factory.py` (`_xcode_has_legacy_simulator_app()`): automatically sets `appium:isHeadless = True` on iOS Simulator sessions when `Developer/Applications/Simulator.app` does not exist.
- Updated `emulator_manager.py` `boot_ios_simulator()` to fall back to Apple's `DeviceHub.app` when `open -a Simulator` fails.
- Added default `ios_simulator` and `ios_device` profiles in `src/qalprit/desktop.py` (`_DEFAULT_DEVICES_YAML`).

### Packaging & Root Cause Fixes
- **Standalone macOS Window Fix**: Installed `.[desktop]` in `packaging/macos/build_mac.sh` and collected `webview`, `objc`, `Cocoa`, `Foundation`, `AppKit`, `WebKit`, `Quartz`, `Security`, and `UniformTypeIdentifiers` + `NSAppTransportSecurity` in `packaging/macos/qalprit.spec`.
- **Clean `xattr` on JRE**: Added `chmod -R u+w "${RUNTIME_TARGET}/jdk"` before `codesign` to prevent `Errno 13: Permission denied` on `0444` Temurin `jdk/legal/*` files during `xattr -dr com.apple.quarantine`.

---

## [v0.1.23] — 2026-11-15 (`46c63d6`)

### Root Cause Fix
- **macOS Gatekeeper `"QAlprit" is damaged and can't be opened`**: `ensure_portable_appium_home()` in `src/qalprit/doctor.py` previously rewrote `extensions.yaml` inside `dist/QAlprit.app` after `codesign` during CI smoke testing (and on writable `/Applications/QAlprit.app` launches), breaking the sealed signature.
- Added `_is_inside_macos_app_bundle()` so `.app/Contents/` is never mutated; `APPIUM_HOME` symlinks and `extensions.yaml` live under `get_qalprit_data_dir() / "appium_home"` with automatic stale-symlink refresh.
- Enforced `codesign --verify --deep --strict` post-smoke and post-`ditto` in `packaging/macos/build_mac.sh` and updated `HOW-TO-OPEN.txt` for macOS Sequoia/Tahoe.

---

## [v0.1.22] — 2026-11-15 (`8d5df07`)

### Backend, UI & Web
- Added environment-aware PII black-marker redaction (`redact_screenshots` in `EnvironmentConfig`: default `False` for `dev`/`development`/`local`, `True` for `qa`/`pre-prod`/`prod`).
- Added live toggle in `RecorderManager` (`POST /api/recorder/redaction`), CLI `--redact` / `--no-redact` in `cli.py`/`runner.py`, and UI switches in Step 1C, Step 2, and Step 4 (`src/qalprit/ui/index.html`).
- Updated `web/index.html` (`https://qalprit.vattitude.ca/`) showcasing interactive recording, redaction toggle, OS-standard storage, and cleanup.

---

## [v0.1.21] — 2026-11-15 (`e1bace6`)

### Backend & Installer Fixes
- Fixed Android Emulator `PANIC: Broken AVD system path` by resolving/matching `image.sysdir.1` in AVD `config.ini` against installed SDK roots (`doctor.py`, `emulator_manager.py`).
- Fixed Windows `adb.exe` `AdbWinApi.dll was not found` error by validating `AdbWinApi.dll` and `AdbWinUsbApi.dll` presence in `NeedsAndroidSdkRuntime()` (`packaging/windows/installer.iss`) and `doctor.py`.

---

## [v0.1.20] — 2026-11-15 (`86c9d6b`)

### Backend & Root Cause Fix
- Fixed `sdkmanager install failed: NoClassDefFoundError: javax/xml/bind/annotation/XmlSchema` under Java 17 by ensuring `find_android_tool("sdkmanager")` (`doctor.py`) prefers modern `cmdline-tools/latest/bin/sdkmanager` over legacy `tools/bin/sdkmanager`.

---

## [v0.1.19] — 2026-11-15 (`d025bc6`)

### Installer Fix
- Updated `packaging/windows/installer.iss` `CreateCustomForm` Pascal Script calls (`CreateCustomForm` parameter arity) for Inno Setup 6.6+ compatibility.

---

## [v0.1.18] — 2026-11-15 (`3b27694`)

### Backend, UI & Installer Fixes
- Fixed Windows installer `Runtime error (at 6:717): An attempt was made to expand the "app" constant before it was initialized` by deferring `{app}` path checks cleanly until after target directory initialization (`packaging/windows/installer.iss`).
- Added selective cleanup across backend (`src/qalprit/cleanup.py`, `qalprit cleanup` CLI, `/api/system/*` endpoints), Desktop UI (`src/qalprit/ui/index.html`), and Windows uninstaller checkboxes so users can selectively keep or remove workspace journeys, Android SDK/AVDs, and settings/caches.

---

## [v0.1.17] — 2026-11-15 (`919a730`)

### Backend & UI
- Added OS-standard directory resolution (`src/qalprit/paths.py`): `~/Documents/QAlpritWorkspace` and `%LOCALAPPDATA%\QAlprit` / `~/Library/Application Support/QAlprit`, avoiding root home folder clutter (`NTUSER.DAT`, oversized unprompted `~/.qalprit`).
- Added pre-install system software discovery (`discover_system_software`) and user-approved SDK/workspace storage wizard controls in `ui/index.html` and `packaging/windows/installer.iss`.

---

## [v0.1.16] — 2026-11-15 (`c304689`)

### Backend, UI & Installer
- Added smart Windows installer upgrade detection skipping unchanged bundled runtimes (Node, Appium, Java, Android SDK) unless **Force reinstall** is checked.
- Added streamlined 4-step workbench UI with inline workflow/journey CRUD and per-journey run selection.
