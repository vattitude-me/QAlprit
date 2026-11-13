---
id: LOGIN-001
title: Valid user can log in with TOTP MFA
tags: [regression, login, security]
platforms: [android, ios]
targets: [simulator, device]
priority: high
preconditions:
  - Fresh install
  - Test user QA_USER exists
reset: clear_data
auto_accept_alerts: true
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
  - screenshot: post_login
teardown:
  - tap_if_visible: nav.logout_button
expected: User authenticates with password + RFC 6238 TOTP and lands on Checking account.
---

# LOGIN-001: Valid user can log in with TOTP MFA

Reference specification demonstrating reusable 1-level flows, `${VAR}` substitution, `${TOTP:VAR}`, `secret: true`, parameterized locators, and guaranteed teardown.
