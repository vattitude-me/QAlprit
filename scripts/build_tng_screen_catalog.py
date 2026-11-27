"""Build the Tangerine non-prod screen catalog: one screenshot of every screen,
grouped by flow, marked happy path or failure path.

Usage:
    .venv/bin/python scripts/build_tng_screen_catalog.py [DEST]

Reads screenshots from results/tng-exploration/ and writes DEST (default
results/tng-screen-catalog/) with index.html and img/. The originals are copied,
never moved. Open index.html in any browser. It needs no server or network.
"""

from __future__ import annotations

import html
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "results" / "tng-exploration"

H, F = "happy", "failure"

# (flow title, intro, [(screenshot stem, kind, screen name, note)])
FLOWS: list[tuple[str, str, list[tuple[str, str, str, str]]]] = [
    ("Login and home", "Unlock with the 4-digit passcode and land on Home.", [
        ("01_launch", H, "Passcode", ""),
        ("02_after_pin", F, "Wrong passcode", "Shows \"Incorrect passcode. Try again.\""),
        ("302_home", H, "Home", "Account name \"TNG Retail Debit \" has a trailing space."),
        ("324_profile_menu", H, "Profile menu", ""),
        ("11_debugmenu", H, "Debug menu (non-prod only)", ""),
    ]),
    ("Accounts and transactions", "Account detail, balances, insights, transactions and search.", [
        ("335_debit", H, "TNG Retail Debit transactions", "Category \"MP-t-... Payments \" has a trailing space."),
        ("04_debit_details", H, "Declined transaction in the feed", "Today header shows \"+$0.00\" next to a declined transaction."),
        ("336_txn_detail", H, "Transaction detail", "Time reads \"Today. 19:42\" with a full stop."),
        ("337_txn_search", H, "Transaction search", ""),
        ("339_txn_search_match", H, "Search with results", ""),
        ("338_txn_search_nomatch", F, "Search with no results", "\"No results found.\" then \"Transactions loaded since Nov 24\" and Load more."),
        ("06_balance", H, "Balances", ""),
        ("08_insights", H, "Spending insights", ""),
        ("140_savings_detail", H, "Savings Account detail", ""),
        ("05_overflow", H, "Account menu (debit)", "\"Business FlexiDraft\" offered on a personal account. Title case items."),
        ("175_account_menu", H, "Account menu (savings)", "Close Account is never tapped."),
        ("09_ov_441", H, "Account details", "Menu says \"Account Details and Direct Deposit\", screen says \"Account details\"."),
        ("09_ov_549", F, "Business FlexiDraft not eligible", "Title has no full stop."),
        ("09_ov_657", H, "Edit display name", ""),
    ]),
    ("Statements and documents", "Account menu > Statements & Documents.", [
        ("340_statements", H, "Statements and Documents", "Menu reads \"Statements & Documents\"."),
        ("341_statement_history", H, "Statement history", ""),
        ("342_legal_docs", H, "Legal documents", ""),
        ("343_interest_rates", H, "Interest rates", ""),
        ("344_app_licences", H, "Licences", "Row reads \"App licences\", screen title \"Licences\"."),
    ]),
    ("Send money (Interac e-Transfer)", "Move Money > Send money to a saved contact.", [
        ("312_move_money", H, "Move money", "Tab \"Move Money\" vs title \"Move money\". No Request money option."),
        ("21_send", H, "Choose a contact", ""),
        ("23_send_retry", F, "Something went wrong", "\"Try again later\" with no full stop, \"Try Again\" in title case."),
        ("26_send_amount", H, "Send form", ""),
        ("27_send_empty", F, "Empty send form", "\"Please enter amount\" and \"Enter a valid Description.\""),
        ("28_send_filled", H, "Send form filled", ""),
        ("29_send_review", H, "Security question", ""),
        ("30_send_confirm", H, "Review", "Label \"Personal message\" vs form \"Description\". \"Send Transfer\" in title case."),
        ("31_send_done", F, "Transfer sent", "Placeholder copy \"COPY_PENDING\" under the heading. \"SHARE\" in capitals."),
    ]),
    ("Recipients", "Move Money > Manage recipients.", [
        ("65_recipients", H, "Manage recipients", "Screen titled \"Send Money\"."),
        ("66_recipient_detail", H, "Contact detail", "Back button is announced as \"Got It\"."),
        ("67_recipient_menu", H, "Contact menu", "\"Edit Payee\" vs \"Delete payee\"."),
        ("68_edit_payee", H, "Edit payee", "Helper text repeats on the name fields."),
    ]),
    ("Autodeposit", "Move Money > Manage Autodeposit, register an email.", [
        ("71_autodeposit", H, "Manage Autodeposit", ""),
        ("72_alias_add", H, "Choose a method", ""),
        ("73_alias_email", H, "Email address", ""),
        ("74_alias_empty", F, "Empty email", "\"Please provide a valid email address.\""),
        ("75_alias_invalid", F, "Invalid email", "\"Please provide a valid email address.\""),
        ("76_alias_filled", H, "Valid email", ""),
        ("77_alias_next", H, "Review", ""),
        ("78_alias_submitted", H, "Check your notification", "Names the account holder, not the email. No full stop on the title."),
        ("79_alias_list", H, "Registration listed", ""),
        ("80_alias_menu", H, "Remove option", ""),
    ]),
    ("External accounts", "Move Money > External accounts. Linking needs a third-party bank login, so it stops here.", [
        ("81_external", F, "No linked accounts", "Empty state title has no full stop. \"Link Account\" in title case."),
    ]),
    ("Transfer between own accounts", "Move Money > Transfer money, debit to savings, sent now.", [
        ("41_transfer_empty", F, "Review with no account", "Nothing happens and no message shows."),
        ("42_transfer_pick", H, "Choose the account", ""),
        ("43_transfer_form", H, "Transfer form", ""),
        ("44_transfer_noamt", F, "Empty amount and description", "\"Please enter amount\", \"Please enter payment reference\"."),
        ("45_transfer_filled", H, "Form filled", ""),
        ("46_transfer_review", H, "Review", "\"To\" shows the holder name, not \"Savings Account\"."),
        ("47_transfer_sent", H, "Transfer sent", ""),
    ]),
    ("Transfer checks and limits", "Errors and limits on the transfer form, without sending.", [
        ("92_amount_zero", F, "Amount $0", "Says \"Please enter amount\" though 0 was entered."),
        ("96_over_review_now", F, "Over the available balance", "\"Amount is higher than your available balance.\""),
        ("95_over_review", F, "Scheduled over balance reaches Review", "$10,004 from a $44 account, no warning."),
        ("97_desc_150", F, "Description over 140 characters", "Capped at 140."),
        ("98_decimals", F, "Three decimals", "Capped at 2 decimals."),
        ("99_swapped", H, "Swap accounts", ""),
        ("100_back_filled", F, "Back on a filled form", "Form is discarded with no confirmation."),
        ("85_schedule_on", H, "Schedule on", ""),
        ("86_frequency", H, "Frequency options", "No title and no Cancel."),
        ("87_monthly", H, "Monthly chosen", ""),
        ("88_ending", H, "Custom frequency", "Screen opened from \"Ending\" is titled \"Frequency\"."),
        ("89_custom_plural", F, "After 3 payments", "Reads \"After 1 payment 3 payments\"."),
        ("90_zero_payments", F, "0 payments", "Done does nothing, no message."),
        ("91_custom_set", H, "Custom frequency set", ""),
    ]),
    ("Scheduled transfer", "Set up a monthly transfer, find it, cancel it.", [
        ("101_sched_form", H, "Scheduled form", ""),
        ("103_sched_review", H, "Review", "Button \"Confirm transfer\" vs \"Send transfer\" when sent now."),
        ("104_sched_done", H, "Processing", ""),
        ("105_sched_done", H, "Transfer scheduled", ""),
        ("106_sched_list", H, "Choose the account", "\"Select an account\" shows twice."),
        ("107_sched_debit", H, "Scheduled payments list", "Date \"November 27, 2026\" vs \"27 November 2026\" on the form."),
        ("108_sched_detail", H, "Scheduled transfer detail", "\"Reference\" vs \"Description\". \"Past payments\" vs \"Past transfers\"."),
        ("109_cancel_dialog", H, "Things to know before you cancel", "\"Cancel Transfer\" in title case."),
        ("110_cancelled", H, "Cancelled", "\"Got It\" in title case."),
        ("111_sched_after", F, "Empty scheduled list", "Heading and body text are swapped."),
    ]),
    ("Bill payment", "Move Money > Pay bill, add Questrade as a payee, pay $1.", [
        ("49_paybill", F, "Something went wrong", ""),
        ("50_paybill_retry", H, "Pay bill", ""),
        ("51_add_payee", H, "Add payee search", ""),
        ("52_payee_search", H, "Search \"Rogers\"", ""),
        ("53_payee_questrade", H, "Search \"Questrade\"", ""),
        ("54_payee_form", H, "Payee form", "Title \"Add payee\" vs button \"Add bill payee\"."),
        ("55_payee_empty", F, "Empty account number", "Shows \"Invalid display name.\" under Account number."),
        ("56_payee_continue", F, "Scam warning", "Phone number shows as \"1?866?580?6249\". Title is cut off."),
        ("58_payee_auth", H, "Enter your password", ""),
        ("59_payee_added", H, "Bill payment form", "Titled \"Send money\" instead of \"Pay bill\"."),
        ("60_bill_noamt", F, "Empty amount", "\"Please enter amount\"."),
        ("61_bill_review", H, "Review", "Button \"Send Transfer\" on a bill payment."),
        ("62_bill_sent", H, "Success", ""),
        ("63_bill_done", H, "Payment sent", ""),
        ("64_paybill_list", H, "Payee list", ""),
    ]),
    ("Scheduled bill payment", "Schedule a monthly bill, edit it, cancel it.", [
        ("115_bill_sched", H, "Schedule payment on", ""),
        ("116_bill_zero", F, "Amount $0", "\"Please enter amount\"."),
        ("117_bill_over", F, "Over the available balance", "\"Amount is higher than your available balance.\""),
        ("118_bill_sched_form", H, "Scheduled form", ""),
        ("119_bill_sched_review", H, "Review", "Button \"Schedule\"."),
        ("120_bill_sched_done", H, "Payment scheduled", ""),
        ("122_sched_bill_list", F, "Scheduled list A-Z", "Header \"Account transfer\" above a bill payment."),
        ("123_sched_by_type", H, "Scheduled list by type", ""),
        ("124_bill_sched_detail", H, "Scheduled payment detail", "\"EDIT\" in capitals. \"Next transfer\" on a bill."),
        ("125_bill_sched_edit", H, "Edit", "Titled \"Send money\"."),
        ("126_bill_edit_review", H, "Edit review at $2", ""),
        ("127_bill_edit_done", F, "Increase refused", "Fails after Review, not on the form."),
        ("128_after_gotit", F, "Stays on Review", ""),
        ("129_bill_edit_half", H, "Decrease to $0.50", ""),
        ("131_bill_edit_success", H, "Success", "Generic \"Success\" heading."),
        ("132_after_edit_done", H, "Detail after edit", ""),
        ("133_bill_cancel_things", H, "Cancel scheduled payment", "Different wording from the transfer cancel screen."),
        ("136_after_autofill", H, "Password needed to cancel", "Cancelling a transfer does not ask for it."),
        ("137_bill_cancelled", H, "Payment cancelled", ""),
        ("138_bill_list_after_cancel", H, "List empty again", ""),
    ]),
    ("Cheque deposit", "Move Money > Deposit cheque. No real cheque was used. The pending deposit was resubmitted with blank photos and rejected again.", [
        ("313_cheque_hub", H, "Deposit cheque", "Button \"Deposit Cheque\" vs title \"Deposit cheque\"."),
        ("314_cheque_action_required", F, "Deposit needs action", "\"Your cheque photos were invalid\", Edit and Resubmit."),
        ("315_cheque_form", H, "Before depositing", ""),
        ("316_cheque_step2", H, "Cheque details", ""),
        ("317_cheque_empty", F, "Empty details", "\"Amount must be higher than zero.\" and \"This information is required.\""),
        ("318_cheque_large", F, "Over $1,000,000", "\"You need to mail this cheque to us\". \"Mailing Instructions\" in title case."),
        ("320_cheque_filled", H, "Details filled", ""),
        ("321_cheque_take_photos", H, "Photo tips", ""),
        ("322_cheque_camera", H, "Camera, front of cheque", ""),
        ("422_cheque_resubmit", F, "Resubmit rejected", "\"There's an unknown problem with the image.\" Try Again or Cancel."),
        ("423_cheque_retry", H, "Photo tips on retry", ""),
        ("424_cheque_camera_front", H, "Camera, front", "Landscape."),
        ("425_cheque_front_review", F, "Review front photo", "A black photo is accepted with no check on the phone."),
        ("426_cheque_back_camera", H, "Camera, back", ""),
        ("427_cheque_back_review", H, "Review back photo", ""),
        ("428_cheque_after_photos", H, "Review details", ""),
        ("429_cheque_submitted", F, "Deposit rejected", "\"Photos not accepted\" again. Back is blocked."),
        ("430_cheque_after_cancel", H, "After Cancel", "Deposit still shows Action required."),
    ]),
    ("Spaces", "Savings Account > Spaces: create, move money, rename, goal, archive.", [
        ("141_spaces", H, "Spaces empty state", ""),
        ("142_create_space", H, "New Space", ""),
        ("143_space_empty", F, "Empty name", "\"Shouldn't be empty\"."),
        ("144_space_long", F, "Long name", "Capped at 30 characters with no counter. Old error stays."),
        ("145_space_goal0", F, "Goal $0 accepted", "No message, Space has no goal."),
        ("146_space_auto_on", H, "Automatic transfer", ""),
        ("147_space_auto_empty", F, "Empty automatic amount", "\"Please enter an amount to transfer\"."),
        ("148_space_repeats", H, "Repeats picker", "Confirm button \"Got It\"."),
        ("149_space_until", H, "Until options", ""),
        ("151_space_next", H, "New Space", "No review or confirmation."),
        ("152_space_out_empty", H, "Transfer Out", ""),
        ("153_space_out_noamt", F, "Transfer Out empty", "\"Shouldn't be empty\"."),
        ("154_space_out_over", F, "Transfer Out over balance", "\"Not enough funds\"."),
        ("156_space_in", H, "Transfer In", ""),
        ("157_space_in_zero", F, "Transfer In $0", "Says \"Shouldn't be empty\" though 0 is entered."),
        ("158_space_in_done", H, "After Transfer In", "Source shows \"Current (CAD)\", not \"Savings Account\"."),
        ("159_manage_space", H, "Manage Space", ""),
        ("160_rename", H, "Rename Space", ""),
        ("161_rename_empty", F, "Empty rename", "\"Please enter a name for your Space\"."),
        ("163_renamed", H, "Renamed", ""),
        ("164_goal", H, "Savings goal", ""),
        ("165_goal_on", H, "Goal on", ""),
        ("166_goal_empty", F, "Empty goal", "\"Please enter a target amount\"."),
        ("167_goal_saved", H, "Goal saved", ""),
        ("168_space_with_goal", H, "Space with goal", ""),
        ("169_archive", H, "Archive Space", ""),
        ("171_archived", H, "After archive", ""),
        ("172_archived_list", H, "Archived Spaces", ""),
        ("173_archived_detail", H, "Archived Space", "Money out shows \"$1.00\" with no minus sign."),
    ]),
    ("Profile settings", "Profile menu > User Settings. No change is saved.", [
        ("179_user_settings", H, "User Settings", ""),
        ("180_user_settings2", H, "User Settings, middle", ""),
        ("181_user_settings3", H, "User Settings, bottom", ""),
        ("182_mobile", H, "Mobile number", "Country code \"+ 1\" has a stray space."),
        ("183_mobile_empty", F, "Empty mobile number", "\"This information is required.\""),
        ("184_mobile_invalid", F, "Invalid mobile number", "\"Invalid phone number.\""),
        ("186_email", H, "Email address", ""),
        ("191_email_back", F, "Empty email", "Save skips validation and opens the password prompt."),
        ("189_email_after", F, "Password prompt for an empty email", ""),
        ("194_email_left", F, "Email \"abc\"", "Also skips validation."),
        ("196_mailing_address", H, "Mailing address search", ""),
        ("197_mailing_continue_empty", F, "Empty Continue", "Does nothing."),
        ("198_mailing_search_nomatch", F, "Search \"zqz\"", ""),
        ("200_manual_address", H, "Manual address form", "\"Unit number\" not marked optional."),
        ("201_manual_empty", F, "Empty manual form", "Continue disabled with no hint."),
        ("202_postal_invalid", F, "Invalid postal code", "\"Required field\" only on Province."),
        ("204_income", F, "Personal income", "Four rows show \"MP-t-... NOT REQUIRED\" with no label."),
        ("205_login_security", H, "Login and security", "\"Biometric Authentication\" in title case."),
        ("206_change_passcode", H, "Change passcode", "No attempts were typed."),
        ("208_change_password", H, "Change password", ""),
        ("209_change_password_empty", F, "Empty password", "Says \"Incorrect password\"."),
        ("211_manage_devices", H, "Manage devices", "Titled \"Security check\". This phone shows last used November 24."),
        ("212_what_devices", H, "What are connected devices", ""),
    ]),
    ("Notifications, theme and privacy", "Profile menu > User Settings and Notifications. Every setting was put back.", [
        ("214_notifications", H, "Notifications", "Low balance description repeats the Mobile wallet text."),
        ("215_low_balance", H, "Low balance warning", "Label \"max. $100,000\"."),
        ("216_low_over", F, "Over $100,000", ""),
        ("217_low_over_save", F, "Save over the maximum", ""),
        ("218_low_max_save", F, "Exactly $100,000", "Saves with no confirmation."),
        ("219_low_empty_save", F, "Empty amount", "\"Set an amount above $0\"."),
        ("221_low_verify", H, "Restored to 10.00", ""),
        ("222_sched_off", F, "Turn off Scheduled payments", "Dialog uses the FlexiDraft text. \"Turn Off\" in title case."),
        ("225_wallet_tap", F, "Mobile wallet off", "Turns off without asking."),
        ("227_notif_restored", H, "All switches on again", ""),
        ("228_app_theme", H, "App theme", ""),
        ("229_theme_dark", H, "Dark theme", ""),
        ("230_theme_light", H, "Light theme", ""),
        ("231_privacy", H, "Privacy settings", "\"Confirm Choices\", \"Reject Optional Items\" in title case."),
        ("232_privacy_bottom", H, "Privacy settings, bottom", ""),
        ("235_inbox", H, "Notification History", "Amounts show as both \"CA$1.00\" and \"$1.00\"."),
    ]),
    ("Open an account", "Home > Open Account. Stops before opening anything.", [
        ("306_open_account", H, "New Account", ""),
        ("307_open_personal", H, "Personal products", ""),
        ("309_open_search_sav", H, "Search \"Sav\"", ""),
        ("308_open_search_nomatch", F, "Search with no results", "\"No results found\" with no full stop."),
        ("310_open_savings_intro", F, "Savings Account greyed out", "Cannot be tapped and no reason is shown."),
        ("311_open_spending", H, "Spending Account intro", ""),
    ]),
    ("Help and support", "Profile menu > Help and Support.", [
        ("325_help", H, "How can we help?", ""),
        ("326_help_topics", H, "All help topics", "Topics cover Spaces only."),
        ("327_help_topic", H, "Help topic", ""),
        ("328_help_article", H, "Help article", ""),
        ("331_help_search_results", H, "Search \"transfer\"", ""),
        ("330_help_search_nomatch", F, "Search with no results", ""),
        ("333_support", H, "Support", ""),
        ("334_call_status", H, "Call status", ""),
    ]),
    ("Live chat and contact", "Support > + > Contact us. One test chat was sent and ended. No message was sent, no call was made and no feedback was submitted.", [
        ("400_support", H, "Support", ""),
        ("401_chat_new", H, "New conversation", ""),
        ("402_contact_options", H, "Contact options", ""),
        ("403_send_message", H, "New message", "A subject typed in an earlier session is still there. Send stays disabled with no message."),
        ("404_message_left_no_warning", F, "Leave a draft", "Back goes straight to Support with no warning."),
        ("405_conversation_detail", F, "Archived conversation", "Bot messages in reverse order. The top timestamp is clipped under the header."),
        ("406_chat_started", F, "Live chat started", "Consent message shows raw <a href> HTML, and the link points to a business PDF."),
        ("407_chat_typed", H, "Message typed", ""),
        ("408_chat_sent", F, "Message sent", "Raw code \"EN_ERR_EA_4819\" shows after the message."),
        ("409_chat_wait", F, "Waiting for an agent", "Nobody joined after 30 seconds."),
        ("410_chat_attach", H, "Attach a file", "\"Take Photo\", \"Choose Photo\", \"Choose File\" in title case."),
        ("411_chat_close", H, "End the chat?", ""),
        ("412_chat_ended", H, "Chat ended", "The error code is gone."),
        ("414_call_us", H, "Call us", "System chooser. No call was made."),
        ("415_feedback", F, "Feedback", "\"We're listening\" with no full stop."),
    ]),
    ("Cards", "Cards tab.", [
        ("305_cards_wait", F, "Cards never loads", "Spinner still showing after 40 seconds."),
    ]),
    ("Onboarding (new customer)", "A fresh install, then Become a client > Savings Account as a new applicant, up to the pending review. The SMS code was read from the phone and the email code typed by the tester. The tester held the ID and took the selfie.", [
        ("ob_002_debug_security", H, "Debug menu", "Dev only. Business Waitlist switched on, then off."),
        ("ob_003_apply_and_continue", F, "Start", "The only text is \"Our Bank\", not Tangerine."),
        ("ob_004_start", F, "Welcome", "\"Welcome to Forward Banking\", not Tangerine."),
        ("ob_006_choose_account", H, "Choose an account", ""),
        ("ob_008_swipe_up", F, "Savings Account", "Raw code \"EN_ERR_EA_2547\" shows on the page."),
        ("ob_009_see_info", H, "Interest rates", ""),
        ("ob_011_add_account", H, "Sign up", ""),
        ("ob_012_sign_up", H, "Mobile number", ""),
        ("ob_013_continue", F, "Empty number", "\"This information is required.\""),
        ("ob_014_phone_number", H, "Google number picker", "From Google Play services. Cancelled."),
        ("ob_015_type_phone_number", F, "Number typed", "The required error stays after a number is typed."),
        ("ob_017_continue", F, "Empty code", "\"You need to enter 6 digits\" with no full stop. The #N suffix shows as \"ext.\" in the number."),
        ("ob_018_sms_code_entered", H, "Code entered", ""),
        ("ob_020_notifications", H, "Notifications", "Not Now was chosen."),
        ("ob_021_not_now", H, "Log in choice", "Says \"the Tangerine app\". The welcome screen said Forward Banking."),
        ("ob_022_passcode_only", H, "Create passcode", ""),
        ("ob_023_how_to_choose_a_strong_passcode", H, "Passcode tips", "Titled for \"PIN, passcode and password\"."),
        ("ob_025_after_keypad", F, "Weak passcode", "\"Choose a more secure passcode.\""),
        ("ob_026_after_keypad", H, "Re-enter passcode", ""),
        ("ob_028_cancel", H, "Create password", "The link on a password screen says \"How to choose a strong passcode\"."),
        ("ob_031_continue", F, "Weak password", "The rule text turns into the error. No separate message."),
        ("ob_033_cancel", H, "Re-enter password", ""),
        ("ob_034_continue", F, "Password mismatch", "\"Your password doesn't match. Try again.\""),
        ("ob_035_continue", H, "Legal name", ""),
        ("ob_036_continue", F, "Empty name", "No message on the empty fields."),
        ("ob_038_after_back", H, "Name typed", ""),
        ("ob_039_continue", H, "Date of birth", ""),
        ("ob_040_continue", F, "Empty date", "\"Invalid day\" only. Nothing on month or year."),
        ("ob_042_continue", F, "31 February", "\"Please check the date you've entered\" with no full stop."),
        ("ob_043_dob_entered", H, "Date entered", ""),
        ("ob_044_continue", H, "Mailing address", ""),
        ("ob_045_continue", F, "No address", "Continue does nothing and shows no message."),
        ("ob_046_add_address", H, "Address search", ""),
        ("ob_047_address_search", H, "Search results", "\"- 264 Addresses\" with a hyphen and a capital A."),
        ("ob_049_continue", H, "Confirm mailing address", "Button says \"Confirm\"."),
        ("ob_050_confirm", H, "Home address", ""),
        ("ob_052_continue", H, "Confirm home address", ""),
        ("ob_053_confirm", H, "Email", ""),
        ("ob_054_continue", F, "No email", "No message. Continue looks disabled."),
        ("ob_056_continue", F, "Invalid email", "\"abc\" gets no message."),
        ("ob_058_swipe_up", F, "Check your details", "Date of birth shows as YYYY-MM-DD, not the way it was typed. The keyboard capitalised the first letter of the email."),
        ("ob_060_after_checking", H, "Verify email", ""),
        ("ob_061_swipe_down", F, "Verification email", "Titled \"Welcome to Tangerine\" and says \"Now that you're a Client\" before the application is finished."),
        ("ob_064_email_code_entered", H, "Email code entered", ""),
        ("ob_071_action_bar_root", H, "Tax in Canada", ""),
        ("ob_077_continue", F, "No tax answer", "Continue shows no message."),
        ("ob_078_continue", H, "SIN now?", ""),
        ("ob_079_continue", H, "Enter SIN", ""),
        ("ob_080_continue", F, "Empty SIN", "Continue shows no message. No SIN was entered. Back, then No."),
        ("ob_081_continue", H, "Tax in another country", ""),
        ("ob_082_continue", H, "ID verification", ""),
        ("ob_083_continue", H, "Select your ID", ""),
        ("ob_084_id_picker", H, "ID types", "The picker opens only when tapped near its right edge."),
        ("ob_085_driver_s_licence", H, "Driver's licence chosen", ""),
        ("ob_086_continue", F, "Camera permission", "Android names the app \"Tangerine Business\" in a personal application."),
        ("ob_087_while_using_the_app", H, "Get ready: ID photo", ""),
        ("ob_088_continue", H, "ID camera", ""),
        ("ob_089_id_camera_front", F, "Too dark", ""),
        ("ob_090_help", H, "Camera help", ""),
        ("ob_091_idv_auto_1", H, "Uploading", ""),
        ("ob_092_idv_auto_2", H, "Front captured", ""),
        ("ob_093_continue", H, "Back of ID", ""),
        ("ob_095_idv_auto_4", F, "Too blurry", ""),
        ("ob_097_idv_auto_6", H, "Analyzing", "US spelling \"Analyzing\". The app uses Canadian spelling elsewhere (licence)."),
        ("ob_100_continue", H, "Checking details", ""),
        ("ob_101_idv_auto_9", H, "Get ready: selfie", ""),
        ("ob_103_idv_auto_10", H, "Take a selfie", ""),
        ("ob_104_take_selfie", H, "Selfie camera", ""),
        ("ob_105_idv_auto_11", H, "Processing", ""),
        ("ob_108_idv_auto_14", H, "Verifying identity", ""),
        ("ob_110_id_matched", H, "Matched", ""),
        ("ob_112_identity_verified", H, "Identity verified", ""),
        ("ob_114_continue", H, "Occupation", ""),
        ("ob_116_occupation_search", F, "Occupation search", "One result has a subtitle and no title."),
        ("ob_118_continue", H, "Personal income", ""),
        ("ob_119_what_s_your_employer_s_name", H, "Employer name", ""),
        ("ob_120_continue", F, "Empty employer name", "\"Please enter a name\" with no full stop."),
        ("ob_122_continue", H, "Employer name saved", ""),
        ("ob_123_what_s_your_employer_s_address", H, "Employer address", ""),
        ("ob_125_continue", F, "Confirm employer address", "Button says \"Continue\". The personal address screens say \"Confirm\"."),
        ("ob_127_what_s_your_employer_s_phone_num", H, "Employer phone", ""),
        ("ob_128_employer_phone", H, "Phone typed", ""),
        ("ob_129_continue", F, "Personal income done", "The employer phone shows unformatted, \"+1\" and ten digits with no spaces or dashes."),
        ("ob_130_continue", H, "Account purpose", ""),
        ("ob_131_continue", F, "No purpose chosen", "Continue does nothing and shows no message."),
        ("ob_132_continue", F, "Legal information", "Asks the customer to \"Download the Board Resolution Declaration, sign it, and file it in your company's minutes\" in a personal application."),
        ("ob_133_swipe_up", F, "Legal information, bottom", "Says \"opening this account for your business\", and \"call us at\" has no phone number after it."),
        ("ob_134_submit", F, "Submit unticked", "No message."),
        ("ob_135_consent_to_electronic_delivery_o", H, "Consent document loading", ""),
        ("ob_136_tap_MISSING", H, "Consent document", "In-app viewer, 46 pages."),
        ("ob_138_i_have_read_and_agree_to_the_doc", H, "Box ticked", ""),
        ("ob_139_submit", H, "Submitting", ""),
        ("ob_140_checking_details_pending", H, "Checking your details", ""),
        ("ob_140_turn_on_notifications", F, "Notification permission", "Android names the app \"Tangerine Business\". Allowed."),
        ("ob_141_busier_than_usual", H, "Busier than usual", "End of onboarding. The application waits for review."),
    ]),
]

