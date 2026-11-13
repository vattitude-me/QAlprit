---
name: mda/login
params: [username, password]
steps:
  - wait_for_visible: mda_nav.open_menu
  - tap: mda_nav.open_menu
  - wait_for_visible: mda_nav.menu_login
  - tap: mda_nav.menu_login
  - wait_for_visible: mda_login.username_field
  - type: {target: mda_login.username_field, value: "${username}"}
  - type: {target: mda_login.password_field, value: "${password}", secret: true}
  - hide_keyboard: true
  - tap: mda_login.login_button
  - assert_visible: mda_catalog.title
---

# Reusable Flow: mda/login

Opens the navigation drawer in My Demo App, navigates to Login, submits credentials with masked password input, and asserts return to the Products catalog.
