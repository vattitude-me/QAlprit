# QAlprit AI Agent Instructions

Instead of running every QAlprit command and debugging Appium XML trees by hand, let your AI coding agent (Antigravity, Claude Code, Codex, Cursor, or GitHub Copilot) run and evolve the suite for you.

---

## Quick Start: Copy-Paste Prompt for Your AI Assistant

Paste this into your AI assistant after placing your app in `apps/` and copying `config/env.example` to `.env`:

```text
Follow docs/agent-instructions.md to run QAlprit against my app at apps/<your-app.apk-or-.app> on <android|ios>.
Check tools and devices, align config/devices.yaml and config/environments.yaml with the app binary, inspect screens, validate the suite, run the tests, fix any broken locators or keyboard occlusion issues (without hiding real app bugs), re-run until green, and give me the link to results/<run-id>/report.html and summary.md.
```

---

## Agent Operating Instructions

When a user asks you to run, set up, or update QAlprit tests for an app, act as an autonomous mobile QA operator using the loop below.

### 1. Preflight & Binary Alignment

1. Check environment readiness and available phones/simulators:
   ```bash
   qalprit doctor
   qalprit devices
   ```
2. Verify `.env` exists (never print or commit `.env` values) and check target config matches the app binary:
   - **iOS `.app`**: Read the bundle ID with `/usr/libexec/PlistBuddy -c "Print :CFBundleIdentifier" "<app-path>/Info.plist"` and match `ios_bundle_id` in `config/environments.yaml`, plus `device_name` / `platform_version` in `config/devices.yaml`.
   - **Android `.apk`**: Verify `android_package` in `config/environments.yaml` and `device_name` / `avd` / `udid` in `config/devices.yaml`.
   - If macOS CoreSimulator or `sudo` permissions block device listing, stop and give the user the exact commands to run, then resume once confirmed.

### 2. Inspect Before Authoring or Fixing Locators

- Never guess `accessibility_id` names, labels, or XPaths.
- Capture live screen structure with:
  ```bash
  qalprit inspect --platform <android|ios> --device <profile> --app <app-path> --screen <screen_name>
  ```
- Maintain `screens/screens.yaml` cleanly:
  - Prefer a shared `accessibility_id` when both platforms expose the same ID.
  - When Android and iOS differ, keep `android:` and `ios:` blocks independent under the same logical locator key — never break a passing platform override to fix the other.
  - For unlabelled iOS text fields beside a label, target relative to the label (for example `xpath: //XCUIElementTypeStaticText[@name="Zip Code*"]/following::XCUIElementTypeTextField[1]`).
  - Keep `sensitive: true` on passwords, cards, CVVs, PINs, OTPs, and PII fields, and `secret: true` on sensitive `type` steps in specs.

### 3. Validate Before Running on Device

Always run static validation scoped to the target suite/platform before starting an Appium session:

```bash
qalprit validate --suite <suite> --env <env> --platform <android|ios>
```

Fix any missing locators, invalid step names, nested flow calls (flows are 1 level deep), or missing `.env` variables before running `qalprit run`.

### 4. Execute & Triage Failures Automatically

Run the suite:

```bash
qalprit run --platform <android|ios> --device <profile> --app <app-path> --env <env> --suite <suite>
```

When any spec fails, read:
- `results/<run-id>/summary.md`
- `results/<run-id>/screenshots/<SPEC_ID>/attempt_*/*_FAIL.png`
- `results/<run-id>/page_sources/<SPEC_ID>/attempt_*/*_FAIL.xml`

Classify the failure and respond accordingly:

| Failure Type | How to Spot It | How the Agent Should Evolve the Suite |
|---|---|---|
| **Wrong / Changed Locator** | Element missing from XML or different attribute on this OS/version | Update only the affected platform (`android:` or `ios:`) in `screens/screens.yaml` from `*_FAIL.xml` or `qalprit inspect`. |
| **iOS Keyboard Covering Fields** | `XCUIElementTypeKeyboard` overlaps a lower field (`visible="false"` in `*_FAIL.xml`) | Insert `- hide_keyboard: true` (or `scroll_until_visible`) after typing into the field above it. |
| **Platform Formatting Difference** | Semantic match with different punctuation/spacing (e.g., `$29.99` vs `$ 29.99`) | Switch brittle `equals:` text checks to `contains:` when valid on both OSes. |
| **Platform-Only Fixture or Flow** | A test user, screen, or OS feature only exists on Android or iOS | Scope that spec with `platforms: [android]` or `platforms: [ios]` and explain why. |
| **Real App Bug** | Wrong data, crash, missing screen, broken button, or wrong sort order | **Do not weaken assertions to pass.** Report the bug to the user with the `*_FAIL.png` screenshot and `report.html`. |

### 5. Safety Boundaries

- **Never delete data or erase devices silently**: Ask the user before running destructive cleanup commands (`rm`, `xcrun simctl erase <udid>`, `git reset`).
- **Keep deterministic execution intact**: Use QAlprit's fixed step schema (`launch`, `tap`, `type`, `hide_keyboard`, `assert_visible`, etc.) in `specs/` and `flows/` — AI helps maintain specs between runs, while `qalprit run` executes deterministically without AI guessing mid-test.
- **Verify after every edit**: Re-run `qalprit validate` and `qalprit run`, and present exact `<run-id>`, pass/fail summary, and paths to `results/<run-id>/report.html` and `results/<run-id>/summary.md`.
