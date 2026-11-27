---
name: tng-nonprod/scheduled-transfer
params: [from_account, to_account, amount, amount_display, description]
platforms: [android]
steps:
  - tap: {target: tng_common.list_label, params: {text: "Transfer money"}}
  - assert_text: {target: tng_common.app_bar_title, equals: "Transfer money", timeout: 15}
  - tap: tng_move_money.to_account
  - wait_for_visible: {target: tng_move_money.account_picker_confirm_btn, timeout: 10}
  - tap: {target: tng_move_money.account_picker_item, params: {name: "${to_account}"}}
  - tap: tng_move_money.account_picker_confirm_btn
  - wait_for_visible: {target: tng_move_money.to_account_name, params: {name: "${to_account}"}, timeout: 10}
  - type: {target: tng_move_money.amount_field, value: "${amount}"}
  - hide_keyboard: true
  - set_toggle: {target: tng_move_money.schedule_toggle, state: "on"}
  - tap: tng_move_money.schedule_frequency
  - tap: {target: tng_custom_frequency.frequency_option, params: {option: "Monthly"}, timeout: 10}
  - wait_for_visible: tng_move_money.schedule_ending
  # A scheduled transfer needs a description.
  - tap: tng_move_money.submit_btn
  - assert_visible: {target: tng_common.copy, params: {text: "Please enter payment reference"}}
  - type: {target: tng_move_money.description_field, value: "${description}"}
  - hide_keyboard: true
  - screenshot: tng_scheduled_form
  - tap: tng_move_money.submit_btn
  - assert_visible: {target: tng_common.copy, params: {text: "Review"}, timeout: 15}
  - assert_visible: {target: tng_payment_review.value, params: {text: "${amount_display}"}}
  - assert_visible: {target: tng_payment_review.value, params: {text: "${description}"}}
  - assert_visible: {target: tng_payment_review.key_label, params: {label: "When"}}
  - assert_visible: {target: tng_payment_review.key_label, params: {label: "Frequency"}}
  - assert_visible: {target: tng_payment_review.value, params: {text: "Monthly"}}
  - assert_visible: {target: tng_payment_review.key_label, params: {label: "Ending"}}
  - assert_visible: {target: tng_payment_review.value, params: {text: "When cancelled"}}
  - assert_text: {target: tng_payment_review.submit_btn, equals: "Confirm transfer"}
  - screenshot: tng_scheduled_review
  - tap: tng_payment_review.submit_btn
  - assert_visible: {target: tng_common.copy, params: {text: "Transfer scheduled"}, timeout: 25}
  - assert_visible: {target: tng_common.copy, params: {text: "Your funds will be transferred to this account on your chosen date."}}
  - assert_text: {target: tng_payment_review.payee_account, equals: "${to_account}"}
  - assert_visible: {target: tng_payment_review.value, params: {text: "Monthly"}}
  - screenshot: tng_scheduled_done
  - tap: tng_payment_review.done_btn
  # Find it in the scheduled payments list.
  - tap: {target: tng_common.list_label, params: {text: "View scheduled payments"}, timeout: 15}
  - assert_text: {target: tng_common.app_bar_title, equals: "Scheduled payments", timeout: 15}
  - tap: {target: tng_common.list_label, params: {text: "${from_account}"}}
  - assert_text: {target: tng_scheduled.sort_az_chip, equals: "A-Z", timeout: 15}
  - assert_text: {target: tng_scheduled.sort_type_chip, equals: "Type"}
  - assert_text: {target: tng_scheduled.section_header, equals: "Account transfer"}
  - assert_text: {target: tng_scheduled.item_amount, equals: "${amount_display}"}
  - assert_text: {target: tng_scheduled.item_type, equals: "Account transfer"}
  - screenshot: tng_scheduled_list
  - tap: {target: tng_scheduled.item_name_contains, params: {text: "(${to_account})"}}
  # Detail.
  - assert_visible: {target: tng_common.copy, params: {text: "Scheduled transfer"}, timeout: 15}
  - assert_text: {target: tng_scheduled.payee_header, equals: "Transferring to"}
  - assert_text: {target: tng_scheduled.card_description, equals: "${to_account}"}
  - assert_text: {target: tng_scheduled.details_header, equals: "Transfer details"}
  - assert_visible: {target: tng_scheduled.field_label, params: {label: "Amount"}}
  - assert_visible: {target: tng_scheduled.field_value, params: {text: "${amount_display}"}}
  - assert_visible: {target: tng_scheduled.field_value, params: {text: "${description}"}}
  - assert_visible: {target: tng_scheduled.field_label, params: {label: "Frequency"}}
  - assert_visible: {target: tng_scheduled.field_value, params: {text: "Monthly"}}
  - assert_visible: {target: tng_scheduled.field_label, params: {label: "Ending"}}
  - assert_visible: {target: tng_scheduled.field_value, params: {text: "When cancelled"}}
  - assert_text: {target: tng_scheduled.cancel_btn, equals: "Cancel scheduled transfer"}
  - assert_text: {target: tng_scheduled.next_header, equals: "Next transfer"}
  - assert_visible: tng_scheduled.history_header
  - screenshot: tng_scheduled_detail
  # Cancel it, so the journey leaves nothing behind.
  - tap: tng_scheduled.cancel_btn
  - wait_for_text: {target: tng_scheduled.screen_title, equals: "Things to know before you cancel", timeout: 15}
  - assert_visible: {target: tng_scheduled.bullet, params: {text: "Your transfer will be cancelled immediately."}}
  - assert_visible: {target: tng_scheduled.bullet, params: {text: "If you change your mind, you'll need to set up a new transfer."}}
  - assert_text: {target: tng_scheduled.continue_header, equals: "Transfer recipient"}
  - assert_text: {target: tng_scheduled.card_description, equals: "${to_account}"}
  - screenshot: tng_scheduled_cancel
  - tap: tng_scheduled.cancel_btn
  - wait_for_text: {target: tng_scheduled.screen_title, matches: '^Your scheduled transfer to .+ has been cancelled\.$', timeout: 20}
  - screenshot: tng_scheduled_cancelled
  - tap: tng_scheduled.result_btn
  - wait_for_visible: {target: tng_common.app_bar_title, timeout: 15}
  - assert_not_visible: {target: tng_scheduled.item_name_contains, params: {text: "(${to_account})"}}
---

# Scheduled transfer: set up, find and cancel

Sets up a monthly transfer between the customer's own accounts, checks the
review and confirmation, finds it under View scheduled payments, checks the
detail screen and cancels it. The journey ends with nothing scheduled, so it can
run again in any order.

Known findings, checked in TNG-copy-defects: "Reference" vs "Description",
"Past payments" vs "Past transfers", the "Cancel Transfer" and "Got It" button
case, the date format, and the swapped empty state heading and text.
