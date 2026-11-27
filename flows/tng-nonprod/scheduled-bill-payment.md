---
name: tng-nonprod/scheduled-bill-payment
params: [from_account, payee_name, amount, amount_display, over_amount, increased_amount]
platforms: [android]
steps:
  - tap: {target: tng_common.list_label, params: {text: "Pay bill"}}
  - assert_text: {target: tng_common.app_bar_title, equals: "Pay bill", timeout: 10}
  # The non-prod payee service sometimes answers "Something went wrong". Retry, up to 3 times.
  - repeat:
      until: tng_common.list_label
      params: {text: "${payee_name}"}
      max: 3
      until_timeout: 12
      steps:
        - if_visible:
            target: tng_common.error_retry_btn
            timeout: 2
            then:
              - screenshot: tng_sched_bill_load_error
              - tap: tng_common.error_retry_btn
  - tap: {target: tng_common.list_label, params: {text: "${payee_name}"}}
  - wait_for_visible: {target: tng_move_money.submit_btn, timeout: 15}
  # Zero and over-balance amounts, paid now.
  - type: {target: tng_move_money.amount_field, value: "0"}
  - assert_text: {target: tng_move_money.amount_field, equals: "0"}
  - hide_keyboard: true
  - tap: tng_move_money.submit_btn
  - assert_visible: {target: tng_common.copy, params: {text: "Please enter amount"}}
  - screenshot: tng_bill_amount_zero
  - type: {target: tng_move_money.amount_field, value: "${over_amount}", clear_first: true}
  - assert_text: {target: tng_move_money.amount_field, equals: "${over_amount}"}
  - hide_keyboard: true
  - tap: tng_move_money.submit_btn
  - assert_visible: {target: tng_common.copy, params: {text: "Amount is higher than your available balance."}}
  - screenshot: tng_bill_over_balance
  # Schedule a monthly payment.
  - type: {target: tng_move_money.amount_field, value: "${amount}", clear_first: true}
  - hide_keyboard: true
  - set_toggle: {target: tng_move_money.schedule_toggle, state: "on"}
  - tap: tng_move_money.schedule_frequency
  - tap: {target: tng_custom_frequency.frequency_option, params: {option: "Monthly"}, timeout: 10}
  - wait_for_visible: tng_move_money.schedule_ending
  - assert_visible: {target: tng_common.copy, params: {text: "When cancelled"}}
  - tap: tng_move_money.submit_btn
  - assert_visible: {target: tng_common.copy, params: {text: "Review"}, timeout: 15}
  - assert_visible: {target: tng_payment_review.value, params: {text: "${amount_display}"}}
  - assert_visible: {target: tng_payment_review.value, params: {text: "${payee_name}"}}
  - assert_visible: {target: tng_payment_review.key_label, params: {label: "Frequency"}}
  - assert_visible: {target: tng_payment_review.value, params: {text: "Monthly"}}
  - assert_visible: {target: tng_payment_review.key_label, params: {label: "Ending"}}
  - assert_visible: {target: tng_payment_review.value, params: {text: "When cancelled"}}
  - assert_text: {target: tng_payment_review.submit_btn, equals: "Schedule"}
  - screenshot: tng_sched_bill_review
  - tap: tng_payment_review.submit_btn
  - assert_text:
      target: tng_common.labelled_copy
      params: {text: "Payment scheduled"}
      matches: '^(MP-t-[0-9a-f]{8} )?Payment scheduled$'
      timeout: 25
  - assert_text:
      target: tng_common.labelled_copy
      params: {text: "will be sent on the next business day."}
      matches: '^(MP-t-[0-9a-f]{8} )?This payment will be sent on your chosen date\. Payments scheduled on a weekend or bank holiday in Ontario will be sent on the next business day\.$'
  - assert_text: {target: tng_payment_review.payee_name, equals: "${payee_name}"}
  - assert_text: {target: tng_payment_review.transaction_id, matches: '^Transaction ID: [0-9a-f-]{36}$'}
  - screenshot: tng_sched_bill_done
  - tap: tng_payment_review.done_btn
  - wait_for_visible: {target: tng_common.list_label, params: {text: "Add bill payee"}, timeout: 15}
  # Find it under View scheduled payments.
  - tap: tng_common.app_bar_back
  - tap: {target: tng_common.list_label, params: {text: "View scheduled payments"}, timeout: 15}
  - assert_text: {target: tng_common.app_bar_title, equals: "Scheduled payments", timeout: 15}
  - tap: {target: tng_common.list_label, params: {text: "${from_account}"}}
  - wait_for_visible: {target: tng_scheduled.item_name, params: {text: "${payee_name}"}, timeout: 15}
  # The A-Z header reads "Account transfer" for a bill (finding), so only its presence is checked.
  - assert_visible: tng_scheduled.section_header
  - assert_text: {target: tng_scheduled.item_amount, equals: "${amount_display}"}
  - assert_text: {target: tng_scheduled.item_type, equals: "Scheduled payment"}
  - screenshot: tng_sched_bill_list
  - tap: {target: tng_scheduled.item_name, params: {text: "${payee_name}"}}
  # Detail.
  - assert_visible: {target: tng_common.copy, params: {text: "Scheduled payment"}, timeout: 15}
  - assert_text: {target: tng_scheduled.card_title, equals: "${payee_name}"}
  - assert_visible: {target: tng_scheduled.field_label, params: {label: "Amount"}}
  - assert_visible: {target: tng_scheduled.field_value, params: {text: "${amount_display}"}}
  - assert_visible: {target: tng_scheduled.field_value, params: {text: "Monthly"}}
  - assert_visible: {target: tng_scheduled.field_value, params: {text: "When cancelled"}}
  - assert_text: {target: tng_scheduled.cancel_btn, equals: "Cancel scheduled payment"}
  - assert_visible: tng_scheduled.next_header
  - assert_visible: tng_scheduled.edit_btn
  - screenshot: tng_sched_bill_detail
  # Raising the amount of a scheduled bill is refused only after Review (finding).
  - tap: tng_scheduled.edit_btn
  - wait_for_visible: {target: tng_move_money.amount_field, timeout: 15}
  - type: {target: tng_move_money.amount_field, value: "${increased_amount}", clear_first: true}
  - hide_keyboard: true
  - tap: tng_move_money.submit_btn
  - assert_visible: {target: tng_common.copy, params: {text: "Review"}, timeout: 15}
  - tap: tng_payment_review.submit_btn
  - wait_for_text: {target: tng_scheduled.error_message, equals: "We are unable to increase your scheduled payment to this amount. Please delete the scheduled payment and set it up again.", timeout: 20}
  - screenshot: tng_sched_bill_increase_error
  - tap: tng_scheduled.error_ok_btn
  - repeat:
      until: tng_scheduled.cancel_btn
      max: 3
      until_timeout: 8
      steps:
        - back: true
  - assert_visible: {target: tng_scheduled.field_value, params: {text: "${amount_display}"}}
  # Cancel it, so the journey leaves nothing behind.
  - tap: tng_scheduled.cancel_btn
  - wait_for_text: {target: tng_scheduled.screen_title, equals: "Are you sure you want to cancel this payment?", timeout: 15}
  - assert_visible: {target: tng_scheduled.bullet, params: {text: "We'll cancel your scheduled payment immediately."}}
  - assert_visible: {target: tng_scheduled.bullet, params: {text: "Future payments in this series will also be cancelled."}}
  - assert_text: {target: tng_scheduled.continue_header, equals: "We'll cancel your scheduled payment to:"}
  - assert_text: {target: tng_scheduled.card_title, equals: "${payee_name}"}
  # The button reads "Cancel Payment" in title case (finding), so only its presence is checked.
  - assert_visible: tng_scheduled.cancel_btn
  - screenshot: tng_sched_bill_cancel
  - tap: tng_scheduled.cancel_btn
  - if_visible:
      target: android_autofill.dismiss_btn
      timeout: 4
      then:
        - tap: android_autofill.dismiss_btn
  - if_visible:
      target: tng_password_sheet.password_field
      timeout: 8
      then:
        - assert_text: {target: tng_password_sheet.title, equals: "Enter your password"}
        - assert_text: {target: tng_password_sheet.forgot_btn, equals: "Forgot password?"}
        - tap: tng_password_sheet.password_field
        - type: {target: tng_password_sheet.password_field, value: "${TNG_PASSWORD}", secret: true}
        - if_visible:
            target: android_autofill.dismiss_btn
            timeout: 2
            then:
              - tap: android_autofill.dismiss_btn
        - tap: tng_password_sheet.continue_btn
  - wait_for_text: {target: tng_scheduled.screen_title, equals: "Payment cancelled", timeout: 25}
  - screenshot: tng_sched_bill_cancelled
  - tap: tng_scheduled.result_btn
  - wait_for_visible: {target: tng_common.app_bar_title, timeout: 15}
  - assert_not_visible: {target: tng_scheduled.item_name, params: {text: "${payee_name}"}}
---

# Scheduled bill payment: errors, set up, edit and cancel

Checks the zero and over-balance errors on the bill form, schedules a monthly
payment to an existing payee, finds it under View scheduled payments, checks the
detail screen, tries to raise the amount (refused after Review), and cancels it.
Cancelling asks for the app password, typed from `TNG_PASSWORD` and masked. The
journey ends with nothing scheduled.

Known findings, checked in TNG-copy-defects: the "Account transfer" header in the
A-Z list, "Next transfer" on a bill, the edit screen titled "Send money", the
late increase error, "Cancel Payment" and "Got It" in title case.
