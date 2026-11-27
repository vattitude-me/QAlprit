---
name: tng-nonprod/settings
params: [invalid_phone, invalid_postal, threshold, over_threshold, over_threshold_display, original_theme, other_theme]
platforms: [android]
steps:
  # Profile menu.
  - tap: tng_home.profile_btn
  - assert_visible: {target: tng_common.copy, params: {text: "User Settings"}, timeout: 10}
  - assert_visible: {target: tng_common.copy, params: {text: "Notifications"}}
  - assert_visible: {target: tng_common.copy, params: {text: "Help and Support"}}
  - screenshot: tng_profile_menu
  - tap: {target: tng_common.copy, params: {text: "User Settings"}}
  # User Settings. Values are the holder's details, so only labels are checked.
  - wait_for_visible: {target: tng_settings.detail_label, params: {text: "Mobile number"}, timeout: 15}
  - assert_visible: tng_settings.user_name
  - assert_visible: tng_settings.avatar_camera_btn
  - assert_visible: {target: tng_settings.detail_label, params: {text: "Verified email"}}
  - assert_visible: {target: tng_settings.detail_label, params: {text: "Mailing address"}}
  - assert_visible: {target: tng_settings.detail_label, params: {text: "Home address"}}
  - assert_visible: {target: tng_settings.detail_label, params: {text: "Your Personal Income"}}
  - assert_visible: {target: tng_settings.row, params: {text: "Login and security"}}
  - assert_visible: {target: tng_settings.row_subtitle, params: {text: "App login, password and device management"}}
  - assert_visible: {target: tng_settings.row, params: {text: "Notifications"}}
  - assert_visible: {target: tng_settings.row_subtitle, params: {text: "Manage app notifications and contact preferences."}}
  - assert_visible: {target: tng_settings.row, params: {text: "App theme"}}
  - assert_visible: {target: tng_settings.row_subtitle, params: {text: "Choose light or dark mode"}}
  - scroll_until_visible: {target: tng_settings.row, params: {text: "Privacy Settings"}, max_swipes: 3}
  - assert_visible: {target: tng_settings.row_subtitle, params: {text: "Manage tracking and ad preferences"}}
  - screenshot: tng_user_settings
  # Mobile number: errors only. A valid number would start a code check, so it is never sent.
  - scroll_until_visible: {target: tng_settings.detail_label, params: {text: "Mobile number"}, direction: up, max_swipes: 3}
  - tap: {target: tng_settings.detail_label, params: {text: "Mobile number"}}
  - assert_text: {target: tng_settings.mobile_message, equals: "Update your mobile number", timeout: 15}
  - assert_visible: {target: tng_common.copy, params: {text: "Mobile number"}}
  - assert_text: {target: tng_settings.country_label, equals: "Country"}
  - assert_visible: tng_settings.country_field
  - assert_text: {target: tng_settings.mobile_continue_btn, equals: "Continue"}
  - tap: tng_settings.mobile_continue_btn
  - wait_for_text: {target: tng_settings.input_error, equals: "This information is required.", timeout: 10}
  - type: {target: tng_settings.phone_field, value: "${invalid_phone}", secret: true}
  - hide_keyboard: true
  - tap: tng_settings.mobile_continue_btn
  - wait_for_text: {target: tng_settings.input_error, equals: "Invalid phone number.", timeout: 10}
  - screenshot: tng_settings_mobile_invalid
  - repeat:
      until: tng_settings.detail_label
      params: {text: "Verified email"}
      max: 3
      until_timeout: 4
      steps:
        - back: true
  # Verified email: labels only. Never saved.
  - tap: {target: tng_settings.detail_label, params: {text: "Verified email"}}
  - assert_text: {target: tng_settings.screen_title, equals: "Update your email address", timeout: 15}
  - assert_visible: {target: tng_common.copy, params: {text: "Email address"}}
  - assert_text: {target: tng_settings.screen_description, equals: "We'll send you an email to verify your email address."}
  - assert_visible: tng_settings.email_field
  - assert_text: {target: tng_settings.email_save_btn, equals: "Save"}
  - screenshot: tng_settings_email
  - repeat:
      until: tng_settings.detail_label
      params: {text: "Mailing address"}
      max: 3
      until_timeout: 4
      steps:
        - back: true
  # Mailing address: search screen, then the manual form's postal code check. Never saved.
  - tap: {target: tng_settings.detail_label, params: {text: "Mailing address"}}
  - assert_visible: {target: tng_common.copy, params: {text: "Mailing address"}, timeout: 15}
  - assert_visible: {target: tng_common.copy, params: {text: "Search"}}
  - assert_visible: {target: tng_common.copy, params: {text: "Start typing your address."}}
  - assert_visible: {target: tng_common.copy, params: {text: "Continue"}}
  - tap: {target: tng_common.copy, params: {text: "I can't find my address"}}
  - assert_visible: {target: tng_settings.address_field, params: {label: "Street number"}, timeout: 10}
  - assert_visible: {target: tng_settings.address_field, params: {label: "Unit number"}}
  - assert_visible: {target: tng_settings.address_field, params: {label: "Street name"}}
  - assert_visible: {target: tng_settings.address_field, params: {label: "Additional details (optional)"}}
  - assert_visible: {target: tng_settings.address_field, params: {label: "City"}}
  - assert_visible: {target: tng_settings.address_field, params: {label: "Province or territory"}}
  - assert_visible: {target: tng_settings.address_field, params: {label: "Postal code"}}
  - type: {target: tng_settings.address_field, params: {label: "Postal code"}, value: "${invalid_postal}"}
  - tap: {target: tng_settings.address_field, params: {label: "City"}}
  - hide_keyboard: true
  - wait_for_visible: {target: tng_common.copy, params: {text: "Please enter a valid postal code (e.g. A1A 1A1)."}, timeout: 10}
  - screenshot: tng_settings_address_invalid
  - repeat:
      until: tng_settings.detail_label
      params: {text: "Your Personal Income"}
      max: 4
      until_timeout: 4
      steps:
        - back: true
  # Personal income: read only here. Continue is never tapped.
  - tap: {target: tng_settings.detail_label, params: {text: "Your Personal Income"}}
  - assert_text: {target: tng_settings.screen_title, equals: "Some questions about your personal income", timeout: 15}
  - assert_visible: {target: tng_common.copy, params: {text: "Personal income"}}
  - assert_text: {target: tng_settings.screen_description, equals: "To protect you and your account, we need to know some details about your personal income. \n\nWe won't use this information for marketing purposes."}
  - assert_visible: {target: tng_settings.row_subtitle, params: {text: "Occupation"}}
  - assert_visible: {target: tng_settings.row_subtitle, params: {text: "Employer name"}}
  - assert_visible: {target: tng_settings.row_subtitle, params: {text: "Employer address"}}
  - assert_visible: {target: tng_settings.row_subtitle, params: {text: "Employer phone number"}}
  - assert_text: {target: tng_settings.income_continue_btn, equals: "Continue"}
  - screenshot: tng_settings_income
  - tap: tng_common.toolbar_up
  # Login and security.
  - scroll_until_visible: {target: tng_settings.row, params: {text: "Login and security"}, max_swipes: 3}
  - tap: {target: tng_settings.row, params: {text: "Login and security"}}
  - assert_visible: {target: tng_common.copy, params: {text: "Login and security"}, timeout: 15}
  - assert_visible: {target: tng_common.copy, params: {text: "App login"}}
  - assert_visible: {target: tng_settings.row, params: {text: "Change passcode"}}
  - assert_text: {target: tng_settings.biometric_title, equals: "Biometric Authentication"}
  - assert_visible: tng_settings.biometric_switch
  - assert_text: {target: tng_settings.biometric_description, equals: "Turn on Biometric Authentication to log in to the app."}
  - assert_visible: {target: tng_common.copy, params: {text: "Password"}}
  - assert_visible: {target: tng_settings.row, params: {text: "Change password"}}
  - assert_visible: {target: tng_settings.row, params: {text: "Forgot password?"}}
  - assert_visible: {target: tng_common.copy, params: {text: "Devices"}}
  - assert_visible: {target: tng_settings.row, params: {text: "Manage devices"}}
  - screenshot: tng_settings_login_security
  # Change passcode asks for the current passcode. Nothing is typed.
  - tap: {target: tng_settings.row, params: {text: "Change passcode"}}
  - assert_text: {target: tng_pin.title, equals: "Enter your 4-digit passcode", timeout: 15}
  - back: true
  # Change password: first screen only. Continue is never tapped, so no attempt is used.
  - tap: {target: tng_settings.row, params: {text: "Change password"}, timeout: 15}
  - if_visible:
      target: android_autofill.dismiss_btn
      timeout: 3
      then:
        - tap: android_autofill.dismiss_btn
  - assert_text: {target: tng_settings.password_title, equals: "Enter your existing password", timeout: 15}
  - assert_visible: {target: tng_common.copy, params: {text: "Change password"}}
  - assert_visible: tng_settings.password_field
  - assert_text: {target: tng_settings.password_forgot_btn, equals: "Forgot password?"}
  - assert_text: {target: tng_settings.password_continue_btn, equals: "Continue"}
  - repeat:
      until: tng_settings.row
      params: {text: "Manage devices"}
      max: 3
      until_timeout: 4
      steps:
        - back: true
  # Manage devices.
  - tap: {target: tng_settings.row, params: {text: "Manage devices"}}
  - assert_text: {target: tng_settings.devices_title, equals: "Devices connected to your account", timeout: 15}
  - assert_visible: {target: tng_common.copy, params: {text: "Security check"}}
  - assert_text: {target: tng_settings.device_name, equals: "This device"}
  - assert_visible: tng_settings.device_last_login
  - assert_text: {target: tng_settings.devices_help_btn, equals: "What are connected devices?"}
  - screenshot: tng_settings_devices
  - tap: tng_settings.devices_help_btn
  - assert_text: {target: tng_settings.devices_help_text, equals: "When you set up the Tangerine app or log in from a device, we allow future logins from that device. You should remove devices you no longer need connected. You can set them up again if needed.", timeout: 15}
  - tap: tng_common.toolbar_up
  - tap: {target: tng_common.toolbar_up, timeout: 10}
  - tap: {target: tng_common.toolbar_up, timeout: 10}
  # Manage notifications. Every switch is put back the way it was.
  - scroll_until_visible: {target: tng_settings.row, params: {text: "Notifications"}, max_swipes: 3}
  - tap: {target: tng_settings.row, params: {text: "Notifications"}}
  - assert_text: {target: tng_common.app_bar_title, equals: "Manage notifications", timeout: 15}
  - assert_visible: {target: tng_common.copy, params: {text: "Set your notification preferences below. You'll still receive important account messages we're required to send you."}}
  - assert_visible: {target: tng_common.copy, params: {text: "Balance notifications"}}
  - assert_visible: {target: tng_common.list_label, params: {text: "Low balance warning"}}
  - assert_visible: {target: tng_common.copy, params: {text: "Spending notifications"}}
  - assert_visible: {target: tng_common.list_label, params: {text: "Mobile wallet notifications"}}
  - assert_visible: {target: tng_common.list_value, params: {text: "If you're receiving duplicate notifications when paying with your phone or smart watch, you can turn off the notifications sent by the Tangerine app here."}}
  - assert_visible: {target: tng_common.copy, params: {text: "FlexiDraft notifications"}}
  - assert_visible: {target: tng_common.list_label, params: {text: "Scheduled payments"}}
  - assert_visible: {target: tng_common.list_value, params: {text: "Get notified when you don't have enough funds to cover your scheduled payments."}}
  - assert_visible: {target: tng_common.list_label, params: {text: "FlexiDraft usage"}}
  - assert_visible: {target: tng_common.list_value, params: {text: "Get notified when you've entered FlexiDraft after making a transaction."}}
  - assert_visible: {target: tng_common.copy, params: {text: "Offers, promotions and events"}}
  - assert_visible: {target: tng_common.list_label, params: {text: "Email"}}
  - assert_visible: {target: tng_common.list_label, params: {text: "App notification"}}
  - screenshot: tng_settings_notifications
  # Mobile wallet switches off without a question.
  - assert_toggle: {target: tng_settings.mobile_wallet_switch, state: "on"}
  - tap: tng_settings.mobile_wallet_switch
  - assert_toggle: {target: tng_settings.mobile_wallet_switch, state: "off", timeout: 10}
  - tap: tng_settings.mobile_wallet_switch
  - assert_toggle: {target: tng_settings.mobile_wallet_switch, state: "on", timeout: 10}
  # FlexiDraft usage asks first. Cancel keeps it on.
  - assert_toggle: {target: tng_settings.flexidraft_switch, state: "on"}
  - tap: tng_settings.flexidraft_switch
  - assert_visible: {target: tng_common.copy, params: {text: "Turn off notifications?"}, timeout: 10}
  - assert_visible: {target: tng_common.copy, params: {text: "If you turn this off, you may be less aware of your FlexiDraft usage and risk incurring interest charges."}}
  - assert_visible: {target: tng_common.copy, params: {text: "Turn Off"}}
  - screenshot: tng_settings_notifications_off_dialog
  - tap: {target: tng_common.copy, params: {text: "Cancel"}}
  - assert_toggle: {target: tng_settings.flexidraft_switch, state: "on", timeout: 10}
  # Scheduled payments also asks first. Its dialog text is a finding, so only the title is checked.
  - tap: tng_settings.scheduled_payments_switch
  - assert_visible: {target: tng_common.copy, params: {text: "Turn off notifications?"}, timeout: 10}
  - tap: {target: tng_common.copy, params: {text: "Cancel"}}
  - assert_toggle: {target: tng_settings.scheduled_payments_switch, state: "on", timeout: 10}
  # Low balance warning: the threshold's limits, then the original value saved back.
  - tap: tng_settings.low_balance_row
  - assert_text: {target: tng_common.app_bar_title, equals: "Low balance warning", timeout: 15}
  - assert_visible: {target: tng_common.copy, params: {text: "Get notified when your account balance hits or drops below your set amount."}}
  - assert_toggle: {target: tng_settings.low_balance_switch, params: {index: "0"}, state: "on"}
  - assert_visible: {target: tng_common.copy, params: {text: "Low balance (max. $100,000)"}}
  - assert_text: {target: tng_settings.low_balance_field, params: {index: "0"}, equals: "${threshold}"}
  - assert_visible: {target: tng_common.copy, params: {text: "Save"}}
  - type: {target: tng_settings.low_balance_field, params: {index: "0"}, value: "${over_threshold}", clear_first: true}
  - assert_text: {target: tng_settings.low_balance_field, params: {index: "0"}, equals: "${over_threshold_display}"}
  - hide_keyboard: true
  - tap: tng_common.primary_btn
  - wait_for_visible: {target: tng_common.copy, params: {text: "Set an amount below $100,000"}, timeout: 10}
  - screenshot: tng_settings_low_balance_over
  - clear: {target: tng_settings.low_balance_field, params: {index: "0"}}
  - hide_keyboard: true
  - tap: tng_common.primary_btn
  - wait_for_visible: {target: tng_common.copy, params: {text: "Set an amount above $0"}, timeout: 10}
  - screenshot: tng_settings_low_balance_empty
  - type: {target: tng_settings.low_balance_field, params: {index: "0"}, value: "${threshold}", clear_first: true}
  - hide_keyboard: true
  - tap: tng_common.primary_btn
  - wait_for_visible: {target: tng_settings.low_balance_row, timeout: 15}
  - tap: tng_settings.low_balance_row
  - assert_text: {target: tng_settings.low_balance_field, params: {index: "0"}, equals: "${threshold}", timeout: 15}
  - tap: tng_common.app_bar_back
  - wait_for_visible: {target: tng_settings.low_balance_row, timeout: 15}
  - tap: tng_common.app_bar_back
  # App theme: switch and switch back.
  - scroll_until_visible: {target: tng_settings.row, params: {text: "App theme"}, max_swipes: 3}
  - tap: {target: tng_settings.row, params: {text: "App theme"}}
  - assert_text: {target: tng_common.app_bar_title, equals: "App theme", timeout: 15}
  - assert_text: {target: tng_settings.theme_intro, equals: "Choose a theme for your app: light, dark, or the theme you've chosen in your device settings."}
  - assert_visible: {target: tng_common.copy, params: {text: "Light"}}
  - assert_visible: {target: tng_common.copy, params: {text: "Dark"}}
  - assert_visible: {target: tng_common.copy, params: {text: "System"}}
  - assert_toggle: {target: tng_settings.theme_option, params: {theme: "${original_theme}"}, state: "on"}
  - tap: {target: tng_settings.theme_option, params: {theme: "${other_theme}"}}
  - assert_toggle: {target: tng_settings.theme_option, params: {theme: "${other_theme}"}, state: "on", timeout: 10}
  - screenshot: tng_settings_theme_other
  - tap: {target: tng_settings.theme_option, params: {theme: "${original_theme}"}}
  - assert_toggle: {target: tng_settings.theme_option, params: {theme: "${original_theme}"}, state: "on", timeout: 10}
  - tap: tng_common.app_bar_back
  # Privacy settings: read only. Neither button is tapped.
  - scroll_until_visible: {target: tng_settings.row, params: {text: "Privacy Settings"}, max_swipes: 3}
  - tap: {target: tng_settings.row, params: {text: "Privacy Settings"}}
  - assert_visible: {target: tng_common.copy, params: {text: "Privacy settings"}, timeout: 15}
  - assert_visible: {target: tng_common.copy, params: {text: "Manage your preferences"}}
  - assert_visible: {target: tng_common.copy, params: {text: "Strictly necessary"}}
  - assert_visible: {target: tng_common.copy, params: {text: "Essential functions"}}
  - assert_visible: {target: tng_common.copy, params: {text: "Optional"}}
  - assert_visible: {target: tng_common.copy, params: {text: "Performance"}}
  - assert_visible: {target: tng_common.copy, params: {text: "Confirm Choices"}}
  - assert_visible: {target: tng_common.copy, params: {text: "Reject Optional Items"}}
  - screenshot: tng_settings_privacy
  - scroll_until_visible: {target: tng_common.copy, params: {text: "Track targeting"}, max_swipes: 5}
  - assert_visible: {target: tng_common.copy, params: {text: "Track performance"}}
  - assert_visible: {target: tng_common.copy, params: {text: "Targeting"}}
  - tap: tng_common.toolbar_up
  - wait_for_visible: {target: tng_settings.row, params: {text: "Privacy Settings"}, timeout: 15}
  - tap: tng_common.toolbar_up
  # Profile menu > Notifications opens the notification history.
  - wait_for_visible: {target: tng_home.profile_btn, timeout: 15}
  - tap: tng_home.profile_btn
  - tap: {target: tng_common.copy, params: {text: "Notifications"}, timeout: 10}
  - assert_text: {target: tng_settings.screen_title, equals: "Your notifications from the past 30 days", timeout: 15}
  - assert_visible: {target: tng_common.copy, params: {text: "Notification History"}}
  - screenshot: tng_notification_history
  - tap: tng_common.toolbar_up
  # Profile menu > Help and Support.
  - wait_for_visible: {target: tng_home.profile_btn, timeout: 15}
  - tap: tng_home.profile_btn
  - tap: {target: tng_common.copy, params: {text: "Help and Support"}, timeout: 10}
  - assert_visible: {target: tng_common.copy, params: {text: "How can we help?"}, timeout: 15}
  - assert_text: {target: tng_help.message, equals: "Search for features and FAQs, browse help topics, or reach out for support."}
  - assert_text: {target: tng_help.search_field, equals: "Search"}
  - assert_visible: {target: tng_settings.row, params: {text: "Browse all help topics"}}
  - assert_visible: tng_help.support_btn
  - assert_text: {target: tng_help.call_status_label, equals: "Tangerine call status"}
  - assert_visible: {target: tng_settings.row, params: {text: "We've never called you"}}
  - assert_visible: {target: tng_settings.row_subtitle, params: {text: "If the caller says they're from Tangerine, it's a scam. Hang up and call us via Support."}}
  - assert_text: {target: tng_help.service_code_title, equals: "Customer service code"}
  - assert_visible: tng_help.service_code
  - assert_text: {target: tng_help.service_code_description, equals: "When you're on a call with us, we may ask for this code to verify you."}
  - screenshot: tng_help_home
  - tap: tng_common.toolbar_up
  - wait_for_visible: {target: tng_home.profile_btn, timeout: 15}
---

# Profile and settings

Walks the profile menu and every User Settings screen without changing the
customer's details:

- Mobile number, email and mailing address check only their error messages. Nothing
  is saved, and a valid phone number is never sent because it would start a code check.
- Personal income, Login and security, Change passcode, Change password and Manage
  devices are read only. No passcode or password is typed.
- Manage notifications turns Mobile wallet off and on, cancels the FlexiDraft and
  Scheduled payments "Turn off" questions, and checks the low balance limits before
  saving the original threshold back.
- App theme switches to the other theme and back. Privacy settings is read only.
- The profile menu's Notifications (history) and Help and Support landing screens.

Known findings, checked in TNG-copy-defects instead: an empty or invalid email goes
straight to the password sheet, the Personal income rows that read "NOT REQUIRED"
with no label, the copied Low balance warning description, and the Scheduled
payments "Turn off" text that talks about FlexiDraft.