NOT_CAPTURED = [
    "Request money: the app has no Request money option.",
    "A successful cheque deposit: needs a real cheque. Blank photos were rejected.",
    "A live chat answered by an agent: nobody joined the test chat.",
    "Linking an external account: needs a sign-in at another bank.",
    "Saving a new email or phone number: starts verification and changes the profile.",
    "Close Account, wrong passcode attempts in settings, logging out: never done.",
    "Onboarding legal documents other than Consent to Electronic Delivery, and the two CDIC links: not opened before Submit.",
    "Onboarding paths not taken: biometric login, Get Notified, entering a SIN, tax residency outside Canada, \"I can't find my address\", a passport or other ID.",
]

CSS = """
:root{--bg:#f3f6f9;--card:#fff;--ink:#0f172a;--muted:#475569;--line:#e2e8f0;--accent:#0f766e;
--ok:#15803d;--okbg:#dcfce7;--bad:#b91c1c;--badbg:#fee2e2}
@media (prefers-color-scheme:dark){:root{--bg:#090e1a;--card:#111827;--ink:#e2e8f0;--muted:#94a3b8;
--line:#1f2937;--accent:#2dd4bf;--ok:#4ade80;--okbg:#052e16;--bad:#f87171;--badbg:#450a0a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
header{padding:28px 20px 8px;max-width:1280px;margin:auto}h1{margin:0 0 4px;font-size:26px}
.sub{color:var(--muted);margin:0}
.bar{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);padding:10px 20px}
.bar .in{max-width:1280px;margin:auto;display:flex;gap:8px;flex-wrap:wrap;align-items:center}
button,select{font:inherit;padding:6px 12px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink);cursor:pointer}
button.on{background:var(--accent);color:var(--bg);border-color:var(--accent)}
main{max-width:1280px;margin:auto;padding:0 20px 40px}
.stats{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 16px}
.stat b{display:block;font-size:22px}
section{margin-top:28px}h2{font-size:20px;margin:0}section>p{color:var(--muted);margin:2px 0 12px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden;display:flex;flex-direction:column}
.card img{width:100%;aspect-ratio:960/2142;object-fit:cover;object-position:top;display:block;cursor:zoom-in;background:var(--line)}
.card .t{padding:8px 10px 10px}.card .n{font-weight:600;font-size:14px}
.card .note{color:var(--muted);font-size:13px;margin-top:4px}
.tag{display:inline-block;font-size:11px;font-weight:600;padding:1px 8px;border-radius:99px;margin-bottom:4px}
.happy .tag{color:var(--ok);background:var(--okbg)}.failure .tag{color:var(--bad);background:var(--badbg)}
.num{color:var(--muted);font-size:12px;float:right}
ul.nc{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 28px}
#lb{position:fixed;inset:0;background:rgba(0,0,0,.85);display:none;align-items:center;justify-content:center;z-index:9;padding:16px}
#lb img{max-height:100%;max-width:100%}#lb.open{display:flex}
footer{color:var(--muted);text-align:center;padding:20px;font-size:13px}
@media (max-width:520px){.grid{grid-template-columns:repeat(2,1fr)}}
"""

