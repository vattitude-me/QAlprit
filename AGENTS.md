# QAlprit Repository Rules & Developer Reference

These project rules and architecture notes govern git workflow, codebase navigation, cross-platform builds (`.exe`, `.dmg`, PyPI), website/Vercel deployments, report generation, and safety in this repository.

## 0. Repository Map & Architecture

- **`src/qalprit/` — Core Engine & Shared Desktop/Web Workbench** (bundled identically by PyPI, Windows `.exe`, and macOS `.dmg`):
  - **Entry points & Desktop Host**:
    - `cli.py`: Click CLI (`qalprit run`, `validate`, `list`, `inspect`, `doctor`, `devices`, `report`, `review`, `refs`, `cleanup`, `ui`, `version`).
    - `desktop.py`: Frozen `.exe`/`.app` GUI entry point (`launch_workbench()`), pywebview native window host, portable workspace bootstrap (`ensure_workspace()`), and bundled runtime path wiring (`runtime/`).
    - `ui_server.py`: Threading HTTP + API server (`create_ui_server()`, `/api/overview`, `/api/runs/*`, `/api/recorder/*`, `/api/emulator/*`, `/api/datasets/*`, `/api/flows/*`, `/api/specs/*`, `/api/screens/*`, `/api/environments/*`, `/api/apps/*`, `/api/system/*`, `/api/references/fetch`, `/api/runs/ai-review`, `/api/runs/review`, `/api/atlassian/settings`, `/api/atlassian/test`).
    - `ui/index.html`: Single-file Workbench UI (first-launch welcome guide: platform, app, device, then record; views: `Testing space` (home, test cards with run/rename/duplicate/delete), `Record a test`, `Workflows`, `Journeys`, `Runs & reports`; Library: `Elements`; Settings: `Settings & setup` with tabs Apps & environments, Devices & health check, Jira & Confluence, Storage & cleanup).
  - **Execution & Validation**:
    - `spec_loader.py` & `validator.py`: Parse Markdown+YAML journeys (`specs/`), reusable flows (`flows/`, optional `platforms:` scoping locator checks), screen locators (`screens/screens.yaml`), and `validate_project()` pre-flight checks.
    - `datasets.py`: CSV dynamic variable parameterization (`datasets/flows/`, `datasets/specs/`), secret column detection, and UI masking (`mask_bundle_for_ui()`).
    - `runner.py` & `steps/`: Step execution (`execute_step()`), flow expansion, retries, teardown, `SecretMasker`, and `FailureCategory` classification. `_run_step_list` works through a queue so block steps can push the steps they pick.
    - `steps/flow_control.py`: `if_visible` (`then`/`else`) and `repeat` (`times`, or `until` with `max`, `${round}`). Nested steps stay raw YAML in the block's params (`BLOCK_STEP_KEYS`) and are read with `spec_loader.block_children()` when the block runs. The validator walks them with `iter_block_children()`.
    - Runs without an app file: `runner.installed_app_problems()` (used by `qalprit run` and `start_run`) needs an app id from the environment or `spec_loader.launched_app_id()`, and refuses `reinstall` (and iOS `clear_data`), which would uninstall. `apply_app_reset()` never calls `remove_app` without an app file. `TestRunner._missing_app_error()` checks `is_app_installed` first.
    - `driver_factory.py`: `AppiumDriverAdapter` and `create_appium_driver()` (Android UiAutomator2 AVD resolution + iOS XCUITest auto-boot via `_ensure_ios_simulator_ready()` + multi-strategy `hide_keyboard()`).
    - `inspector.py`: XML hierarchy parser (`parse_page_source_elements()` / `extract_interactive_elements()`) filtering keyboard overlays and ranking locators.
    - `evidence.py`, `visual_diff.py`, `results.py`, `report/`: Secret-redacted screenshots, XML failure dumps, visual baseline comparisons, and Jinja2 HTML/JUnit/Markdown report generation (`report/templates/report.html.j2`).
  - **Live Recorder & Emulator/Toolchain Management**:
    - `recorder.py`: `RecorderManager` interactive live mirror, element hit-testing, step editing (`update_step()` with `value`, `secret`, `move_to`, `duplicate`, `delete`), system prompt auto-dismiss (`find_system_prompt_dismiss()`, e.g. iOS "Save Password"), and saving into `screens.yaml`, `flows/` (with `platforms:`), and `specs/`. `save_recorded(parameterize=True)` turns typed values into `${variables}` (`parameterize_typed_steps()`) and seeds a `recorded` row in `datasets/flows/<name>.csv`.
    - `error_text.py`: `clean_error()` / `friendly_error()` turn exceptions into one readable line with a next step for the UI and API.
    - `workbench_api.py`: Workspace CRUD, `_resolve_device_profile()` (`live:`, `avd:`, YAML profiles, built-in `ios_simulator`/`android_emulator`), `RunJobManager`, and batch dataset runs.
    - `emulator_manager.py`: `EmulatorJobManager` for SDK/AVD setup, booting Android AVDs / iOS Simulators, and clean shutdown (`shutdown()`).
    - `doctor.py`, `devices.py`, `appium_manager.py`, `cleanup.py`, `paths.py`, `config_loader.py`, `env_loader.py`: Toolchain diagnostics, ADB/`xcrun simctl` discovery, Appium 2 subprocess lifecycle, OS data directories, and uninstall/leftover cleanup.
  - **Real devices, camera, one-time codes**:
    - `device_input.py`: Parses `adb shell getevent -lt` into taps, long presses, swipes and edge-swipe Back, so the recorder can follow someone working a USB phone or emulator by hand. Pure parsing, tested offline.
    - `camera_injection.py`: Feeds still images (ID card, selfie) into the Android **emulator** camera (`set_camera_image` step). It does nothing on physical phones (`ctx.target_type == "device"`).
    - `handoff.py`: `HandoffBroker`, the hand-off for anything only a person can do. `input` requests take a typed value, `action` requests wait for Done, Skip or Fail, and a `poll` callback can finish an action on its own (and keeps the Appium session alive). Workbench prompt via `/api/runs/handoff` (and `/api/runs/otp`), console prompt in a terminal, fails at once when nobody can be asked. `otp.py` is a shim for the old names.
    - `steps/handoff_steps.py`: `wait_for_user` (prompt, `until`, `input`/`target`, `optional`, `timeout`; screenshots off by default). `enter_otp` (`steps/interaction_steps.py`) uses the `OTP` variable when set, otherwise asks the same broker (120 s timeout). The recorder's Wait for user button adds the step and pauses recording while the person works the phone.
    - `background_devices.py`: Tracks emulators/simulators QAlprit started hidden and shuts them down on quit.
    - `EmulatorJobManager.restart_android_with_window()`: a `-no-window` emulator cannot open a window later, so Show window restarts the same AVD with the setting on.
    - `update_check.py`: daily PyPI version check (cached in settings.json) and pip self-update for pip installs (`/api/system/update`, `/api/system/update/install`, `/api/system/update/settings`).
  - **Jira / Confluence references and the AI check**:
    - `atlassian_refs.py`: `AtlassianConfig` (per-product credentials: `JIRA_*`, `CONFLUENCE_*`, falling back to `ATLASSIAN_*`; blank email means a Bearer PAT), `AtlassianClient`, `fetch_reference_docs()` (cached under `references/docs/`), and the Settings helpers `read_connection()`, `save_connection()`, `check_connection()`. The token is only sent to the configured hosts and is never returned to the UI.
    - `reference_check.py` and `recorder_paths.py`: Recorded step screenshots (`references/flows/<flow>/manifest.json` plus PNGs) and the replay-versus-recording pixel difference stored on each step.
    - `ai_review.py`: The AI check. Pluggable reviewers `anthropic` (API, `ANTHROPIC_API_KEY`), `agy` (Antigravity CLI) and `copilot` (GitHub Copilot CLI), chosen by `--ai-provider`, the UI menu or `QALPRIT_AI_PROVIDER`. CLI reviewers run in a throwaway folder with redacted JPEGs and a `review_request.md`. Verdicts go to `ai_review.json` and `results.json` and never change a test's pass or fail.
    - `spec_loader.effective_references(spec, flows)`: A journey's own `references:` plus those of every workflow it runs. The runner, `qalprit refs --id`, and the UI use it.
