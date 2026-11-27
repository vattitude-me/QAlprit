---
name: tng-nonprod/unlock-pin
platforms: [android]
steps:
  - if_visible:
      target: tng_debug.apply_and_continue_btn
      timeout: 8
      then:
        - tap: tng_debug.apply_and_continue_btn
  - if_visible:
      target: tng_pin.title
      timeout: 6
      then:
        - assert_text: {target: tng_pin.title, equals: "Enter your 4-digit passcode"}
        - assert_text: {target: tng_pin.reset_passcode_btn, equals: "Reset passcode"}
        - type: {target: tng_pin.key, value: "${TNG_PIN}", secret: true, keypad: true}
  # The app may resume on any screen. Go back until the tab bar shows, then open Home.
  - repeat:
      until: tng_home.nav_home
      max: 6
      until_timeout: 8
      steps:
        - back: true
  - tap: tng_home.nav_home
  - wait_for_visible: {target: tng_home.personal_accounts_title, timeout: 20}
---

# Unlock the Tangerine non-prod app

Starts every TNG journey. A cold start shows the non-prod Debug Menu, which this
continues past without changing any switch. If the passcode screen shows, it checks
the copy and types `TNG_PIN` on the app's number pad, masked in reports. If the app
is already unlocked, both blocks are skipped. It then goes back to the Home tab
from whatever screen the app resumed on. Never logs in or resets the app.
