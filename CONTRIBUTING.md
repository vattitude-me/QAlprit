# Contributing to QAlprit

Thank you for helping improve **QAlprit** (*Mobile QA. Bugs caught with proof.*)!

## Reporting Bugs

Please open a GitHub issue using the **Bug Report** template and include:
- `qalprit --version` and `qalprit doctor --json-output`
- OS, Python version, Appium 2 version, and target (`android` / `ios`, emulator / simulator / device)
- Minimal reproduction spec/flow (with all secrets removed!)

## Suggesting Features

Use the **Feature Request** template and keep QAlprit's core principles in mind:
1. **Zero runtime LLM calls** — test execution must remain 100% deterministic.
2. **Cross-platform Python (`pathlib`)** — support macOS, Linux, and Windows (Android).
3. **Security-first evidence** — never write raw secrets or unredacted sensitive pixels to disk.
4. **Short dependency list** — prefer Python stdlib where practical.

## Development Workflow

1. Fork the repository and create a feature branch off `main` (never commit directly to `main`).
2. Set up a local virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -e ".[dev]"
   ```
3. Make focused changes and add unit tests using `tests/fake_driver.py` where applicable.
4. Verify before opening a Pull Request:
   ```bash
   pytest -v
   cp config/env.example .env
   qalprit validate --suite mda --env mda
   qalprit validate --suite regression --env qa
   ```
5. Open a Pull Request targeting `main` and complete the PR checklist.