- **`packaging/` — Standalone Installers**:
  - `packaging/windows/`: `qalprit.spec` + `build_win.ps1` + `installer.iss` -> produces `dist-win/QAlprit-Setup-<X.Y.Z>-win64.exe` (pywebview Edge WebView2 + `qalprit-cli.exe` + portable Node/Appium/Java/Android SDK runtime).
  - `packaging/macos/`: `qalprit.spec` + `build_mac.sh` -> produces `dist-mac/QAlprit-<X.Y.Z>-macos-arm64.dmg` (`QAlprit.app` Cocoa WKWebView + portable Node/Appium/Java/Android platform-tools).
- **`tools/web-capture/`, QAlprit Web Capture**: Standalone Playwright script (`web_capture.py`), not part of the PyPI package. Signs in to a web portal once (SSO with `SSO_EMAIL`, falling back to a manual sign-in), saves the session to `auth/session.json`, then repeats the YAML steps per CSV row and writes screenshots, `extracted.csv` and `index.html` to `captures/<run-id>/`. `samples/` (tracked) holds the sample config, CSV, `.env` template and a sample run, and is attached to each GitHub release as `qalprit-web-capture-<X.Y.Z>.zip`. Tests: `tests/test_web_capture.py` (browser tests skip without Playwright).
- **`web/` — Marketing Site & Sample Report** (`https://qalprit.vattitude.ca`): Static landing page (`index.html`, `app.js`, `styles.css`, `architecture.svg`, `sample-report.html`) deployed via Vercel (`vercel.json`).
- **`.github/workflows/` — CI & Automated Release**:
  - `ci.yml`: Runs on PRs/pushes to `main` (`pytest -v --cov=qalprit`, `node --check web/app.js`, `qalprit validate`, `python -m build && twine check dist/*`).
  - `publish.yml`: Runs on GitHub Release `published` (`vX.Y.Z`) to build/publish PyPI (`build-and-publish`), build & attach `QAlprit-Setup-<X.Y.Z>-win64.exe` (`build-windows-exe` on `windows-latest`), and build & attach `QAlprit-<X.Y.Z>-macos-arm64.dmg` (`build-macos-dmg` on `macos-latest`).
