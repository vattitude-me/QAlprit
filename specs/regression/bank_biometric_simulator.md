---
id: BIO-001
title: Biometric unlock prompt succeeds on simulator
tags: [regression, biometric]
platforms: [android, ios]
targets: [simulator]
priority: medium
reset: clear_data
steps:
  - run_flow:
      flow: auth/login
      params:
        username: "${QA_USER}"
        password: "${QA_PASS}"
  - biometric: enroll
  - background: 2
  - biometric: match
  - assert_visible: accounts.account_list
teardown:
  - tap_if_visible: nav.logout_button
expected: Biometric match unlocks session after backgrounding; automatically skipped on real devices.
---

# BIO-001: Biometric unlock prompt succeeds on simulator

Demonstrates `targets: [simulator]` gating and `biometric` + `background` steps.
