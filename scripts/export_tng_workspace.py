"""Build a portable QAlprit workspace with the Tangerine non-prod suite.

Usage:
    .venv/bin/python scripts/export_tng_workspace.py [DEST] [--with-secrets]

DEST defaults to ~/Documents/QAlprit-TNG-workspace. The folder gets the config,
locators, workflows, journeys, datasets, every TNG run with an index by journey
and result, the exploration screenshots, and a README.

--with-secrets copies only the TNG_* keys from the repo .env into DEST/.env
(owner-only). Other keys, such as PYPI_TOKEN, are never copied. Without the flag,
DEST/.env.example lists the key names to fill in.

Existing files in DEST are overwritten. Nothing is deleted.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SECRET_PREFIX = "TNG_"
SECRET_KEYS = ["TNG_PIN", "TNG_PASSWORD", "TNG_ALIAS_EMAIL"]

README = """# QAlprit workspace: Tangerine non-prod

This folder is a ready QAlprit workspace for the Tangerine non-prod Android app
(`ca.tangerine.mobile.banking.app.nonProd`).

## Contents

- `config/`: the device profile (`android_device`) and the `tng` environment.
- `screens/screens.yaml`: locators. The TNG groups start with `tng_`.
- `flows/tng-nonprod/`: workflows. `specs/tng/`: journeys. `datasets/flows/tng-nonprod/`: data rows.
- `results/<run-id>/`: earlier runs, listed in the Workbench under Runs & reports.
  `results/RUNS.md` sorts them by journey and result. Each `report.html` holds its own images.
- `results/tng-exploration/`: screenshots and XML from mapping the app, with `notes.md`.
- `results/tng-screen-catalog/index.html`: one screenshot of every screen, grouped by flow and
  marked happy path or failure path. Open it in any browser.
- `.env`: the QA PIN, password and alias email, if they were exported. Keep this folder private.

## Set up on a new machine

1. Install QAlprit: `pip install qalprit`, or install the desktop app.
2. Check the tools: `qalprit doctor`.
3. Connect the Android phone with USB debugging on. The non-prod app must be
   installed and already logged in, with MFA done. The tests never install,
   reset or log out of the app.
4. Check the phone shows up: `qalprit devices`.
5. If `.env` is missing, copy `.env.example` to `.env` and fill in the values.

## Run

From this folder:

```bash
qalprit validate --platform android --env tng --tag tng-nonprod
qalprit run --platform android --device android_device --env tng --tags tng-nonprod --id TNG-home-accounts
```

Leave out `--id` to run every TNG journey. Each journey unlocks with the PIN and
ends on Home, so they run alone and in any order.

To use the Workbench, run `qalprit ui --workspace .` from this folder.

## Notes

- `appium.log` in each run folder has file paths from the machine that ran it.
- The phone must stay unlocked with Do Not Disturb on during a run.
"""


def copy_tree(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, dirs_exist_ok=True)


def sort_runs(dest: Path) -> list[tuple[str, str, str]]:
    """Copy each TNG run to results/<run-id>/, where the Workbench finds it."""
    sorted_runs = []
    for run in sorted((REPO / "results").glob("*_android_tng")):
        data_file = run / "results.json"
        if not data_file.is_file():
            continue
        data = json.loads(data_file.read_text(encoding="utf-8"))
        tests = data.get("tests") or []
        if not tests:
            continue
        journey = tests[0].get("id", "unknown")
        failed = data.get("summary", {}).get("failed", 0) or data.get("interrupted")
        status = "failed" if failed else "passed"
        copy_tree(run, dest / "results" / run.name)
        sorted_runs.append((journey, status, run.name))
    write_index(dest, sorted_runs)
    return sorted_runs


def write_index(dest: Path, runs: list[tuple[str, str, str]]) -> None:
    lines = ["# TNG runs by journey", ""]
    for journey in sorted({j for j, _, _ in runs}):
        lines += [f"## {journey}", "", "| Run | Result |", "| --- | --- |"]
        for j, status, name in runs:
            if j == journey:
                lines.append(f"| [{name}]({name}/report.html) | {status} |")
        lines.append("")
    (dest / "results" / "RUNS.md").write_text("\n".join(lines), encoding="utf-8")


def write_env(dest: Path, with_secrets: bool) -> int:
    (dest / ".env.example").write_text(
        "# QA test values for the Tangerine non-prod app.\n"
        + "".join(f"{key}=\n" for key in SECRET_KEYS),
        encoding="utf-8",
    )
    if not with_secrets:
        return 0
    src = REPO / ".env"
    lines = [
        line
        for line in src.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith(SECRET_PREFIX)
    ]
    target = dest / ".env"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(target, 0o600)
    return len(lines)


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    with_secrets = "--with-secrets" in argv
    dest = Path(args[0]).expanduser() if args else Path.home() / "Documents" / "QAlprit-TNG-workspace"
    dest.mkdir(parents=True, exist_ok=True)

    for name in ("devices.yaml", "environments.yaml", "env.example"):
        (dest / "config").mkdir(exist_ok=True)
        shutil.copy2(REPO / "config" / name, dest / "config" / name)
    (dest / "screens").mkdir(exist_ok=True)
    shutil.copy2(REPO / "screens" / "screens.yaml", dest / "screens" / "screens.yaml")
    copy_tree(REPO / "flows" / "tng-nonprod", dest / "flows" / "tng-nonprod")
    copy_tree(REPO / "datasets" / "flows" / "tng-nonprod", dest / "datasets" / "flows" / "tng-nonprod")
    (dest / "specs" / "tng").mkdir(parents=True, exist_ok=True)
    for spec in sorted((REPO / "specs" / "tng").glob("TNG-*.md")):
        shutil.copy2(spec, dest / "specs" / "tng" / spec.name)
    for doc in ("tng-nonprod-next-steps.md", "tng-nonprod-coverage.md", "tng-nonprod-findings.md"):
        if (REPO / "docs" / doc).is_file():
            (dest / "docs").mkdir(exist_ok=True)
            shutil.copy2(REPO / "docs" / doc, dest / "docs" / doc)

    runs = sort_runs(dest)
    exploration = REPO / "results" / "tng-exploration"
    if exploration.is_dir():
        copy_tree(exploration, dest / "results" / "tng-exploration")
    catalog = REPO / "results" / "tng-screen-catalog"
    if (catalog / "index.html").is_file():
        copy_tree(catalog, dest / "results" / "tng-screen-catalog")

    secrets = write_env(dest, with_secrets)
    (dest / "README.md").write_text(README, encoding="utf-8")
    (dest / ".gitignore").write_text(".env\n", encoding="utf-8")

    print(f"Workspace: {dest}")
    for journey, status, name in runs:
        print(f"  {status:6} {journey:28} {name}")
    print(f"Secrets copied: {secrets} TNG_* keys" if with_secrets else "Secrets: not copied, see .env.example")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
