---
name: tng-non-prod-acc-open-2
params:
- phone_number
- security_code_password
- password
- password_2
- first_name
- last_name
- dd
- mm
- yyyy
- search
- email_address
- security_code_input_2_password
steps:
- tap: recorded_flow.security_btn
- set_toggle:
    target: recorded_flow.root_jailbreak_detection_toggle
    state: 'off'
- set_toggle:
    target: recorded_flow.tamper_detection_toggle
    state: 'off'
- set_toggle:
    target: recorded_flow.runtime_detection_toggle
    state: 'off'
- tap: recorded_flow.general_btn
- set_toggle:
    target: recorded_flow.toggle_toggle
    state: 'on'
- set_toggle:
    target: recorded_flow.toggle_toggle
    state: 'off'
- tap: tng_non_prod.apply_and_continue_btn
- tap: tng_non_prod.accept_all_btn
- tap: tng_non_prod.button_btn
- tap: tng_non_prod.become_a_client_btn
- wait_for_visible: tng_non_prod.savings_account_btn
- tap: tng_non_prod.savings_account_btn
- scroll_until_visible: tng_non_prod.interestrateitem_btn
- tap: tng_non_prod.interestrateitem_btn
- wait_for_visible: tng_non_prod.close_sheet_btn
- tap: tng_non_prod.close_sheet_btn
- tap_if_visible: tng_non_prod.interestratebottomsheet
- tap: tng_non_prod.productintrocta_btn
- tap: tng_non_prod.sign_up_btn
- tap: tng_non_prod.phone_number_input
- tap_if_visible: tng_non_prod.cancel_btn
- type:
    target: tng_non_prod.phone_number_input
    value: ${phone_number}
- tap: tng_non_prod.continue_btn
- enter_otp: tng_non_prod.security_code_input
- tap: tng_non_prod.continue_btn_2
- tap: tng_non_prod.you_re_all_set_btn
- tap: tng_non_prod.passcode_only_btn
- tap: tng_non_prod.8_btn
- tap: tng_non_prod.8_btn
- tap: tng_non_prod.8_btn
- tap: tng_non_prod.8_btn
- assert_text:
    target: tng_non_prod.choose_a_more_secure_passcode
    contains: Choose a more secure passcode.
- tap: tng_non_prod.4_btn
- tap: tng_non_prod.5_btn
- tap: tng_non_prod.8_btn
- tap: tng_non_prod.7_btn
- assert_visible: tng_non_prod.re_enter_your_passcode_2
- tap: tng_non_prod.4_btn
- tap: tng_non_prod.5_btn
- tap: tng_non_prod.8_btn
- tap: tng_non_prod.7_btn
- tap: new_test.continue_btn
- tap:
    target: new_test.select_id_picker_btn
    offset_x: 0.92
    offset_y: 0.5
- tap: new_test.driver_s_licence_btn
- wait_for_enabled: new_test.continue_btn_2
- tap: new_test.continue_btn_2
- tap_if_visible: new_test.while_using_the_app_btn
- wait_for_user:
    prompt: Scan your driver's licence with the phone camera (front, then back if the app asks) and take the selfie if asked. Stop when the app asks for your occupation.
    until: personal_income.mp_t_44b92ca0_what_s_your_occupati_btn
    timeout: 900
---

Recorded live on android emulator (85 steps).

The ID scan and selfie are done by a person on a real phone. The run pauses at the
`wait_for_user` step and carries on by itself when the occupation screen (the first screen of
`tng-non-prod-acc-open-3`) shows up.
