# QAlprit (`qalprit`)

> **Mobile QA. Bugs caught with proof.**

[![PyPI](https://img.shields.io/pypi/v/qalprit?color=0f766e&label=pypi)](https://pypi.org/project/qalprit/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Powered by Appium 2](https://img.shields.io/badge/Appium-2.x-662d91.svg)](https://appium.io/)

**QAlprit** is a deterministic, Markdown-driven mobile UI test automation framework for native **Android** and **iOS** applications powered by **Appium 2**.

Every test run produces verifiable visual and structural proof—step-by-step screenshots, masked failure XML dumps, Appium logs, and self-contained HTML reports—with **zero LLM calls at runtime** and **in-memory secret redaction** before any image touches disk.

---

## Why QAlprit?

- **100% Deterministic Execution** — Zero runtime LLM calls or flaky AI guesswork. Test steps compile directly into Appium 2 actions with automatic element readiness polling.
- **QA-Friendly Markdown Specs** — Author tests as Markdown files with YAML front matter under `specs/`, compose 1-level reusable `flows/`, and keep shared cross-platform locators in `screens/screens.yaml`.
- **Security-First Evidence Pipeline** — `.env` substitution and RFC 6238 `${TOTP:VAR}` generation run purely in Python stdlib. Secrets are masked across logs, JSON, XML, and HTML reports, while Pillow black-fills `secret: true` and `sensitive: true` bounding boxes **in memory** (scaled to Retina/device pixels) before writing PNGs to disk.
- **Self-Contained Proof Reports & Workbench UI** — Generates a single portable `report.html` (Pillow-compressed WebP/JPEG screenshots with interactive lightbox, or `--external-assets`), `summary.md` for PR comments, `results.json`, and includes a local `qalprit ui` workbench plus a static Web UI in `web/` ready for Vercel.
- **Guaranteed Cleanup & Target Gating** — `teardown:` steps execute even after mid-test failures; simulator-only steps like `biometric` are cleanly skipped on real devices.

---

## Architecture

Editable Draw.io source: [`docs/architecture.drawio`](docs/architecture.drawio)

![QAlprit System Architecture](docs/architecture.svg)

### Layer-by-Layer Breakdown

1. **Authoring & Configuration**
   - `specs/**/*.md`: Test cases written in Markdown with YAML front matter (`id`, `title`, `platforms`, `targets`, `reset`, `steps`, `teardown`, `expected`).
   - `flows/**/*.md`: Reusable **1-level-deep** step sequences invoked via `run_flow` with `${param}` interpolation (nested flows are rejected at validation time).
   - `screens/screens.yaml`: Logical `<screen>.<element>` locator catalog supporting shared `accessibility_id`, per-platform `android`/`ios` overrides, parameterized `{name}` templates, and `sensitive: true` screenshot redaction markers.
   - `config/` & `.env`: `devices.yaml` profiles, `environments.yaml` app package/bundle IDs & timeouts, plus local `.env` secrets and RFC 6238 `${TOTP:VAR}` authenticator codes.
2. **Deterministic Execution Core (`src/qalprit/`)**
   - `cli.py`: Click CLI (`doctor`, `devices`, `inspect`, `validate`, `run`, `report`, `ui`).
   - `validator.py`: Fail-fast pre-flight validation of step vocabulary, 1-level flows, locator targets, template parameters, and environment variables—no device required.
   - `runner.py` & `steps/`: Reuses one Appium 2 session across compatible tests, applies `reinstall` / `clear_data` / `none` reset strategies, polls for element readiness, tracks `--retries` / `flaky` status, and guarantees `teardown:`.
   - `appium_manager.py`: Auto-starts and stops a local Appium 2 subprocess (auto-detecting `ANDROID_HOME`, `JAVA_HOME`, and free ports) or attaches to an existing server via `--appium-url`.
3. **Appium 2 & Native Targets**
   - Drives **Android** emulators/devices (`UiAutomator2`, `.apk`, `adb shell pm clear`) and **iOS** simulators/devices on macOS (`XCUITest`, `.app`/`.ipa`, `xcrun simctl`).
4. **In-Memory Evidence Redaction & Reporting**
   - `evidence.py`: Captures raw PNG bytes in memory, scales Appium element bounds by `screenshot_width / window_width`, black-fills sensitive fields via Pillow, scrubs strings via `SecretMasker`, and flags embedded WebViews (`C8`).
   - `report/generator.py` & `ui_server.py`: Renders `report.html`, `summary.md`, and powers the local `qalprit ui` workbench server.

---

## Quick Start

### Install

```bash
pip install qalprit
```

Or install from source for local development:

```bash
git clone https://github.com/vattitude-me/QAlprit.git
cd QAlprit
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Validate & Run

```bash
# 1. Copy sample environment variables (never commit .env)
cp config/env.example .env

# 2. Check OS prerequisites (Node, Appium 2, drivers, JDK 17+, Android SDK, Xcode on macOS)
qalprit doctor

# 3. List connected Android devices/AVDs and iOS simulators
qalprit devices

# 4. Validate specs, flows, locators, and env vars without needing a device
qalprit validate --suite mda --env mda

# 5. Run a suite against an Android APK (Appium 2 auto-started and stopped)
qalprit run --platform android --device android_emulator --app /path/to/app.apk --env mda --suite mda

# 6. Open the local Workbench UI to browse runs, proof reports, specs, and sensitive locators
qalprit ui
```

---

## CLI Commands

| Command | Purpose |
|---|---|
| `qalprit doctor [--json-output]` | OS-aware prerequisite check (skips Xcode on Linux/Windows). |
| `qalprit devices` | List connected Android `adb` devices, AVDs, and iOS simulators. |
| `qalprit inspect --platform android\|ios [--device <profile>] [--from-xml <file>]` | Dump addressable UI elements, flag WebViews, and emit starter `screens.yaml`. |
| `qalprit validate [--suite <name>] [--env <name>] [--platform android\|ios] [--tag <tag>]` | Fail-fast schema, flow, locator, template param, and env var validation. |
| `qalprit run --platform android\|ios --device <profile> --app <path> [options]` | Execute specs (`--env`, `--suite`, `--tags`, `--retries`, `--appium-url`, `--external-assets`). |
| `qalprit report <results-dir> [--external-assets]` | Regenerate `report.html` and `summary.md` from an existing `results.json`. |
| `qalprit ui [--port 8765] [--results-dir results] [--no-open]` | Launch the local QAlprit Workbench web UI. |

---

## User Interfaces

1. **Self-Contained Proof Report (`report.html`)** — Generated per run in `results/<run-id>/report.html` with KPI cards, status/tag/text filters, expanded flow badges, failure XML inspection, and a modal screenshot lightbox.
2. **Local Workbench (`qalprit ui`)** — Zero-dependency Python HTTP server for browsing historical runs, opening proof reports, and auditing `sensitive: true` screen locators.
3. **Web Showcase & Interactive Demo (`web/`)** — Static web application configured via [`vercel.json`](vercel.json) for instant Vercel deployment with an interactive Proof Report Viewer and Spec Studio.

---

## Repository Structure

```text
QAlprit/
├── config/
│   ├── devices.yaml                # Named Android/iOS emulator & device profiles
│   ├── environments.yaml           # Per-env package/bundle IDs and timeouts
│   └── env.example                 # Sample .env variables (copy to .env locally)
├── docs/
│   ├── architecture.drawio         # Editable Draw.io architecture diagram
│   ├── architecture.svg            # Exported architecture vector diagram
│   ├── authoring-guide.md          # QA step vocabulary, worked examples & iOS Keychain note
│   └── copilot-conversion-prompt.md
├── flows/                          # 1-level reusable Markdown flows
├── screens/
│   └── screens.yaml                # Logical locators & sensitive: true redaction markers
├── specs/                          # Test suites (mda/, regression/)
├── src/qalprit/                    # Core Python package, steps, evidence, report & UI server
├── tests/                          # Unit tests (FakeDriver, Pillow redaction, validator, UI)
├── web/                            # Vercel-ready web showcase & interactive proof demo
├── pyproject.toml
└── vercel.json
```

---

## Documentation & Community

- [QA Authoring Guide](docs/authoring-guide.md) — Complete step vocabulary, worked examples, locator strategy, and iOS Keychain reset caveat.
- [Copilot Confluence Conversion Prompt](docs/copilot-conversion-prompt.md) — Prompt template and review checklist for converting manual test plans.
- [Contributing Guide](CONTRIBUTING.md)
- [Security Policy](SECURITY.md)
- [Code of Conduct](CODE_OF_CONDUCT.md)

## Running Tests

```bash
pytest -v
```

## License

Released under the [MIT License](LICENSE).
