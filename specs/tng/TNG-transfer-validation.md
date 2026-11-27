---
id: TNG-transfer-validation
title: Transfer money errors, limits and schedule options
tags: [tng, tng-nonprod, move-money, transfers, negative]
platforms: [android]
targets: [device]
reset: none
auto_accept_alerts: false
steps:
  - run_flow: {flow: tng-nonprod/unlock-pin}
  - run_flow: {flow: tng-nonprod/move-money-hub}
  - run_flow:
      flow: tng-nonprod/transfer-validation
      params:
        from_account: "TNG Retail Debit "
        to_account: Savings Account
        over_amount: "100"
        over_amount_display: "100"
        long_description: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
  - run_flow: {flow: tng-nonprod/go-home}
expected: Each invalid input shows its message or limit, the schedule options show, nothing is sent, and the app is back on Home.
---

# TNG-transfer-validation

Negative and edge checks on Transfer money. Sends nothing. The debit account name
ends with a space in the app, so the parameter keeps it.
