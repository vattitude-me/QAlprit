# QAlprit QA Authoring Guide

This guide explains how QA engineers author, validate, and run automated mobile tests without writing Python code.

---

## 1. Anatomy of a Test Spec (`specs/<suite>/<name>.md`)

Every test lives in a `.md` file under `specs/`. The YAML front matter between `---` markers is executed by `qalprit`; everything below the second `---` is free-form documentation for humans.

### Required & Optional Front Matter Fields

| Field | Required | Default | Notes |
|---|---|---|---|
| `id` | Yes | — | Unique test ID (e.g., `LOGIN-001`) |
| `title` | Yes | — | Short human-readable title |
| `tags` | No | `[]` | Used with `--tags` filtering and report filters |
| `platforms` | No | `[android, ios]` | Platforms this test applies to |
| `targets` | No | `[simulator, device]` | Set `[simulator]` for biometric tests |
| `priority` | No | `medium` | `high`, `medium`, or `low` |
| `preconditions` | No | `[]` | Documented preconditions |
| `reset` | No | `clear_data` | `reinstall`, `clear_data`, or `none` |
| `auto_accept_alerts` | No | profile default | Overrides `devices.yaml` per test |
| `continue_on_failure` | No | `false` | If `false`, remaining `steps` skip on failure while `teardown` still runs |
| `retriable` | No | `true` | Controls automatic retries on transient failures (`--retries N`) |
| `retries` | No | CLI `--retries` | Per-spec retry override (`0`–`5`) |
| `retry_on_assertion` | No | `false` | Allow retrying assertion failures too (off by default to keep assertions deterministic) |
| `setup` | No | `[]` | Steps run before `steps` on each attempt (`phase: setup`) |
| `steps` | Yes | — | Non-empty list of steps (`phase: steps`) |
| `teardown` | No | `[]` | Always runs even if a step above fails (`phase: teardown`) |
| `expected` | No | `""` | Summary of expected outcome shown in reports |

---

## 2. Step Vocabulary

| Step | Parameters | Notes |
|---|---|---|
| `launch` | `app` | Launch or foreground the app under test |
| `reset_app` | none | Apply configured `reset` mid-test; aborts attempt cleanly if reset fails |
| `background` | `seconds` | Background app for N seconds, then foreground |
| `tap` | `target` (or `{target, params?}`) | Auto-waits up to `default_timeout`; retries once on stale element reference |
| `tap_if_visible` | `target`, `timeout?`, `params?` | Short wait (default 2s); no-op if absent |
| `type` | `target`, `value`, `secret?`, `params?` | `secret: true` masks logs/reports and redacts screenshot bounds |
| `clear` | `target`, `params?` | Auto-waits and clears input |
| `swipe` | `direction` (`up\|down\|left\|right`), `target?`, `duration?`, `distance?` | Finger drag on screen or element (`distance` fraction `0 < d < 1`) |
| `scroll_until_visible` | `target`, `direction?`, `max_swipes?` | Scrolls content (`down` default, `max_swipes: 8`) |
| `wait` | `seconds` | Fixed pause; prefer event-driven `wait_for_*` steps |
| `wait_for_visible` | `target`, `timeout?`, `params?` | Explicit wait for visibility up to `timeout` |
| `wait_for_enabled` | `target`, `timeout?`, `params?` | Wait until element is visible and enabled |
| `wait_for_text` | `target`, `equals \| contains \| matches`, `timeout?`, `params?` | Wait until element text satisfies literal/substring/regex check |
| `wait_for_keyboard` | `visible: true \| false`, `timeout?` | Wait until OS soft keyboard appears or dismisses |
| `assert_visible` / `assert_not_visible` | `target`, `timeout?`, `params?` | Auto-waits for visibility state |
| `assert_text` | `target`, `equals \| contains \| matches`, `timeout?` | Auto-waits and verifies element text (exact, substring, or regex) |
| `assert_enabled` / `assert_disabled` | `target`, `timeout?`, `params?` | Assert element enabled/disabled state |
| `back` / `hide_keyboard` | none | Hardware/system back or hide soft keyboard |
| `biometric` | `action: enroll \| match \| fail` | Simulator/emulator only; auto-skipped on real devices |
| `run_flow` | `flow`, `params?` | Expands a 1-level flow from `flows/` |
| `screenshot` | `name` (or `{name, baseline?, threshold?, mask?}`) | Captures screenshot and optionally compares against baseline PNG |
| `visual_compare` | `target?`, `baseline`, `threshold?`, `mask?` | Compares current screenshot (or cropped element) against a stored baseline |