- **`tests/` & Sample Workspace Directories**: `tests/` (`pytest` suite), `config/` (`environments.yaml`, `devices.yaml`, `env.example`), `screens/`, `flows/`, `specs/`, `datasets/`, `examples/`, `docs/`.

## 1. Git, Commits & PR Workflow

- **Never commit directly to `main`**: Create a feature/fix branch off `main` (e.g., `feat/...`, `fix/...`, `docs/...`, `release/...`).
- **Always proceed through PR & merge**: Open a Pull Request targeting `main`, wait for all GitHub Actions CI checks and Vercel checks to pass, squash-merge the PR into `main`, and fast-forward local `main` (`git checkout main && git pull --ff-only origin main`).
- **Stage explicit file paths**: Never run `git add .` blindly. Preserve unrelated uncommitted work (such as local edits in `docs/architecture.drawio`) and respect `.gitignore`.
- **Pre-commit verification**:
  - Run `.venv/bin/pytest -v` after Python or template changes (all tests must pass).
  - Run `node --check web/app.js` after editing frontend JS.
  - Validate sample suites when specs, flows, steps, or locators change (`qalprit validate --suite mda --env mda` and `qalprit validate --suite regression --env qa`, using `.env` or `config/env.example` without committing `.env`).

## 2. Versioning & Releases

- **Bump all version surfaces together**: Every release must update the exact same `X.Y.Z` version across:
  - `pyproject.toml` (`version = "X.Y.Z"`)
  - `src/qalprit/__init__.py` (`__version__ = "X.Y.Z"`)
  - `packaging/windows/installer.iss` (`#define QAlpritVersion "X.Y.Z"`)
  - `web/index.html` (synced release badge `vX.Y.Z`)
  - `CHANGELOG.md` (release section documenting backend, UI, packaging changes, and bug lessons learned)