JS = """
const cards=[...document.querySelectorAll('.card')];let kind='all';
function apply(){const f=document.getElementById('flow').value;
 document.querySelectorAll('section[data-flow]').forEach(s=>{
  const showSec=f==='all'||s.dataset.flow===f;let any=false;
  s.querySelectorAll('.card').forEach(c=>{const ok=kind==='all'||c.classList.contains(kind);c.style.display=ok?'':'none';any=any||ok});
  s.style.display=showSec&&any?'':'none'})}
document.querySelectorAll('[data-kind]').forEach(b=>b.onclick=()=>{kind=b.dataset.kind;
 document.querySelectorAll('[data-kind]').forEach(x=>x.classList.toggle('on',x===b));apply()});
document.getElementById('flow').onchange=apply;
const lb=document.getElementById('lb'),li=lb.querySelector('img');
cards.forEach(c=>c.querySelector('img').onclick=()=>{li.src=c.querySelector('img').src;lb.classList.add('open')});
lb.onclick=()=>lb.classList.remove('open');
document.addEventListener('keydown',e=>{if(e.key==='Escape')lb.classList.remove('open')});
"""


def to_jpeg(src: Path, dst: Path) -> None:
    """Copy a screenshot as JPEG to keep the folder small. Falls back to PNG."""
    if sys.platform == "darwin" and shutil.which("sips"):
        result = subprocess.run(
            ["sips", "-s", "format", "jpeg", "-s", "formatOptions", "75", str(src), "--out", str(dst)],
            capture_output=True,
        )
        if result.returncode == 0:
            return
    shutil.copy2(src, dst.with_suffix(".png"))


