---
id: TNG-transfer-own-accounts
title: Transfer $1 from debit to savings
tags: [tng, tng-nonprod, move-money, transfers]
platforms: [android]
targets: [device]
reset: none
auto_accept_alerts: false
steps:
  - run_flow: {flow: tng-nonprod/unlock-pin}
  - run_flow: {flow: tng-nonprod/move-money-hub}
  - run_flow:
      flow: tng-nonprod/transfer-between-accounts
      params:
        to_account: Savings Account
        amount: "1"
        amount_display: "$1.00"
        description: QA transfer
  - run_flow: {flow: tng-nonprod/go-home}
expected: The transfer is reviewed and sent with the exact copy, then the app is back on Home.
---

# TNG-transfer-own-accounts

Runs on the installed, logged-in non-prod app. Moves real non-prod money between
the customer's own accounts. Never resets or logs out. Ends on Home.