- **Backend + UI Version Parity**: Desktop Workbench UI (`src/qalprit/ui/index.html` `#verPill`) reads `version` dynamically from `/api/overview` (`__version__`), and `web/index.html` must stay synced with the latest released `vX.Y.Z`. Report both `CHANGELOG.md` update status and backend + UI versions after every code/UI response.
- **Tagging**: Tag merged commits on `main` as `vX.Y.Z` before creating a GitHub Release.
- **Always cut a new release after merging**: After merging any user-approved code, UI, or packaging change to `main`, always bump the version (`X.Y.Z+1`) across all version surfaces, tag `vX.Y.Z` on `main`, and create a GitHub Release so `.github/workflows/publish.yml` builds and publishes updated PyPI packages, Windows `.exe` installer, and macOS `.dmg` artifacts.
- **Immutable PyPI versions**: Never re-upload to an already-published PyPI version number — bump at least the patch version (`X.Y.Z+1`) for any new PyPI release or `README.md` update on PyPI.

## 3. Releases, Builds & Publishing (`PyPI`, `.exe`, `.dmg`, `web/`)

- **Single Shared Source Across Targets**: All Workbench UI (`src/qalprit/ui/index.html`) and backend (`src/qalprit/*.py`) changes feed PyPI, Windows `.exe`, and macOS `.dmg` simultaneously. Keep PyInstaller specs (`packaging/windows/qalprit.spec`, `packaging/macos/qalprit.spec`) and runtime wrappers in sync whenever adding new Python dependencies or package data.
- **Automated Multi-Platform Release Flow**: Publishing a GitHub Release (`vX.Y.Z`) triggers `.github/workflows/publish.yml`:
  - Desktop installer jobs (steps 2 and 3) are paused by default since v0.1.45. They run only when the repo variable `BUILD_DESKTOP` is `true` or a manual `workflow_dispatch` run sets `desktop: true`. Until the user turns them back on, a release ships to PyPI only, and the post-publish check should not expect `.exe` or `.dmg` files.
  1. `build-and-publish` (`ubuntu-latest`): builds `.whl`/`.tar.gz` and publishes to PyPI via Trusted Publishing (`pypa/gh-action-pypi-publish`).
  2. `build-windows-exe` (`windows-latest`): runs `packaging\windows\build_win.ps1 -Version <X.Y.Z>` + Inno Setup (`packaging/windows/installer.iss`) and attaches `QAlprit-Setup-<X.Y.Z>-win64.exe` + `SHA256SUMS.txt` to the GitHub Release.
  3. `build-macos-dmg` (`macos-latest`): runs `packaging/macos/build_mac.sh <X.Y.Z>` and attaches `QAlprit-<X.Y.Z>-macos-arm64.dmg` + `SHA256SUMS-mac.txt` to the GitHub Release.
- **Never commit build artifacts**: Local `build/`, `dist/`, `dist-win/`, and `dist-mac/` directories are gitignored temporary build outputs and must never be committed.
- **Manual PyPI fallback upload**:
  1. Build fresh packages: `.venv/bin/python -m build` (or `python -m build`) and validate with `twine check dist/*`.
  2. Authenticate with `TWINE_USERNAME=__token__` and `TWINE_PASSWORD` read safely from local `.env` (`PYPI_TOKEN`) without ever printing, logging, or committing `.env` or token values.
  3. Upload **only the target version's artifacts** (`dist/qalprit-<X.Y.Z>*`) so older wheels/sdists in `dist/` are not re-uploaded.
- **PyPI README rendering**: All image and file links in `README.md` must use absolute GitHub URLs (`https://raw.githubusercontent.com/vattitude-me/QAlprit/main/...` for images and `https://github.com/vattitude-me/QAlprit/blob/main/...` for files), because PyPI rejects/404s relative paths.
- **Post-publish check**: Verify all three jobs in `.github/workflows/publish.yml` succeeded, `QAlprit-Setup-<X.Y.Z>-win64.exe` and `QAlprit-<X.Y.Z>-macos-arm64.dmg` are attached to the GitHub Release, and `https://pypi.org/pypi/qalprit/<X.Y.Z>/json` and `https://pypi.org/project/qalprit/` reflect the new version.

## 4. Website (`web/`), Vercel & Report Consistency

