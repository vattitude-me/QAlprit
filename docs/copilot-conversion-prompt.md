# Copilot Confluence-to-QAlprit Conversion Prompt

Use this prompt in GitHub Copilot Chat to convert existing Confluence manual test plans into `qalprit` Markdown specs.

## Prompt Template

```text
Convert the following test case into a Markdown file with YAML front matter using the QAlprit spec schema:
- Required front matter: id, title, tags, platforms ([android, ios]), reset (clear_data | reinstall | none), steps, expected.
- Optional front matter: targets ([simulator, device]), priority, preconditions, auto_accept_alerts, teardown.

Rules:
- Use only step types from the step vocabulary:
  launch, reset_app, background, tap, tap_if_visible, type, clear, swipe,
  scroll_until_visible, wait, wait_for_visible, assert_visible,
  assert_not_visible, assert_text, back, hide_keyboard, biometric,
  run_flow, screenshot.
- Reuse existing flows from flows/ via run_flow where appropriate (e.g., auth/login) instead of repeating login steps. Flows are 1-level-deep only.
- Use logical locator names in the form screen.element (snake_case). Do not invent accessibility IDs; list any new locator names in a "# New Locators Needed" section at the bottom.
- Replace credentials with ${ENV_VAR} (or ${TOTP:VAR} for authenticator codes) and set secret: true on password/PIN/OTP fields.
- Put cleanup steps (e.g., unfreezing a card, logging out) in teardown:.
- Add targets: [simulator] if the test uses biometric steps.
- Put anything ambiguous or missing in a "# Questions" section instead of guessing.

Test case:
<paste Confluence content>
```

## QA Review Checklist (before merging into `specs/`)

1. All `# Questions` items resolved with product/dev.
2. Every `# New Locators Needed` entry verified with `qalprit inspect` and added to `screens/screens.yaml`.
3. `secret: true` set on all password/PIN/OTP `type` steps, and `sensitive: true` set on sensitive fields in `screens/screens.yaml`.
4. `qalprit validate --env qa` passes with zero errors.
5. `teardown:` restores test data / logs out cleanly even on failure.