def build(
    dest: Path,
    src: Path,
    flows: list[tuple[str, str, list[tuple[str, str, str, str]]]],
    not_captured: list[str],
    *,
    lang: str = "en",
    title: str = "TNG screen catalog",
    heading: str = "Tangerine non-prod: every screen",
    captured: str = "2026-11-26",
    notes_file: str = "results/tng-exploration/notes.md",
) -> int:
    """Write dest/index.html and dest/img/ from the screenshots in src."""
    img = dest / "img"
    img.mkdir(parents=True, exist_ok=True)

    missing = [s for _, _, shots in flows for s, *_ in shots if not (src / f"{s}.png").is_file()]
    if missing:
        print("Missing screenshots:", ", ".join(missing))
        return 1

    total = happy = 0
    sections = []
    options = ['<option value="all">All flows</option>']
    for i, (flow_title, intro, shots) in enumerate(flows, 1):
        fid = f"f{i}"
        options.append(f'<option value="{fid}">{i}. {html.escape(flow_title)}</option>')
        cards = []
        for j, (stem, kind, name, note) in enumerate(shots, 1):
            target = img / f"{stem}.jpg"
            if not target.exists() and not target.with_suffix(".png").exists():
                to_jpeg(src / f"{stem}.png", target)
            rel = f"img/{target.name}" if target.exists() else f"img/{stem}.png"
            total += 1
            happy += kind == H
            label = "Happy path" if kind == H else "Failure path"
            note_html = f'<div class="note">{html.escape(note)}</div>' if note else ""
            cards.append(
                f'<div class="card {kind}"><img loading="lazy" src="{rel}" alt="{html.escape(name)}">'
                f'<div class="t"><span class="num">{i}.{j}</span><span class="tag">{label}</span>'
                f'<div class="n">{html.escape(name)}</div>{note_html}</div></div>'
            )
        sections.append(
            f'<section id="{fid}" data-flow="{fid}"><h2>{i}. {html.escape(flow_title)}</h2>'
            f'<p>{html.escape(intro)} {len(shots)} screens.</p><div class="grid">{"".join(cards)}</div></section>'
        )

    nc = "".join(f"<li>{html.escape(x)}</li>" for x in not_captured)
    page = f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