- **Vercel static config (`vercel.json`)**: Preserve `"framework": null`, `"installCommand": ""`, `"buildCommand": null`, `"outputDirectory": "web"`, and `"cleanUrls": true` so Vercel serves `web/` statically instead of misdetecting `pyproject.toml` as a serverless Python app.
- **Domain & Attribution**: Production domain is `https://qalprit.vattitude.ca` (sample report at `/sample-report`). Maintain the `Managed by vattitude.ca` (`https://vattitude.ca`) footer across `web/index.html`, `src/qalprit/report/templates/report.html.j2`, `src/qalprit/ui/index.html`, and `README.md`.
- **Sample Data & Theme Sync**:
  - Keep `web/index.html`, `web/app.js`, and `web/previews/*` synchronized with `examples/mda-sample-run/results.json` and `web/sample-report.html`.
  - Keep the adaptive light/dark teal-slate theme (`#0f766e` / `#2dd4bf` accent; `#f3f6f9` / `#090e1a` backgrounds) aligned between `web/styles.css` and `src/qalprit/report/templates/report.html.j2`.
- **Report Regeneration**:
  - Regenerate `examples/mda-sample-run/report.html` with `home_url=None` (standalone CLI sample artifact).
  - Regenerate `web/sample-report.html` with `home_url="/"` so website visitors have the sticky top nav bar, `← Back to Home` header button, and footer home link back to `/`.
  - Never inject `data:image/` URIs into `report.html.j2` when `external_assets=True`.

## 5. Documentation, Privacy & Safety

- **Concise Docs & Architecture Flow**: Keep `README.md` clear and accessible (prerequisites, install, run, output, and simple animated architecture flow). Maintain a single canonical SVG at `web/architecture.svg` (served by Vercel and referenced via GitHub raw URL in `README.md`).
- **Secrets & Ignored Artifacts**: Never commit `.env`, tokens/credentials, `.apk`/`.ipa` binaries (`apps/*`), raw test runs (`results/*`), `.agents/`, `PLAN.md`, `docs/PLAN.md`, `flows/hark/`, `specs/smoke/`, or unredacted screenshots.
- **Destructive Actions**: Always ask the user for explicit confirmation before deleting files or directories. Use `tempfile.TemporaryDirectory()` for temporary build/report-generation links instead of creating files that require deletion.

## 6. AI Test Execution & Spec Evolution Rules

- **Verify tools & suite before device runs**: Run `qalprit doctor`, `qalprit devices`, and `qalprit validate --suite <suite> --env <env> --platform <platform>` before executing `qalprit run`.
- **Inspect before editing locators**: Never guess element IDs or XPaths. Inspect live screens with `qalprit inspect` or failure dumps (`results/<run-id>/page_sources/*/attempt_*/*_FAIL.xml`). Keep `android:` and `ios:` overrides independent in `screens/screens.yaml`.
- **Do not mask real app bugs**: When `results/<run-id>/screenshots/*/attempt_*/*_FAIL.png` shows an actual product regression or wrong app behavior, report the failure with proof instead of loosening assertions to make tests green. Scope genuinely missing platform features explicitly (`platforms: [android]` or `[ios]`).
- **Protect secrets & redaction**: Keep `secret: true` on credential/PII `type` steps and `sensitive: true` on sensitive locators in `screens/screens.yaml`. Never run destructive `xcrun simctl erase` or delete files without explicit user confirmation.

## 7. Embedded browser compatibility (desktop app)

- **The desktop UI runs in WKWebView (macOS) and Edge WebView2 (Windows), not only in a modern browser.** A feature that works in Chrome or Safari can be switched off in the app. The Popover API broke the top menus in the macOS app more than once because the CI bootloader is linked against macOS SDK 12.1 and WKWebView hides newer web features from apps linked that way.
- **Never call `el.matches(":popover-open")` or rely on `popover` alone.** Use `isPopOpen(el)` and the fallback in `src/qalprit/ui/index.html` (`installPopoverFallback`). Any new `popover` menu needs the `menu-popover` class so the fallback covers it. `tests/test_webview_compat.py` enforces this.
- **Keep the SDK relink in `packaging/macos/build_mac.sh`.** After building, run `vtool -show-build dist-mac/QAlprit.app/Contents/MacOS/QAlprit`. The `sdk` value must be 17 or higher, or the build fails.
- **Test newer web features with the feature off.** Before relying on a recent browser API in `src/qalprit/ui/index.html`, add a fallback and check it in the packaged app or a frozen pywebview probe, not only in a local browser.

