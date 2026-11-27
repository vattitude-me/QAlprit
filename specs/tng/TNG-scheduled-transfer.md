---
id: TNG-scheduled-transfer
title: Schedule a monthly $1 transfer, find it and cancel it
tags: [tng, tng-nonprod, move-money, transfers, scheduled]
platforms: [android]
targets: [device]
reset: none
auto_accept_alerts: false
steps:
  - run_flow: {flow: tng-nonprod/unlock-pin}
  - run_flow: {flow: tng-nonprod/move-money-hub}
  - run_flow:
      flow: tng-nonprod/scheduled-transfer
      params:
        from_account: "TNG Retail Debit "
        to_account: Savings Account
        amount: "1"
        amount_display: "$1.00"
        description: QA scheduled
  - run_flow: {flow: tng-nonprod/go-home}
expected: The monthly transfer is scheduled, shows in Scheduled payments with the right details, is cancelled, and the app is back on Home.
---

# TNG-scheduled-transfer

Creates a real non-prod scheduled transfer and cancels it in the same run, so
nothing stays scheduled.
