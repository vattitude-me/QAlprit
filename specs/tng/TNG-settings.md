---
id: TNG-settings
title: Profile menu, user settings, notifications, theme, privacy and help
tags: [tng, tng-nonprod, settings, negative]
platforms: [android]
targets: [device]
reset: none
auto_accept_alerts: false
steps:
  - run_flow: {flow: tng-nonprod/unlock-pin}
  - run_flow:
      flow: tng-nonprod/settings
      params:
        invalid_phone: "123"
        invalid_postal: "123"
        threshold: "10.00"
        over_threshold: "200000"
        over_threshold_display: "200,000"
        original_theme: LIGHT
        other_theme: DARK
  - run_flow: {flow: tng-nonprod/go-home}
expected: Every settings screen shows its exact copy and error messages, nothing about the customer changes, the notification switches, low balance threshold and theme end as they started, and the app ends on Home.
---

# TNG-settings

Read only for the customer's details: no phone number, email, address, passcode
or password is saved or typed. Turns one notification switch off and on, saves the
original low balance threshold back, and switches the theme and back. Never logs out.
Ends on Home.
