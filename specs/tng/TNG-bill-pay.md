---
id: TNG-bill-pay
title: Pay $1 to an existing bill payee
tags: [tng, tng-nonprod, move-money, bill-pay]
platforms: [android]
targets: [device]
reset: none
auto_accept_alerts: false
steps:
  - run_flow: {flow: tng-nonprod/unlock-pin}
  - run_flow: {flow: tng-nonprod/move-money-hub}
  - run_flow:
      flow: tng-nonprod/bill-pay
      params:
        payee_name: QUESTRADE INC
        amount: "1"
        amount_display: "$1.00"
  - run_flow: {flow: tng-nonprod/go-home}
expected: The bill payment is reviewed and sent with the exact copy, then the app is back on Home.
---

# TNG-bill-pay

Needs the bill payee QUESTRADE INC on the customer profile (added once during
exploration). Never resets or logs out. Ends on Home.
