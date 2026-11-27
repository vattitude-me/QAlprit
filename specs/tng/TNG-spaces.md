---
id: TNG-spaces
title: Create, fund, manage and archive a Space
tags: [tng, tng-nonprod, spaces, negative]
platforms: [android]
targets: [device]
reset: none
auto_accept_alerts: false
steps:
  - run_flow: {flow: tng-nonprod/unlock-pin}
  - run_flow:
      flow: tng-nonprod/spaces
      params:
        account_name: Savings Account
        long_name: "QA Space AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        long_name_display: "QA Space AAAAAAAAAAAAAAAAAAAAA"
        space_name: QA Space
        renamed_name: QA Space renamed
        over_amount: "5"
        amount: "1"
        amount_display: "$1.00"
        goal_amount: "100"
        goal_display: "$100.00"
        goal_away_display: "$99.00"
  - run_flow: {flow: tng-nonprod/go-home}
expected: A Space is created, funded, renamed, given a goal and archived with the exact copy, the money goes back to the account, and the app ends on Home.
---

# TNG-spaces

Uses the Savings Account. Leaves one archived Space per run, because the app
cannot delete archived Spaces. Never resets or logs out. Ends on Home.
