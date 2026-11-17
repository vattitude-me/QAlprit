# QAlprit Repository Rules & Developer Reference

These project rules and architecture notes govern git workflow, codebase navigation, cross-platform builds (`.exe`, `.dmg`, PyPI), website/Vercel deployments, report generation, and safety in this repository.

## 0. Repository Map & Architecture

- **`src/qalprit/` — Core Engine & Shared Desktop/Web Workbench** (bundled identically by PyPI, Windows `.exe`, and macOS `.dmg`):
  - **Entry points & Desktop Host**:
    - `cli.py`: Click CLI (`qalprit run`, `validate`, `list`, `inspect`, `doctor`, `devices`, `report`, `ui`, `version`).
    - `desktop.py`: Frozen `.exe`/`.app` GUI entry point (`launch_workbench()`), pywebview native window host, portable workspace bootstrap (`ensure_workspace()`), and bundled runtime path wiring (`runtime/`).
    - `ui_server.py`: Threading HTTP + API server (`create_ui_server()`, `/api/overview`, `/api/runs/*`, `/api/recorder/*`, `/api/emulator/*`, `/api/datasets/*`, `/api/flows/*`, `/api/specs/*`, `/api/screens/*`, `/api/environments/*`, `/api/apps/*`, `/api/system/*`).
    - `ui/index.html`: Single-file Workbench UI (first-launch welcome guide: platform, app, device, then record; views: `Testing space` (home, test cards with run/rename/duplicate/delete), `Record a test`, `Workflows`, `Journeys`, `Runs & reports`; Library: `Elements`; Settings: `Settings & setup`).
  - **Execution & Validation**:
    - `spec_loader.py` & `validator.py`: Parse Markdown+YAML journeys (`specs/`), reusable flows (`flows/`, optional `platforms:` scoping locator checks), screen locators (`screens/screens.yaml`), and `validate_project()` pre-flight checks.
    - `datasets.py`: CSV dynamic variable parameterization (`datasets/flows/`, `datasets/specs/`), secret column detection, and UI masking (`mask_bundle_for_ui()`).
    - `runner.py` & `steps/`: Step execution (`execute_step()`), flow expansion, retries, teardown, `SecretMasker`, and `FailureCategory` classification.
    - `driver_factory.py`: `AppiumDriverAdapter` and `create_appium_driver()` (Android UiAutomator2 AVD resolution + iOS XCUITest auto-boot via `_ensure_ios_simulator_ready()` + multi-strategy `hide_keyboard()`).
    - `inspector.py`: XML hierarchy parser (`parse_page_source_elements()` / `extract_interactive_elements()`) filtering keyboard overlays and ranking locators.
    - `evidence.py`, `visual_diff.py`, `results.py`, `report/`: Secret-redacted screenshots, XML failure dumps, visual baseline comparisons, and Jinja2 HTML/JUnit/Markdown report generation (`report/templates/report.html.j2`).
  - **Live Recorder & Emulator/Toolchain Management**:
    - `recorder.py`: `RecorderManager` interactive live mirror, element hit-testing, step editing (`update_step()` with `value`, `secret`, `move_to`, `duplicate`, `delete`), system prompt auto-dismiss (`find_system_prompt_dismiss()`, e.g. iOS "Save Password"), and saving into `screens.yaml`, `flows/` (with `platforms:`), and `specs/`. `save_recorded(parameterize=True)` turns typed values into `${variables}` (`parameterize_typed_steps()`) and seeds a `recorded` row in `datasets/flows/<name>.csv`.
    - `error_text.py`: `clean_error()` / `friendly_error()` turn exceptions into one readable line with a next step for the UI and API.
    - `workbench_api.py`: Workspace CRUD, `_resolve_device_profile()` (`live:`, `avd:`, YAML profiles, built-in `ios_simulator`/`android_emulator`), `RunJobManager`, and batch dataset runs.
    - `emulator_manager.py`: `EmulatorJobManager` for SDK/AVD setup, booting Android AVDs / iOS Simulators, and clean shutdown (`shutdown()`).
    - `doctor.py`, `devices.py`, `appium_manager.py`, `cleanup.py`, `paths.py`, `config_loader.py`, `env_loader.py`: Toolchain diagnostics, ADB/`xcrun simctl` discovery, Appium 2 subprocess lifecycle, OS data directories, and uninstall/leftover cleanup.
- **`packaging/` — Standalone Installers**:
  - `packaging/windows/`: `qalprit.spec` + `build_win.ps1` + `installer.iss` -> produces `dist-win/QAlprit-Setup-<X.Y.Z>-win64.exe` (pywebview Edge WebView2 + `qalprit-cli.exe` + portable Node/Appium/Java/Android SDK runtime).
  - `packaging/macos/`: `qalprit.spec` + `build_mac.sh` -> produces `dist-mac/QAlprit-<X.Y.Z>-macos-arm64.dmg` (`QAlprit.app` Cocoa WKWebView + portable Node/Appium/Java/Android platform-tools).
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
