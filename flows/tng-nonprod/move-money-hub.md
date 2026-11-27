---
name: tng-nonprod/move-money-hub
platforms: [android]
steps:
  - tap: tng_home.nav_move_money
  - wait_for_visible: {target: tng_common.list_label, params: {text: "Send money"}, timeout: 15}
  - assert_visible: {target: tng_common.list_value, params: {text: "By Interac e-Transfer®"}}
  - assert_visible: {target: tng_common.list_label, params: {text: "Transfer money"}}
  - assert_visible: {target: tng_common.list_value, params: {text: "Between your accounts"}}
  - assert_visible: {target: tng_common.list_label, params: {text: "Deposit cheque"}}
  - assert_visible: {target: tng_common.list_value, params: {text: "Make a deposit or view previous deposits"}}
  - assert_visible: {target: tng_common.list_label, params: {text: "Pay bill"}}
  - assert_visible: {target: tng_common.list_value, params: {text: "Make a bill payment"}}
  - assert_visible: {target: tng_common.list_label, params: {text: "Manage recipients"}}
  - assert_visible: {target: tng_common.list_label, params: {text: "View scheduled payments"}}
  - assert_visible: {target: tng_common.list_label, params: {text: "Manage Autodeposit"}}
  - assert_visible: {target: tng_common.list_value, params: {text: "Set up and manage Autodeposit"}}
  - assert_visible: {target: tng_common.list_label, params: {text: "External accounts"}}
  - assert_visible: {target: tng_common.list_value, params: {text: "Link accounts to move money between financial institutions"}}
  - screenshot: tng_move_money_hub
---

# Move Money tab

Opens the Move Money tab and checks every entry and its subtitle, copied from the
live screen.
