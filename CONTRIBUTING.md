# Contributing to QAlprit

Thanks for helping improve **QAlprit**! All core runner, step, redaction, validation, inspector, reporting, and CLI tests run **offline in Python** using `FakeDriver` — no physical device, emulator, or Appium server is required for day-to-day code contributions.

---

## 1. Local Development Setup

```bash
git clone https://github.com/vattitude-me/QAlprit.git
cd QAlprit
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

For validating the sample suites locally:

```bash
cp config/env.example .env
```

> **Important:** Never commit `.env`, real credentials, app binaries (`apps/`), or raw test run outputs (`results/`).

---

## 2. Verification Checks

Run these before opening a Pull Request:

```bash
# 1. Full unit test suite (runs offline via FakeDriver + TestRunner mocks)
.venv/bin/pytest -v

# 2. Validate sample suites and specs
.venv/bin/qalprit validate --suite mda --env mda
.venv/bin/qalprit validate --suite regression --env qa
.venv/bin/qalprit list --suite mda

# 3. Verify web script syntax (when editing web/app.js)
node --check web/app.js
```

---

## 3. Adding a New Step Action

Every step action in QAlprit follows the same four-point pattern:

1. **Vocabulary & Normalization (`src/qalprit/spec_loader.py`)**
   Add the action name to `STEP_VOCABULARY` and handle any YAML shorthand syntax in `normalize_step()`.
2. **Executor Implementation (`src/qalprit/steps/`)**
   Implement `exec_<action>(ctx: StepContext, step: StepItem) -> dict[str, Any]` using `wait_for_element`, `perform_with_stale_retry`, `StepFailureError`, or `StepTimeoutError` from `src/qalprit/steps/base.py`, then register it in `STEP_EXECUTORS` (`src/qalprit/steps/__init__.py`).
3. **Static Validation (`src/qalprit/validator.py`)**
   Validate target names, parameter types, regex syntax, and numeric bounds so misconfigurations fail early in `qalprit validate`.
4. **Tests & Documentation**
   Add deterministic unit tests in `tests/` using `FakeDriver` / `FakeElement` (`tests/fake_driver.py`) and document the step in `docs/authoring-guide.md`.

---

## 4. Pull Request Guidelines

- Branch off `main` (`feat/...`, `fix/...`, `docs/...`) — do not commit directly to `main`.
- Keep changes focused and backward-compatible with existing Markdown specs (`specs/**/*.md`), flows (`flows/**/*.md`), and `results.json` schemas (`1.0` and `1.1`).
- If you modify `src/qalprit/report/templates/report.html.j2` or `src/qalprit/report/generator.py`, make sure `examples/mda-sample-run/report.html` (`home_url=None`) and `web/sample-report.html` (`home_url="/"`) render cleanly and never inject `data:image/` URIs when `--external-assets` / `external_assets=True` is set.
- Preserve the `Managed by vattitude.ca` footer across report templates, workbench UI, and landing pages.
