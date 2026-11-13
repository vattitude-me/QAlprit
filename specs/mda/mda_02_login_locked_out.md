---
id: MDA-002
title: Locked-out user sees account locked error banner
tags: [mda, auth, negative]
platforms: [android]
targets: [simulator, device]
priority: high
preconditions:
  - Sauce Labs My Demo App APK installed
reset: clear_data
auto_accept_alerts: true
steps:
  - wait_for_visible: mda_nav.open_menu
  - tap: mda_nav.open_menu
  - tap: mda_nav.menu_login
  - wait_for_visible: mda_login.username_field
  - type: {target: mda_login.username_field, value: "${MDA_LOCKED_USER}"}
  - type: {target: mda_login.password_field, value: "${MDA_PASS}", secret: true}
  - hide_keyboard: true
  - tap: mda_login.login_button
  - assert_visible: mda_login.error_banner
  - assert_text: {target: mda_login.error_banner, contains: "locked out"}
  - screenshot: mda_locked_out_error
expected: Attempting to log in as a locked-out user remains on Login and displays the locked-out error message.
---

# MDA-002: Locked-out user sees account locked error banner

Negative authentication check confirming error handling for locked accounts.
