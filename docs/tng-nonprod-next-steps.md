# Tangerine non-prod: next steps

Use this file to pick up the TNG non-prod mapping work. It covers the
`ca.tangerine.mobile.banking.app.nonProd` app on a real Android phone. Branch:
`feat/tng-nonprod-flows`.

## Rules for every session

- Never uninstall, reinstall, clear data or reset the app. Use `reset: none` and pass no app file.
- Never log out, enter MFA, or ask for one-time codes. If the PIN cannot get past a login screen, stop and ask.
- Touch only the non-prod app. Never touch `ca.tangerine.mobile.banking.app`.
- Never tap Close Account. Never save a profile email or phone change, and never submit a valid phone number, because it starts OTP.
- Never type passcode or password attempts in settings.
- Never invent locators. Use `qalprit inspect --platform android --device android_device --env tng`.
- Never loosen an assertion. Report the copy problem as a finding, with a screenshot.
- Keep every exploration screenshot in `results/tng-exploration/`.
- `MP-t-xxxxxxxx` prefixes are dev labels on purpose, not defects.

## Commands

```bash
.venv/bin/qalprit validate --platform android --env tng --tag tng-nonprod
.venv/bin/qalprit run --platform android --device android_device --env tng --tags tng-nonprod --id TNG-settings
```

The secrets `TNG_PIN`, `TNG_PASSWORD` and `TNG_ALIAS_EMAIL` live in `.env`, which is never committed.

## Done

Each of these journeys passed twice on the phone, on 2026-11-27.

| Journey | Workflows | Passing runs |
| --- | --- | --- |
| TNG-home-accounts | home-dashboard | 000248, 004312 |
| TNG-transfer-own-accounts | transfer-between-accounts | 003038, 003300 |
| TNG-bill-pay | bill-pay (Questrade or Rogers payee) | 003853, 004121 |
| TNG-transfer-validation | transfer-validation (negative and edge) | 005649, 005941 |
| TNG-scheduled-transfer | scheduled-transfer (set up, find, cancel) | 010515, 010806 |
| TNG-scheduled-bill-payment | scheduled-bill-payment | 012246, 012701 |
| TNG-spaces | spaces | 014524, 014937 |

Every journey uses `unlock-pin` first and `go-home` last. Run ids are folders under
`results/2026xxxx_<id>_android_tng/`.

The engine changes on this branch are:
- `type` with `keypad: true`, which taps one key per character.
- The validator fills `key` for keypad steps.
- The `matches` description fix in `steps/base.py`.

## In progress: TNG-settings

The workflow is `flows/tng-nonprod/settings.md` and the dataset is
`datasets/flows/tng-nonprod/settings.csv`. Runs 021500 and 022112 failed.

Run 022112 got as far as step 194. That step taps `tng_common.app_bar_back` after the low balance threshold is restored to 10.00. The button was not found within 12 seconds.
- Start with `results/20261127_022112_android_tng/screenshots/TNG-settings/attempt_1/steps_194_tap_FAIL.png` and the XML dump in the same run.
- Likely cause: after Save, the low balance screen has a different back button, or it is already back on Manage notifications.
- Once fixed, get two passing runs.

Phone state after the last session: email unchanged, all notification switches on, both low balance thresholds at 10.00, theme Light, privacy choices unchanged.

## Findings not logged yet

Add these to `results/tng-exploration/notes.md` and then to `docs/tng-nonprod-findings.md`. The numbers are exploration screenshot numbers.

High:
- Email: an empty value (187 to 189) and "abc" (192, 193) skip validation and open the password sheet.
- Personal income (204): four rows show "MP-t-xxxx NOT REQUIRED" with no label.
- Notifications: the Low balance warning description repeats the Mobile wallet text.
- Notifications: the Scheduled payments "Turn off notifications?" dialog uses the FlexiDraft text.

Other:
- Mobile number:
  - The country code shows "+ 1" with a stray space.
- Mailing address:
  - An empty Continue does nothing (197).
  - On the manual form (200 to 202), Continue is disabled with no hint.
  - "Unit number" is not marked optional.
  - "Required field" shows only on Province.
- Personal income:
  - The description has a space before the line break.
  - The settings row reads "Your Personal Income" but the screen reads "Personal income".
  - The row value shows the employer name.
  - The employer phone is shown unformatted.
- Login and security (205 to 212):
  - "Biometric Authentication" is in title case.
  - An empty Change password Continue says "Incorrect password", which may count as an attempt.
  - Manage devices opens a screen titled "Security check".
  - "Last used Tuesday, November 24" shows for the phone in use today.
- Notifications (214 to 227):
  - "Turn Off" is in title case.
  - Mobile wallet and marketing turn off without asking.
  - The row reads "Manage notifications" but the screen reads "Notifications".
  - The low balance label reads "max. $100,000" but the content description reads "max $100,000".
  - Exactly 100,000 saves with no confirmation (218).
  - "TNG Retail Debit " has a trailing space.
- Privacy (231, 232):
  - The screen title reads "Privacy settings" but the row reads "Privacy Settings".
  - "Confirm Choices" and "Reject Optional Items" are in title case.
- Notification History (235):
  - Amounts show as both "CA$1.00" and "$1.00".
  - "Notification History" is in title case.

## Areas not started

Work through these in order. If one is blocked, skip to the next.

1. e-Transfer: send, request, autodeposit, contacts, add or edit a recipient, history, cancel and resend. Use `TNG_ALIAS_EMAIL` for alias and autodeposit registration.
2. Payees: add and remove, using Questrade or Rogers.
3. Cheque deposit and the camera permission, using a `wait_for_user` step for the photo.
4. Account details, transactions, filters, search and statements, beyond what home-dashboard covers.
5. Cards: PIN change, lock and unlock, replace. Look at the screens only, and confirm before any step that cannot be undone.
6. Open an account, products and rate sheets.
7. Help in depth: contact, chat, and the legal and privacy links. The Help screen shows a live customer service code, so `uiautomator dump` fails there. Use `qalprit inspect`.
8. Error and empty states.
9. French, as a separate dataset, if the app offers it.

## Wrap-up tasks

- A `TNG-copy-defects` journey that asserts the expected copy for each finding, so it fails until the app is fixed.
- `docs/tng-nonprod-coverage.md` (each flow's status and why) and `docs/tng-nonprod-findings.md` (screen, expected, actual, screenshot path).
- Notes in `docs/authoring-guide.md` and `CHANGELOG.md` for `keypad: true`.
- Ask the user about:
  - the three deleted old flows (`flows/tng-non-prod-acc-open-2.md`, `-3.md`, `tng-onboarding.md`);
  - the `clear_data` reset in `TNG-002`.
- Open the PR, merge it, then release v0.1.47 under the AGENTS.md rules.
- Play music on the Mac when all flows are done.

## Moving to another machine

Run `.venv/bin/python scripts/export_tng_workspace.py --with-secrets` to build `~/Documents/QAlprit-TNG-workspace`. It holds:
- the config, screens, workflows, journeys and datasets;
- every TNG run, sorted into `results/passed/<journey>/` and `results/failed/<journey>/`;
- the exploration screenshots and a README with the setup steps.

`--with-secrets` copies only the `TNG_*` keys from `.env` into the workspace `.env`, owner-only. Run the script again after new runs to refresh the folder, then copy the whole folder to the other machine.