## 8. Workspace location

- **The Workbench never uses the repo folder as its workspace by default.** `qalprit ui` resolves the workspace with `resolve_default_workspace(use_cwd=False)` (saved Settings folder, else `~/Documents/QAlpritWorkspace`) so recording, saving and runs do not change tracked files. To work on the sample suites in this repo, run `qalprit ui --workspace .` on purpose and review `git status` before committing.
- **A workspace inside a git repo must be ignored.** `ensure_workspace()` calls `ensure_workspace_git_ignored()`. Keep that call when changing workspace setup, and never write a `*` ignore file into a folder that is itself a repository.
- **Switching folders goes through Settings > Storage & cleanup** (`/api/system/preferences`, with `/api/system/pick-folder` for the chooser). Do not add other code paths that move the workspace.

## 9. How the tool works, end to end

1. **Author.** A *workflow* (`flows/*.md`, reusable steps, one level deep) or a *journey* (`specs/<suite>/*.md`, an end-to-end test that calls workflows with `run_flow`) is Markdown with YAML front matter. Locators live in `screens/screens.yaml` (`android:` and `ios:` overrides stay independent). Data lives in CSV files under `datasets/`. Secrets live in the workspace `.env` and are referenced as `${NAME}`. The full step list and field tables are in `docs/authoring-guide.md`.
2. **Record (optional).** The Record page mirrors an emulator, simulator or USB phone (`recorder.py`). Taps, typing, swipes and app switches become steps. Each step keeps a redacted screenshot of the screen it led to. Saving writes the workflow, new locators, a dataset row, and `references/flows/<workflow>/`.
3. **Validate.** `qalprit validate --suite <s> --env <e> --platform <p>` checks steps, locators and variables before a device is touched.
4. **Run.** `qalprit run`, or the Workbench Run button (`POST /api/runs/start`), starts Appium (`appium_manager.py`), creates a driver (`driver_factory.py`), applies the reset policy (`clear_data` runs `adb shell pm clear` then relaunches), expands workflows (`expand_steps`) and executes each step (`steps/`). Failures get a screenshot, an XML dump and a `FailureCategory`.
5. **Report.** `results/<run-id>/` holds `results.json`, `summary.md`, `junit.xml`, `report.html`, screenshots (secret fields blacked out) and page sources. Each replay screenshot sits next to the recorded one (`*_REF.png`) with a mismatch score.
6. **AI check (optional).** `--ai-review`, `qalprit review <results dir>` or Review > Check with AI sends each step's recording and replay screenshot plus the Jira/Confluence text to the chosen provider. Each step gets pass, fail or unclear with a reason. A person confirms with Looks right or Wrong. Verdicts never change pass or fail.
7. **Connect Jira and Confluence.** Settings & setup > Jira & Confluence writes `ATLASSIAN_*`, `JIRA_*` and `CONFLUENCE_*` keys to the workspace `.env` (`env_loader.update_env_file` keeps other lines and comments, creates the file owner-only). Add links under References on a workflow or journey. A journey inherits the links of the workflows it runs.

Driving the Workbench from a script: start `qalprit ui --workspace . --port 8766 --no-open`, then `POST /api/runs/start` with `platform`, `device_profile` (`avd:<name>`, `live:android:<serial>`, or a YAML profile), `env_name`, `spec_id`, `app_path`, `reset_mode`. Poll `GET /api/runs/status` (it reports `otp_pending`) and answer a code prompt with `POST /api/runs/otp {"code": "..."}`.

### Working on a real Android phone

- `adb` is not on `PATH` on a typical Mac. QAlprit installs it under `~/Library/Application Support/QAlprit/android-sdk/platform-tools/adb`. In Python use `find_android_tool("adb")`. Quote that path in shell commands.
- A phone can have a work profile (a second user). `pm clear` and relaunch act on the main user.
- `adb shell content query --uri content://sms/inbox` works from the shell on a phone, which is how the planned SMS code reader gets codes.
- After `pm clear` another app (for example Gmail) can take the foreground. That is device interference, not an app bug. Ask the user to turn on Do Not Disturb and leave the phone on the home screen.
- Android 16 shows an "Android App Compatibility" dialog over apps that are not 16 KB aligned. `wait_for_element` dismisses it (`_dismiss_android_compat_dialog` in `steps/base.py`) when an element is not found. `appWaitActivity: "*"` is set on the device profiles so apps whose first activity differs still start.
- Run on the device the user names. Ask before choosing an emulator when they have a phone connected.