<style>{CSS}</style></head><body>
<header><h1>{html.escape(heading)}</h1>
<p class="sub">ca.tangerine.mobile.banking.app.nonProd on a Pixel 9 Pro (Android). One screenshot per screen, grouped by flow.
Captured on {captured}. Built {date.today().isoformat()}. Tap a screenshot to enlarge it.</p></header>
<div class="bar"><div class="in">
<button data-kind="all" class="on">All</button><button data-kind="happy">Happy path</button>
<button data-kind="failure">Failure path</button><select id="flow">{"".join(options)}</select></div></div>
<main><div class="stats"><div class="stat"><b>{total}</b>screens</div><div class="stat"><b>{len(flows)}</b>flows</div>
<div class="stat"><b>{happy}</b>happy path</div><div class="stat"><b>{total - happy}</b>failure path</div></div>
{"".join(sections)}
<section><h2>Not captured, and why</h2><ul class="nc">{nc}</ul></section></main>
<footer>The notes repeat findings from {html.escape(notes_file)}. "MP-t-" prefixes are dev copy labels and are not findings.
This report contains the test customer's details. Keep it private.</footer>
<div id="lb"><img alt=""></div><script>{JS}</script></body></html>"""
    (dest / "index.html").write_text(page, encoding="utf-8")
    print(f"{dest / 'index.html'}: {total} screens in {len(flows)} flows ({happy} happy, {total - happy} failure)")
    return 0


def main(argv: list[str]) -> int:
    dest = Path(argv[0]).expanduser() if argv else REPO / "results" / "tng-screen-catalog"
    return build(dest, SRC, FLOWS, NOT_CAPTURED, captured="2026-11-26 and 2026-11-27")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
