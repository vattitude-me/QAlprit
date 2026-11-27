---
id: TNG-scheduled-bill-payment
title: Schedule, edit and cancel a monthly bill payment
tags: [tng, tng-nonprod, move-money, bill-pay, scheduled, negative]
platforms: [android]
targets: [device]
reset: none
auto_accept_alerts: false
steps:
  - run_flow: {flow: tng-nonprod/unlock-pin}
  - run_flow: {flow: tng-nonprod/move-money-hub}
  - run_flow:
      flow: tng-nonprod/scheduled-bill-payment
      params:
        from_account: "TNG Retail Debit "
        payee_name: QUESTRADE INC
        amount: "1"
        amount_display: "$1.00"
        over_amount: "100"
        increased_amount: "2"
  - run_flow: {flow: tng-nonprod/go-home}
expected: The bill form rejects $0 and an amount over the balance, a monthly payment is scheduled, the increase is refused, and the payment is cancelled. The app ends on Home.
---

# TNG-scheduled-bill-payment

Needs the bill payee QUESTRADE INC and `TNG_PASSWORD` in the workspace `.env`.
Never resets or logs out. Ends on Home with nothing scheduled.
