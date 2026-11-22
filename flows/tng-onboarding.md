---
name: tng-onboarding
platforms:
- android
params:
- phone_number
- passcode_1
- passcode_2
- passcode_3
- passcode_4
- first_name
- last_name
- dob_day
- dob_month
- dob_year
- address_search
- address_pick
- email_address
steps:
# Debug menu: business waitlist off, security checks off.
# Under a test run the waitlist switch can show off while the app still uses on.
# Switching it on and then off saves the off value for real.
- wait: 2
- set_toggle:
    target: tng_onboarding.business_waitlist_toggle
    state: 'on'
- set_toggle:
    target: tng_onboarding.business_waitlist_toggle
    state: 'off'
- tap: tng_onboarding.security_tab
- set_toggle:
    target: tng_onboarding.root_detection_toggle
    state: 'off'
- set_toggle:
    target: tng_onboarding.tamper_detection_toggle
    state: 'off'
- set_toggle:
    target: tng_onboarding.runtime_detection_toggle
    state: 'off'
- tap: tng_onboarding.apply_and_continue_btn
- tap: tng_onboarding.accept_all_btn
# Start, pick the savings account, sign up
- tap: tng_onboarding.start_btn
- tap: tng_onboarding.become_a_client_btn
- tap: tng_onboarding.savings_account_btn
- tap: tng_onboarding.add_account_btn
- tap: tng_onboarding.sign_up_btn
# Phone number and SMS code
- tap: tng_onboarding.phone_input
- tap_if_visible:
    target: tng_onboarding.phone_picker_cancel_btn
    timeout: 4
- type:
    target: tng_onboarding.phone_input
    value: ${phone_number}
- hide_keyboard
- tap: tng_onboarding.phone_next_btn
- enter_otp:
    target: tng_onboarding.sms_code_input
    timeout: 180
- tap: tng_onboarding.sms_code_next_btn
- if_visible:
    target: tng_onboarding.notifications_not_now_btn
    timeout: 8
    then:
    - tap: tng_onboarding.notifications_not_now_btn
    else:
    - tap: tng_onboarding.notifications_all_set_btn
# Passcode, entered twice
- tap: tng_onboarding.passcode_only_btn
- wait_for_text:
    target: tng_onboarding.passcode_title
    contains: Create
- tap: {target: tng_onboarding.passcode_key, params: {d: "${passcode_1}"}}
- tap: {target: tng_onboarding.passcode_key, params: {d: "${passcode_2}"}}
- tap: {target: tng_onboarding.passcode_key, params: {d: "${passcode_3}"}}
- tap: {target: tng_onboarding.passcode_key, params: {d: "${passcode_4}"}}
- wait_for_text:
    target: tng_onboarding.passcode_title
    contains: Re-enter
- tap: {target: tng_onboarding.passcode_key, params: {d: "${passcode_1}"}}
- tap: {target: tng_onboarding.passcode_key, params: {d: "${passcode_2}"}}
- tap: {target: tng_onboarding.passcode_key, params: {d: "${passcode_3}"}}
- tap: {target: tng_onboarding.passcode_key, params: {d: "${passcode_4}"}}
# Name and date of birth
- type:
    target: tng_onboarding.first_name_input
    value: ${first_name}
- hide_keyboard
- type:
    target: tng_onboarding.last_name_input
    value: ${last_name}
- hide_keyboard
- tap: tng_onboarding.name_next_btn
- type:
    target: tng_onboarding.dob_day_input
    value: ${dob_day}
- type:
    target: tng_onboarding.dob_month_input
    value: ${dob_month}
- type:
    target: tng_onboarding.dob_year_input
    value: ${dob_year}
- hide_keyboard
- tap: tng_onboarding.dob_next_btn
# Mailing address, then home address is the same
- tap: tng_onboarding.enter_address_btn
- type:
    target: tng_onboarding.address_search_input
    value: ${address_search}
- tap: {target: tng_onboarding.address_suggestion, params: {pick: "${address_pick}"}}
- tap: tng_onboarding.address_continue_btn
- tap: tng_onboarding.address_confirm_btn
- tap: tng_onboarding.home_same_as_mailing
- tap: tng_onboarding.home_address_next_btn
- tap: tng_onboarding.address_confirm_btn
# Email
- type:
    target: tng_onboarding.email_input
    value: ${email_address}
- hide_keyboard
- tap: tng_onboarding.email_next_btn
- wait_for_visible: tng_onboarding.details_title
- assert_visible: {target: tng_onboarding.details_name, params: {name: "${first_name} ${last_name}"}}
- tap: tng_onboarding.details_confirm_btn
- enter_otp:
    target: tng_onboarding.email_code_input
    timeout: 180
- hide_keyboard
- tap: tng_onboarding.email_code_continue_btn
# Tax residency
- tap: tng_onboarding.tax_resident_yes
- tap: tng_onboarding.tax_resident_next_btn
- tap: tng_onboarding.tax_number_no
- tap: tng_onboarding.tax_number_next_btn
- tap: tng_onboarding.other_tax_no
- tap: tng_onboarding.other_tax_next_btn
# ID: pick driver's licence, the person scans it and takes the selfie
- tap: tng_onboarding.id_intro_continue_btn
- tap:
    target: tng_onboarding.id_picker
    offset_x: 0.92
- tap: tng_onboarding.drivers_licence_option
- tap: tng_onboarding.id_type_continue_btn
- tap_if_visible:
    target: tng_onboarding.camera_allow_btn
    timeout: 5
- wait_for_user:
    prompt: Scan the front and back of the driver's licence, then take the selfie. The run carries on when the phone shows Matched!
    until: tng_onboarding.id_matched
    timeout: 600
- tap: tng_onboarding.matched_continue_btn
- wait_for_visible:
    target: tng_onboarding.id_result
    timeout: 90
- assert_not_visible:
    target: tng_onboarding.id_rejected
    timeout: 3
---

# TNG onboarding

Opens a savings account from a clean app up to the identity check. The SMS and email codes are entered in the Workbench. The ID scan and selfie are done by a person on the phone. The last step fails if the app says it could not verify the person.
