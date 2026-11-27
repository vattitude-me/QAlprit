---
id: TNG-home-accounts
title: Home dashboard copy on the logged-in non-prod app
tags: [tng, tng-nonprod, home]
platforms: [android]
targets: [device]
reset: none
auto_accept_alerts: false
steps:
  - run_flow: {flow: tng-nonprod/unlock-pin}
  - run_flow:
      flow: tng-nonprod/home-dashboard
      params:
        debit_account_name: TNG Retail Debit
        savings_account_name: Savings Account
expected: Home shows the greeting, both accounts, Open Account and the CDIC disclaimer with the exact copy.
---

# TNG-home-accounts

Runs on the app already installed and logged in. Never resets or logs out. Ends on Home.
