---
name: tng-nonprod/transfer-validation
params: [from_account, to_account, over_amount, over_amount_display, long_description]
platforms: [android]
steps:
  - tap: {target: tng_common.list_label, params: {text: "Transfer money"}}
  - assert_text: {target: tng_common.app_bar_title, equals: "Transfer money", timeout: 15}
  # No destination account: the form stays and shows no message (finding).
  - tap: tng_move_money.submit_btn
  - assert_visible: {target: tng_common.copy, params: {text: "Select account"}}
  - assert_text: {target: tng_common.app_bar_title, equals: "Transfer money"}
  - tap: tng_move_money.to_account
  - wait_for_visible: {target: tng_move_money.account_picker_confirm_btn, timeout: 10}
  - tap: {target: tng_move_money.account_picker_item, params: {name: "${to_account}"}}
  - tap: tng_move_money.account_picker_confirm_btn
  - wait_for_visible: {target: tng_move_money.to_account_name, params: {name: "${to_account}"}, timeout: 10}
  # Zero amount.
  - type: {target: tng_move_money.amount_field, value: "0"}
  - assert_text: {target: tng_move_money.amount_field, equals: "0"}
  - hide_keyboard: true
  - tap: tng_move_money.submit_btn
  - assert_visible: {target: tng_common.copy, params: {text: "Please enter amount"}}
  - screenshot: tng_transfer_amount_zero
  # More than the available balance, sent now.
  - type: {target: tng_move_money.amount_field, value: "${over_amount}"}
  - assert_text: {target: tng_move_money.amount_field, equals: "${over_amount_display}"}
  - type: {target: tng_move_money.description_field, value: "QA over balance"}
  - hide_keyboard: true
  - tap: tng_move_money.submit_btn
  - assert_visible: {target: tng_common.copy, params: {text: "Amount is higher than your available balance."}}
  - assert_text: {target: tng_common.app_bar_title, equals: "Transfer money"}
  - screenshot: tng_transfer_over_balance
  # Input limits: two decimals, 140 characters.
  - type: {target: tng_move_money.amount_field, value: "1.239"}
  - assert_text: {target: tng_move_money.amount_field, equals: "1.23"}
  - type: {target: tng_move_money.description_field, value: "${long_description}"}
  - hide_keyboard: true
  - assert_visible: {target: tng_common.copy, params: {text: "140 / 140"}}
  # Swap the accounts and back.
  - tap: tng_move_money.swap_accounts_btn
  - assert_visible: {target: tng_move_money.from_account_name, params: {name: "${to_account}"}}
  - assert_visible: {target: tng_move_money.to_account_name, params: {name: "${from_account}"}}
  - tap: tng_move_money.swap_accounts_btn
  - assert_visible: {target: tng_move_money.from_account_name, params: {name: "${from_account}"}}
  # Schedule options.
  - set_toggle: {target: tng_move_money.schedule_toggle, state: "on"}
  - assert_visible: tng_move_money.schedule_when
  - assert_visible: {target: tng_common.copy, params: {text: "When"}}
  - assert_visible: {target: tng_common.copy, params: {text: "Frequency"}}
  - assert_visible: {target: tng_common.copy, params: {text: "Once"}}
  - tap: tng_move_money.schedule_frequency
  - assert_visible: {target: tng_custom_frequency.frequency_option, params: {option: "Daily"}, timeout: 10}
  - assert_visible: {target: tng_custom_frequency.frequency_option, params: {option: "Weekly"}}
  - assert_visible: {target: tng_custom_frequency.frequency_option, params: {option: "Monthly"}}
  - assert_visible: {target: tng_custom_frequency.frequency_option, params: {option: "Annually"}}
  - assert_visible: {target: tng_custom_frequency.frequency_option, params: {option: "Custom"}}
  - screenshot: tng_transfer_frequency_options
  - tap: {target: tng_custom_frequency.frequency_option, params: {option: "Monthly"}}
  - wait_for_visible: tng_move_money.schedule_ending
  - assert_visible: {target: tng_common.copy, params: {text: "Ending"}}
  - assert_visible: {target: tng_common.copy, params: {text: "When cancelled"}}
  # Custom frequency screen.
  - tap: tng_move_money.schedule_ending
  - assert_text: {target: tng_custom_frequency.repeats_heading, equals: "Repeats", timeout: 10}
  - assert_text: {target: tng_custom_frequency.every_label, equals: "Every"}
  - assert_text: {target: tng_custom_frequency.interval_field, equals: "1"}
  - assert_text: {target: tng_custom_frequency.period_text, equals: "month"}
  - assert_text: {target: tng_custom_frequency.ends_heading, equals: "Ending"}
  - assert_text: {target: tng_custom_frequency.until_cancelled_label, equals: "When cancelled"}
  - assert_text: {target: tng_custom_frequency.date_label, equals: "On"}
  - assert_visible: tng_custom_frequency.date_text
  - assert_text: {target: tng_custom_frequency.done_btn, equals: "Done"}
  - type: {target: tng_custom_frequency.interval_field, value: "2"}
  - assert_text: {target: tng_custom_frequency.period_text, equals: "months"}
  - tap: tng_custom_frequency.num_payments_radio
  # The "After 1 payment" pre-label is a finding, so only its presence is checked here.
  - assert_visible: tng_custom_frequency.num_payments_pre_label
  - type: {target: tng_custom_frequency.num_payments_field, value: "0"}
  - hide_keyboard: true
  - tap: tng_custom_frequency.done_btn
  - assert_visible: tng_custom_frequency.done_btn
  - type: {target: tng_custom_frequency.num_payments_field, value: "3"}
  - hide_keyboard: true
  - assert_text: {target: tng_custom_frequency.num_payments_post_label, equals: "payments"}
  - screenshot: tng_transfer_custom_frequency
  - tap: tng_custom_frequency.done_btn
  - wait_for_visible: {target: tng_common.copy, params: {text: "Every 2 months"}, timeout: 10}
  - assert_visible: {target: tng_common.copy, params: {text: "After 3 payments"}}
  - screenshot: tng_transfer_schedule_set
  # Leave without sending.
  - tap: tng_common.app_bar_back
  - wait_for_visible: {target: tng_common.list_label, params: {text: "Transfer money"}, timeout: 15}
---

# Transfer money checks that do not send

Checks the Transfer money form's errors and limits without moving money: no
account, a zero amount, an amount over the available balance, the two-decimal and
140-character limits, the swap button, and the schedule options (frequency list
and the custom frequency screen). Leaves with the back button.

Known findings, checked in TNG-copy-defects instead: "Review transfer" with no
account shows no message, the custom frequency row reads "After 1 payment 3
payments", and 0 payments does nothing on Done.
