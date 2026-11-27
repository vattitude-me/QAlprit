---
name: tng-nonprod/transfer-between-accounts
params: [to_account, amount, amount_display, description]
platforms: [android]
steps:
  - tap: {target: tng_common.list_label, params: {text: "Transfer money"}}
  - assert_text: {target: tng_common.app_bar_title, equals: "Transfer money", timeout: 15}
  - assert_visible: tng_move_money.from_account
  - assert_visible: {target: tng_common.copy, params: {text: "Select account"}}
  - assert_visible: {target: tng_common.copy, params: {text: "Review transfer"}}
  - tap: tng_move_money.to_account
  - wait_for_visible: {target: tng_move_money.account_picker_confirm_btn, timeout: 10}
  - assert_visible: {target: tng_common.copy, params: {text: "Transfer to"}}
  - assert_visible: {target: tng_common.copy, params: {text: "Personal"}}
  - assert_visible: {target: tng_common.copy, params: {text: "Confirm"}}
  - tap: {target: tng_move_money.account_picker_item, params: {name: "${to_account}"}}
  - tap: tng_move_money.account_picker_confirm_btn
  - wait_for_visible: {target: tng_common.copy, params: {text: "${to_account}"}, timeout: 10}
  - assert_text: {target: tng_move_money.amount_label, equals: "Amount (CAD)"}
  - assert_visible: tng_move_money.swap_accounts_btn
  - assert_visible: {target: tng_common.list_label, params: {text: "Schedule transfer"}}
  - assert_visible: {target: tng_common.copy, params: {text: "Description"}}
  - assert_visible: {target: tng_common.copy, params: {text: "0 / 140"}}
  - tap: tng_move_money.submit_btn
  - assert_visible: {target: tng_common.copy, params: {text: "Please enter amount"}}
  - type: {target: tng_move_money.amount_field, value: "${amount}"}
  - type: {target: tng_move_money.description_field, value: "${description}"}
  - hide_keyboard: true
  - screenshot: tng_transfer_form
  - tap: tng_move_money.submit_btn
  - assert_visible: {target: tng_common.copy, params: {text: "Review"}, timeout: 15}
  - assert_visible: {target: tng_payment_review.key_label, params: {label: "Amount"}}
  - assert_visible: {target: tng_payment_review.value, params: {text: "${amount_display}"}}
  - assert_visible: {target: tng_payment_review.key_label, params: {label: "From"}}
  - assert_visible: {target: tng_payment_review.key_label, params: {label: "To"}}
  - assert_visible: {target: tng_payment_review.key_label, params: {label: "Description"}}
  - assert_visible: {target: tng_payment_review.value, params: {text: "${description}"}}
  - assert_visible: {target: tng_payment_review.key_label, params: {label: "When"}}
  - assert_visible: {target: tng_payment_review.value, params: {text: "Today"}}
  - assert_visible: {target: tng_payment_review.key_label, params: {label: "Category"}}
  - assert_text: {target: tng_payment_review.submit_btn, equals: "Send transfer"}
  - screenshot: tng_transfer_review
  - tap: tng_payment_review.submit_btn
  - assert_visible: {target: tng_common.copy, params: {text: "Transfer sent"}, timeout: 20}
  - assert_visible: {target: tng_common.copy, params: {text: "Your funds have been transferred to this account."}}
  - assert_text: {target: tng_payment_review.payee_account, equals: "${to_account}"}
  - assert_visible: {target: tng_payment_review.value, params: {text: "${amount_display}"}}
  - assert_text: {target: tng_payment_review.done_btn, equals: "Done"}
  - screenshot: tng_transfer_sent
  - tap: tng_payment_review.done_btn
  - wait_for_visible: {target: tng_common.list_label, params: {text: "Transfer money"}, timeout: 15}
---

# Transfer between own accounts

Moves money from the debit account to another of the customer's accounts and
checks the form, the empty amount error, the review and the sent screen. The app
is non-prod, so the transfer runs to the end. Copy with a known defect (the
Category value and the description error) is checked in TNG-copy-defects.
