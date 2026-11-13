---
name: auth/login
params: [username, password]
steps:
  - launch: app
  - tap_if_visible: onboarding.dismiss_banner
  - wait_for_visible: login.username_field
  - type: {target: login.username_field, value: "${username}"}
  - type: {target: login.password_field, value: "${password}", secret: true}
  - tap: login.login_button
  - assert_visible: accounts.account_list
---

# Reusable Flow: auth/login

Standard login flow shared across smoke and regression specs. 1-level-deep only.