Add `capture: off` to any step when a full screen contains sensitive information and no screenshot should be captured after that step.

---

## 3. Worked Examples

### Example 1: Smoke Launch & Scroll Check

```markdown
---
id: SMOKE-001
title: App launches and renders catalog headline
tags: [smoke, launch]
platforms: [android, ios]
reset: clear_data
steps:
  - launch: app
  - wait_for_visible: {target: catalog.brand_title, timeout: 15}
  - scroll_until_visible: {target: catalog.featured_heading, direction: down, max_swipes: 5}
  - assert_visible: catalog.featured_heading
  - screenshot: catalog_featured
expected: Catalog headline and featured section are visible.
---
```

### Example 2: Login with Secrets, TOTP MFA, Dynamic Locator, and Teardown

```markdown
---
id: LOGIN-001
title: Valid user logs in with TOTP MFA
tags: [regression, login]
platforms: [android, ios]
reset: clear_data
steps:
  - run_flow:
      flow: auth/login
      params:
        username: "${QA_USER}"
        password: "${QA_PASS}"
  - tap_if_visible: mfa.totp_option
  - type: {target: mfa.code_field, value: "${TOTP:QA_TOTP_SECRET}", secret: true}
  - tap:
      target: accounts.account_row
      params: {name: "Checking"}
  - screenshot: checking_details
teardown:
  - tap_if_visible: nav.logout_button
expected: User authenticates with TOTP and opens Checking account.
---
```

### Example 3: Simulator-Only Biometric Resume with `capture: off`

```markdown
---
id: BIO-001
title: Face ID / Fingerprint unlocks session after backgrounding
tags: [regression, biometric]
platforms: [android, ios]
targets: [simulator]
reset: clear_data
steps:
  - run_flow:
      flow: auth/login
      params:
        username: "${QA_USER}"
        password: "${QA_PASS}"
  - biometric: enroll
  - background: 3
  - biometric: match
  - assert_visible: accounts.account_list
  - screenshot: {name: unlocked_accounts, capture: off}
teardown:
  - tap_if_visible: nav.logout_button
---
```

---

## 4. Locators (`screens/screens.yaml`)

- Prefer a single `accessibility_id` shared by Android and iOS.
- Add per-platform overrides (`android:` / `ios:`) or `text:` fallback only when needed.
- Mark any field containing secrets or PII with `sensitive: true` so **every** screenshot captured while it is visible automatically black-fills its bounding box in memory via Pillow.
- Use `qalprit inspect --platform android --device android_emulator --app <path>` to inspect current screen elements and generate starter YAML.

---

## 5. Important Platform Caveat: iOS Simulator Keychain Reset

On Android, `reset: clear_data` executes `adb shell pm clear <android_package>`, which wipes app preferences, databases, and stored tokens.

On **iOS Simulators**, `clear_data` terminates, uninstalls, and reinstalls the `.app` container. However, **the iOS Simulator Keychain persists across app uninstalls**. If a test stores authentication tokens or biometric enrollment flags in the Keychain and expects a completely clean login state, either:

1. Use a QA build debug reset hook / logout in `teardown:`, or
2. Erase the simulator between runs (`xcrun simctl erase <udid>`).

---

## 6. Retries, Failure Taxonomy & Visual Baselines

- **Transient-Only Retries:** With `--retries N` (or per-spec `retries:`), QAlprit retries transient failures (`locator_not_found`, `locator_timeout`, `stale_element`, `driver_session_error`, `app_launch_failure`) by default and stops immediately on deterministic assertion failures unless `retry_on_assertion: true` is set.
- **Locator Single-Strategy Rule:** Each platform locator block in `screens/screens.yaml` should define only one primary selector key (`accessibility_id`, `id`, `text`, or `xpath`) per target/override.
- **Visual Baselines:** Run once with `--update-baselines` to record PNG baselines (`baselines/`), then omit `--update-baselines` in CI to assert visual similarity within `threshold` (with optional `mask:` targets or boxes to ignore dynamic clocks/avatars).
