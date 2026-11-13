---
id: MDA-001
title: Standard user login via menu and session verification
tags: [mda, auth, smoke]
platforms: [android, ios]
targets: [simulator, device]
priority: critical
preconditions:
  - Sauce Labs My Demo App APK installed
reset: clear_data
auto_accept_alerts: true
steps:
  - run_flow:
      flow: mda/login
      params:
        username: "${MDA_USER}"
        password: "${MDA_PASS}"
  - assert_text: {target: mda_catalog.title, equals: "Products"}
  - tap: mda_nav.open_menu
  - assert_visible: mda_nav.menu_logout
  - screenshot: mda_logged_in_menu
teardown:
  - tap_if_visible: mda_nav.menu_logout
  - tap_if_visible: mda_nav.logout_confirm_btn
  - assert_visible: mda_login.title
expected: User signs in cleanly with secret masking/redaction, session shows Log Out in drawer, and teardown signs out.
---

# MDA-001: Standard user login via menu and session verification

Verifies valid authentication via the reusable `mda/login` flow with `secret: true` redaction and clean session teardown.
