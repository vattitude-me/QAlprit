---
name: tng-nonprod/bill-pay
params: [payee_name, amount, amount_display]
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
              - screenshot: tng_paybill_load_error
              - tap: tng_common.error_retry_btn
  - assert_visible: {target: tng_common.list_label, params: {text: "Add bill payee"}}
  - tap: {target: tng_common.list_label, params: {text: "${payee_name}"}}
  - wait_for_visible: {target: tng_move_money.submit_btn, timeout: 15}
  - assert_visible: tng_move_money.from_account
  - assert_visible: {target: tng_common.copy, params: {text: "${payee_name}"}}
  - assert_text: {target: tng_move_money.amount_label, equals: "Amount (CAD)"}
  - assert_visible: {target: tng_common.list_label, params: {text: "Schedule payment"}}
  - assert_visible: {target: tng_common.copy, params: {text: "Continue"}}
  - tap: tng_move_money.submit_btn
  - assert_visible: {target: tng_common.copy, params: {text: "Please enter amount"}}
  - type: {target: tng_move_money.amount_field, value: "${amount}"}
  - hide_keyboard: true
  - tap: tng_move_money.submit_btn
  - assert_visible: {target: tng_common.copy, params: {text: "Review"}, timeout: 15}
  - assert_text: {target: tng_payment_review.call_status_title, equals: "We've never called you"}
  - assert_text: {target: tng_payment_review.call_status_message, equals: "If the caller says they're from Tangerine, it's a scam. Hang up and call us via Support."}
  - assert_visible: {target: tng_payment_review.key_label, params: {label: "Amount"}}
  - assert_visible: {target: tng_payment_review.value, params: {text: "${amount_display}"}}
  - assert_visible: {target: tng_payment_review.key_label, params: {label: "From"}}
  - assert_visible: {target: tng_payment_review.key_label, params: {label: "To"}}
  - assert_visible: {target: tng_payment_review.value, params: {text: "${payee_name}"}}
  - assert_visible: {target: tng_payment_review.key_label, params: {label: "When"}}
  - assert_visible: {target: tng_payment_review.value, params: {text: "Today"}}
  - assert_visible: {target: tng_payment_review.key_label, params: {label: "Category"}}
  - assert_visible: {target: tng_payment_review.key_label, params: {label: "Type"}}
  - assert_text:
      target: tng_common.labelled_copy
      params: {text: "or on a bank holiday in Ontario."}
      matches: '^(MP-t-[0-9a-f]{8} )?Your bill will be paid on the next business day if you submit it after 10 pm ET, on a weekend, or on a bank holiday in Ontario\.$'
  - screenshot: tng_bill_review
  - tap: tng_payment_review.submit_btn
  - assert_text:
      target: tng_common.labelled_copy
      params: {text: "Payment sent"}
      matches: '^(MP-t-[0-9a-f]{8} )?Payment sent$'
      timeout: 20
  - assert_text:
      target: tng_common.labelled_copy
      params: {text: "for your payment to be completed."}
      matches: '^(MP-t-[0-9a-f]{8} )?It may take up to 30 minutes for your payment to be completed\.$'
  - assert_text: {target: tng_payment_review.payee_name, equals: "${payee_name}"}
  - assert_visible: {target: tng_payment_review.value, params: {text: "${amount_display}"}}
  - assert_text: {target: tng_payment_review.transaction_id, matches: '^Transaction ID: [0-9a-f-]{36}$'}
  - assert_text: {target: tng_payment_review.done_btn, equals: "Done"}
  - screenshot: tng_bill_sent
  - tap: tng_payment_review.done_btn
  - wait_for_visible: {target: tng_common.list_label, params: {text: "Add bill payee"}, timeout: 15}
---

# Pay a bill

Pays an existing bill payee from the debit account and checks the form, the empty
amount error, the review and the confirmation. The form title, the review button
and the Type value have known copy defects, checked in TNG-copy-defects.