## 10. Current state and open work

Latest release: see `CHANGELOG.md`. Everything below is not done yet. Check `git log` and the changelog before starting, because this list can go stale.

- **TNG onboarding needs a new workflow (not built).** The old recorded flows (`tng-non-prod-acc-open-2`, `-3`), `flows/tng-onboarding.md` and `TNG-002` were removed because they started with `reset: clear_data`, which wipes the logged-in session the other TNG journeys need. The `tng_onboarding` locators in `screens/screens.yaml` are kept for the new one. Ask the user how the run gets a fresh app (for example a second phone) before building it, and never clear data on the phone that holds the logged-in session. Things learned on the device:
  - Under a test run the debug menu's "Show Business Waitlist" switch shows off while the app still uses on, so the flow has to switch it on and then off. Without that, "Become a client" opens the business waitlist.
  - The server keeps an application per phone number. A number used before skips the name, date of birth, address and email screens after the passcode, so a run with new details needs an unused number (the user's non-prod numbers take a `#N` suffix).
  - The notifications screen shows "Not Now" or only "You're All Set" when the permission is already granted. The ID picker opens only when tapped near its right edge (`offset_x: 0.92`).
  - The ID scan and selfie need a `wait_for_user` step. The emulator camera injection (`set_camera_image`) crashes the app according to the user and has not been investigated (start at `camera_injection.py` and `steps/device_steps.py`).
  - The identity check compares the scan with the name, date of birth and address entered earlier, so they must match the ID. The user's real details live only in their workspace copy, never in this repo. The two Ontario licence mock-up images in `docs/` are the user's untracked local files. Do not stage them.
  - The old flow reached the tax step on the phone, not yet the ID check.
- **Read the SMS code directly (not built, user has not said yes).** Idea: in `enter_otp`, before prompting, poll `adb shell content query --uri content://sms/inbox` for a message newer than the step start, extract a 4 to 8 digit code, type it, never log the message body, and fall back to the prompt after about 60 seconds. Physical Android phones only. Typing the code by hand on the device makes `enter_otp` fail with `not_found`, because it waits for the field itself, so users must answer in the Workbench.
- **MDA-004 payment screenshots.** The run passes on a Pixel 9 Pro, with 2 steps marked unclear by the AI check because the keyboard clipboard panel hides the payment fields. Add `hide_keyboard: true` before those screenshots.
- **Copilot AI provider is unverified.** `agy` was verified live against Antigravity CLI 1.5.14. `copilot` follows the documented `-p`, `--silent` and `--add-dir` flags but has not run against a real install (`npm install -g @github/copilot`, then sign in).
- **Jira and Confluence connection is tested only with fakes.** Try Test connection against a real Atlassian Cloud site and a Data Center PAT.
- **Recorder cannot reopen a saved workflow** to add, remove or reorder steps (listed under Upcoming in `CHANGELOG.md`).
- **`if_visible`, `repeat` and installed-app runs are tested only with the fake driver.** Try a run on the user's phone with *App already on the device* and a permission popup marked Only if shown.
- **Recorder Only if shown wraps one step at a time.** A popup that needs two taps gets two conditional steps. Grouping them into one `if_visible` block has to be done in the step editor.

## 11. Writing and UI rules agents keep breaking

- **No native `confirm()`, `alert()` or `prompt()` in the Workbench.** WKWebView blocks them. Use `uiConfirm()` and `uiAsk()` in `src/qalprit/ui/index.html`.
- **Every save path for a journey or workflow must pass every field through.** A step-only save once dropped `references`. `save_flow_spec(references=None)` keeps the saved links, a list replaces them. Do the same for any new front matter field.
- **Secrets never go back to the UI.** Return only whether a token is set, and treat a blank token field as "keep".
- **Docs style.** Plain words, full stops and commas instead of dashes, sentence case headings. No attribution lines in commits or PRs (see the user's global instructions).
