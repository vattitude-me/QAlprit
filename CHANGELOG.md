# Changelog

All notable backend, UI, packaging, and documentation changes to **QAlprit** are recorded in this file, along with root causes and prevention notes ("Lessons Learned") from resolved issues so past mistakes are never repeated.

---

## [Unreleased]

- _No unreleased changes yet._

---

## [v0.1.29] — 2026-11-16

### Backend & Live Mirror Fixes (iOS Scrolling & Bottom Tab Bar Controls)
- **Fix iOS Retina Scaling on Scrollable Screens (`src/qalprit/recorder.py`)**: Never set `already_pixel_space = True` when `self._platform == "ios"` (and require both `x` and `y` dimensions beyond `1.15×` on non-iOS). Previously, any iOS `ScrollView` / catalog screen with off-screen items below `win_h * 1.15` (e.g., `y2 = 1170 > 874 * 1.15` on `iPhone 17 Pro`) falsely skipped `3x` Retina scaling, shrinking all overlays into the top-left `1/3` corner and making bottom controls (`Catalog`, `Cart`, `More`) unclickable.
- **Prioritize Visible On-Screen Elements & Raise Mirror Element Cap (`src/qalprit/recorder.py`)**: Raised `RecorderManager.status()["elements"]` cap from `80` to `250`, sorted visible on-screen elements ahead of scrolled off-screen items, and skipped `visible=False` items in `hit_test_element()`.

### UI Improvements
- **Interactive-First Overlay Z-Order & Offscreen Clamping (`src/qalprit/ui/index.html`)**: Updated `renderRecorderElements()` to clamp partially off-screen boxes cleanly to `[0..100%]`, skip invisible/zero-area/oversized (`>75%`) wrappers, and sort static labels below clickable/editable controls so buttons and bottom tab bars always sit cleanly on top with accurate hover tooltips.

### Tests
- Extended `test_recorder_scales_ios_retina_rects_redacts_and_swipes_in_points` (`tests/test_workbench_and_desktop.py`) with an offscreen scrolled card (`y2 = 1170 > 874 * 1.15`) and bottom `TabBar` `Cart` button to prove `3x` Retina scaling and on-screen priority hold on scrolling iOS pages.

### Root Cause & Lesson Learned
- Checking `max_y2 > win_h * 1.15 or max_x2 > win_w * 1.15` confused scrollable logical-point content extending past the viewport with raw pixel coordinates — passing single-screen pages (e.g., Product Details) while breaking scrollable Catalog/Feed screens. XCUITest XML always reports logical points regardless of scroll depth, and visible viewport controls must never be crowded out by an arbitrary `[:80]` slice before rendering overlays.

---

## [v0.1.28] — 2026-11-15

### Backend & macOS Android Emulator Setup Fix
- **Copy Real `cmdline-tools` Directory in Writable SDK (`src/qalprit/doctor.py`)**: Updated `_link_or_copy_dir()` and `_sync_bundled_sdk_into()` to always copy `cmdline-tools` as a real directory (`copy_real=True`) instead of symlinking it on macOS/Linux, and to automatically heal any pre-existing POSIX symlink left by earlier builds into a real directory while preserving already-downloaded `system-images/` and `emulator/`.

### Tests
- Updated `test_prepare_writable_android_sdk_and_find_sdkmanager` (`tests/test_workbench_and_desktop.py`) to verify `cmdline-tools` is never a symlink and that stale POSIX symlinks at `cmdline-tools` are healed into real directories.

### Root Cause & Lesson Learned
- On macOS `.dmg`, `_sync_bundled_sdk_into()` symlinked `cmdline-tools -> /Applications/QAlprit.app/Contents/Resources/runtime/android-sdk/cmdline-tools`. Although `sdkmanager --sdk_root=...` downloaded `system-images;android-34;google_apis;arm64-v8a` into the writable user SDK, `avdmanager` computes its SDK root via shell `APP_HOME="$(pwd -P)"` and Java `AvdManagerCli` `Path.toRealPath()` (`../../`), resolving symlinks back into the read-only `.app` bundle where zero system images exist (`Error: Package path is not valid. Valid system image paths are: null`). Always materialize `cmdline-tools` as a real directory inside the writable SDK across all OSes.

---

## [v0.1.27] — 2026-11-15

### Backend Fixes
- **Direct Workflow Execution & Dry-Run Mode (`src/qalprit/validator.py`, `src/qalprit/workbench_api.py`)**: Added `flow_name: str | None = None` support to `validate_project()` and `RunJobManager.start()`, allowing any saved workflow under `flows/` to be validated, previewed via Dry Run, and executed directly in Step 4 as a synthetic `FLOW-<SLUG>` test spec even before wrapping it into `specs/*.md`.
- **iOS `.zip` Binary Validation (`src/qalprit/validator.py`)**: Accepted `.zip` alongside `.app` and `.ipa` in `valid_exts["ios"]` so compressed iOS Simulator app bundles no longer trigger misleading extension warnings.
- **Actionable Mismatch Errors (`src/qalprit/workbench_api.py`)**: Replaced the generic `"No matching test journeys found to run."` error with specific diagnostics naming existing journeys on other platforms (e.g., `SMK-001 [android]`), other suites, or available `flows/`.

