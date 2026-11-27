---
name: tng-nonprod/home-dashboard
params: [debit_account_name, savings_account_name]
platforms: [android]
steps:
  - wait_for_visible: {target: tng_home.personal_accounts_title, timeout: 15}
  - assert_visible: tng_home.greeting
  - assert_text: {target: tng_home.personal_accounts_title, equals: "Personal accounts"}
  - assert_visible: {target: tng_home.account_card, params: {name: "${debit_account_name}"}}
  - assert_visible: {target: tng_home.account_card, params: {name: "${savings_account_name}"}}
  - assert_text: {target: tng_home.open_account_label, equals: "Open Account"}
  - assert_text:
      target: tng_home.cdic_disclaimer
      equals: "Tangerine Bank is a wholly-owned subsidiary of The Bank of Nova Scotia and a member of the Canada Deposit Insurance Corporation (CDIC) in its own right."
  - assert_visible: tng_home.cdic_logo
  - assert_visible: tng_home.cdic_info_link
  - assert_visible: tng_home.profile_btn
  - assert_visible: tng_home.nav_home
  - assert_visible: tng_home.nav_move_money
  - assert_visible: tng_home.nav_cards
  - screenshot: tng_home
---

# Home dashboard copy

Checks the logged-in Home screen: greeting, Personal accounts list, Open Account,
the CDIC disclaimer and the bottom navigation. Account names come from the test
data because they are the customer's own display names.
