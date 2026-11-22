---
name: tng-non-prod-acc-open-3
params:
- search
- en_err_ea_2036
- search_input_2
- en_err_ea_2041
platforms:
- android
apps:
  android: apps/tng-non-prod.apk
steps:
- tap: personal_income.mp_t_44b92ca0_what_s_your_occupati_btn
- type:
    target: personal_income.search_input
    value: ${search}
- set_toggle:
    target: personal_income.software_and_applications_develope_toggl
    state: 'on'
- tap: personal_income.continue_btn
- tap: personal_income.occupation_btn_2
- type:
    target: personal_income.en_err_ea_2036_input
    value: ${en_err_ea_2036}
- tap: personal_income.continue_btn_2
- tap: personal_income.acme_software_inc_btn_2
- tap: personal_income.addresslookupintroenteryouraddress_btn
- type:
    target: personal_income.search_input_2
    value: ${search_input_2}
- set_toggle:
    target: personal_income.5_bay_st_toggle_3
    state: 'on'
- tap: personal_income.continue_btn_3
- tap: personal_income.en_err_ea_2032_btn
- tap: personal_income.5_bay_st_oro_medonte_on_l0l_2l0_btn_2
- tap: personal_income.en_err_ea_2041_input
- type:
    target: personal_income.en_err_ea_2041_input
    value: ${en_err_ea_2041}
- tap: personal_income.en_err_ea_2039_btn
- tap: personal_income.continue_btn_4
- set_toggle:
    target: personal_income.daily_banking_toggle
    state: 'on'
- tap: personal_income.button_btn_2
- swipe: up
- set_toggle:
    target: personal_income.refer_to_the_privacy_code_for_deta_toggl
    state: 'on'
- tap: personal_income.termsacceptancefooterbutton_btn
---

Recorded live on android emulator (23 steps).