### UI Improvements (Windows `.exe` & macOS `.dmg` Parity)
- **Step 4 Direct Workflow Selector & Live Match Diagnostics (`src/qalprit/ui/index.html`)**: Added a `Workflow (Optional)` picker (`#runFlowFilter`), live `#runMatchHint` diagnostics explaining platform/suite matches or mismatches before running, and a `Run ->` button on saved workflows in Step 3 (`#flowsTable`), plus Step 2 `btnRecGoRun` support even when saving only a workflow.
- **Step 3 iOS Target Platform Auto-Default (`src/qalprit/ui/index.html`)**: Defaulted `#specPlatIos` to checked on macOS hosts (`Darwin`) and whenever an iOS binary/platform is active (`!STATE.specPlatUserTouched`), matching `resetJourneyBuilderToNew()` so newly authored journeys on Mac aren't accidentally saved as `[android]`-only.
- **Accurate Redaction Label (`src/qalprit/ui/index.html`)**: Updated `syncRunRedactToEnv()` copy from `"off by default for '...'"` to `"enabled/off in '...' config (editable in Step 1C)"` so it reflects the configured `environments.yaml` flag unambiguously.

### Tests
- Added `test_validate_project_accepts_ios_zip_and_single_flow_mode` (`tests/test_validator.py`) and `test_run_job_manager_supports_direct_flow_and_helpful_mismatch_error` (`tests/test_workbench_and_desktop.py`).

### Root Cause & Lesson Learned
- Step 3's `#specPlatIos` HTML checkbox started unchecked on initial load (only `resetJourneyBuilderToNew()` checked `hostOs === "Darwin"`), causing journeys created on Mac to save with `platforms: [android]` while Step 4 defaulted to `ios` (`All Journeys (0)`), and Step 4 lacked any selector to run standalone `flows/*.md` directly. Keep initial HTML/overview defaults aligned with `reset*()` helpers, validate `.zip` as a first-class iOS simulator binary extension, and surface platform/suite mismatches immediately in both the UI hint and backend error messages.

---

## [v0.1.26] — 2026-11-15

### Backend & Live Recorder Fixes
- **iOS Retina `2x`/`3x` Live Mirror Coordinate Scaling (`src/qalprit/recorder.py`)**: `RecorderManager._capture_snapshot_locked()` now scales XML element `rect` bounds from logical iOS points (`driver.get_window_size()`, e.g., `402×874`) to PNG screenshot pixels (e.g., `1206×2622` on 3x Retina), fixing overlay alignment, direct image click hit-testing, and Pillow black-marker redaction on the Live Mirror while keeping `driver.swipe()` in logical window coordinates.
- **iOS Tappable Element & Shell Filtering (`src/qalprit/inspector.py`)**: Detects native XCUITest interactive tags (`XCUIElementTypeButton`, `Link`, `Cell`, `Switch`, `Tab`, `SegmentedControl`, `Stepper`, `MenuItem`) as `clickable=True` (since iOS XML omits `clickable="true"`) and filters out full-screen container shells (`XCUIElementTypeApplication`, `XCUIElementTypeWindow`, `hierarchy`, `AppiumAUT`).

### UI & Web Showcase
- **Mirror Overlay Stacking (`src/qalprit/ui/index.html`)**: Filters hidden/oversized (>85% viewport) overlays and sorts bounding boxes largest-to-smallest with ascending `z-index` (`.rec-box:hover` at `z-index: 999`) so nested buttons sit above parent wrappers.
- **Website Update (`web/index.html`)**: Updated hero copy, execution strip, Step 01, Workbench showcase, and architecture summary to highlight live emulator screen capture for building workflows and smoke tests with minimal AI / LLM usage, and synced release badge to `v0.1.26`.

### Tests
- Added `test_inspector_detects_ios_clickable_buttons_and_skips_app_shell` (`tests/test_inspector_and_cli.py`), `test_recorder_scales_ios_retina_rects_redacts_and_swipes_in_points` (`tests/test_workbench_and_desktop.py`), and included `tests/test_appium_manager.py` macOS `/usr/bin/java` `JAVA_HOME` guard regression tests.

### Root Cause & Lesson Learned
- iOS Appium `page_source` and `get_window_size()` report **logical points**, whereas `get_screenshot_as_png()` returns **2x/3x Retina pixels**, and XCUITest uses tag names instead of `clickable="true"`. Always normalize element rectangles against `px / win` before rendering overlays, hit-testing taps, or drawing live mirror masks, and treat iOS button/cell/tab tags as `clickable=True`.

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
