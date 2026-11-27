---
name: tng-nonprod/spaces
params: [account_name, long_name, long_name_display, space_name, renamed_name, over_amount, amount, amount_display, goal_amount, goal_display, goal_away_display]
platforms: [android]
steps:
  - tap: {target: tng_home.account_card, params: {name: "${account_name}"}}
  - wait_for_visible: {target: tng_account.spaces_btn, timeout: 15}
  - tap: tng_account.spaces_btn
  - wait_for_visible: {target: tng_common.copy, params: {text: "Create Space"}, timeout: 15}
  - assert_visible: {target: tng_common.copy, params: {text: "Spaces"}}
  - if_visible:
      target: tng_common.copy
      params: {text: "Set money aside in Spaces"}
      timeout: 2
      then:
        - assert_visible: {target: tng_common.copy, params: {text: "Saving for a vacation, big project or emergency fund? Create a Space to help you reach your savings goal."}}
        - screenshot: tng_spaces_empty
  # New Space: name and goal.
  - tap: {target: tng_common.copy, params: {text: "Create Space"}}
  - assert_text: {target: tng_spaces.new_space_title, equals: "New Space", timeout: 10}
  - assert_text: {target: tng_spaces.new_space_subtitle, equals: "Space"}
  - assert_text: {target: tng_spaces.lets_begin, equals: "Let's begin"}
  - assert_text: {target: tng_spaces.name_field, equals: "Space name"}
  - assert_text: {target: tng_spaces.goal_field, equals: "Goal amount (optional)"}
  - assert_text: {target: tng_spaces.continue_btn, equals: "Continue"}
  # Empty name. The message "Shouldn't be empty" is a finding, so only its presence is checked.
  - tap: tng_spaces.continue_btn
  - assert_visible: tng_spaces.input_error
  - screenshot: tng_space_name_empty
  # The name is capped at 30 characters, with no counter.
  - type: {target: tng_spaces.name_field, value: "${long_name}"}
  - assert_text: {target: tng_spaces.name_field, equals: "${long_name_display}"}
  - type: {target: tng_spaces.name_field, value: "${space_name}"}
  # A $0 goal is accepted and leaves the Space with no goal (finding).
  - type: {target: tng_spaces.goal_field, value: "0"}
  - hide_keyboard: true
  - tap: tng_spaces.continue_btn
  # Automatic transfer step.
  - assert_text: {target: tng_spaces.control_title, equals: "Automatic transfer", timeout: 10}
  - assert_visible: {target: tng_common.copy, params: {text: "Set up a recurring transfer into your Space."}}
  - set_toggle: {target: tng_spaces.switch, state: "on"}
  - assert_text: {target: tng_spaces.amount_label, equals: "Transfer", timeout: 5}
  - assert_visible: {target: tng_spaces.setting_row, params: {text: "Starting"}}
  - assert_visible: {target: tng_spaces.setting_row, params: {text: "Repeats"}}
  - assert_visible: {target: tng_spaces.option_value, params: {text: "Monthly"}}
  - assert_visible: {target: tng_spaces.setting_row, params: {text: "Until"}}
  - assert_visible: {target: tng_common.copy, params: {text: "Cancelled"}}
  - tap: tng_spaces.continue_btn
  - assert_text: {target: tng_spaces.input_error, equals: "Please enter an amount to transfer"}
  - screenshot: tng_space_auto_no_amount
  - tap: {target: tng_spaces.setting_row, params: {text: "Repeats"}}
  - assert_visible: {target: tng_common.copy, params: {text: "Every"}, timeout: 5}
  - assert_visible: {target: tng_common.copy, params: {text: "Month"}}
  # The picker confirms with "Got It" (finding), so only Cancel is checked.
  - assert_text: {target: android_dialog.cancel_btn, equals: "Cancel"}
  - screenshot: tng_space_auto_repeats
  - tap: android_dialog.cancel_btn
  - tap: {target: tng_spaces.setting_row, params: {text: "Until"}}
  - assert_visible: {target: android_dialog.list_option, params: {text: "Cancelled"}, timeout: 5}
  - assert_visible: {target: android_dialog.list_option, params: {text: "Number of payments"}}
  - assert_visible: {target: android_dialog.list_option, params: {text: "Date"}}
  - screenshot: tng_space_auto_until
  - back: true
  - set_toggle: {target: tng_spaces.switch, state: "off"}
  - assert_not_visible: tng_spaces.amount_label
  - tap: tng_spaces.continue_btn
  # The new Space.
  - assert_text: {target: tng_spaces.space_name, equals: "${space_name}", timeout: 20}
  - assert_text: {target: tng_spaces.space_description, equals: "Space"}
  - assert_text: {target: tng_spaces.space_balance, equals: "$0.00"}
  - assert_text: {target: tng_spaces.manage_btn, equals: "Manage Space"}
  - assert_text: {target: tng_spaces.empty_title, equals: "No transactions"}
  - assert_text: {target: tng_spaces.transfer_in_btn, equals: "Transfer In"}
  - assert_text: {target: tng_spaces.transfer_out_btn, equals: "Transfer Out"}
  - screenshot: tng_space_new
  # Transfer Out of an empty Space.
  - tap: tng_spaces.transfer_out_btn
  - assert_visible: {target: tng_common.copy, params: {text: "Transfer"}, timeout: 10}
  - assert_text: {target: tng_spaces.amount_label, equals: "Transfer amount"}
  - assert_text: {target: tng_spaces.from_label, equals: "From"}
  - assert_text: {target: tng_spaces.to_label, equals: "To"}
  - assert_visible: {target: tng_spaces.transfer_account_name, params: {name: "${space_name}"}}
  - assert_visible: {target: tng_spaces.transfer_account_name, params: {name: "${account_name}"}}
  - tap: tng_spaces.transfer_submit_btn
  - assert_visible: tng_spaces.input_error
  - type: {target: tng_spaces.amount_field, value: "${over_amount}"}
  - hide_keyboard: true
  - tap: tng_spaces.transfer_submit_btn
  - assert_text: {target: tng_spaces.input_error, equals: "Not enough funds"}
  - screenshot: tng_space_out_not_enough
  - back: true
  # Transfer In.
  - tap: {target: tng_spaces.transfer_in_btn, timeout: 10}
  - assert_text: {target: tng_spaces.transfer_submit_btn, equals: "Transfer In", timeout: 10}
  - type: {target: tng_spaces.amount_field, value: "0"}
  - hide_keyboard: true
  - tap: tng_spaces.transfer_submit_btn
  # A $0 amount says "Shouldn't be empty" (finding), so only the message's presence is checked.
  - assert_visible: tng_spaces.input_error
  - screenshot: tng_space_in_zero
  - type: {target: tng_spaces.amount_field, value: "${amount}", clear_first: true}
  - hide_keyboard: true
  - tap: tng_spaces.transfer_submit_btn
  - wait_for_text: {target: tng_spaces.space_balance, equals: "${amount_display}", timeout: 20}
  - assert_text: {target: tng_spaces.transactions_header, equals: "Transactions"}
  - assert_text: {target: tng_spaces.item_amount, equals: "+${amount_display}"}
  - assert_visible: tng_spaces.item_name
  - assert_text: {target: tng_spaces.see_all_btn, equals: "See all transactions"}
  - screenshot: tng_space_after_in
  # Manage Space: rename.
  - tap: tng_spaces.manage_btn
  - assert_visible: {target: tng_common.copy, params: {text: "Manage Space"}, timeout: 10}
  - assert_visible: {target: tng_spaces.section_header, params: {text: "Name"}}
  - assert_visible: {target: tng_spaces.setting_row, params: {text: "${space_name}"}}
  - assert_visible: {target: tng_spaces.section_header, params: {text: "Details"}}
  - assert_visible: {target: tng_spaces.setting_row, params: {text: "Savings goal"}}
  - assert_visible: {target: tng_spaces.setting_row, params: {text: "Automatic transfer"}}
  - assert_visible: {target: tng_spaces.setting_row, params: {text: "Archive Space"}}
  # The photo tile is labelled "Manage Space" (finding).
  - assert_visible: tng_spaces.photo_tile
  - screenshot: tng_space_manage
  - tap: {target: tng_spaces.setting_row, params: {text: "${space_name}"}}
  - assert_visible: {target: tng_common.copy, params: {text: "Rename Space"}, timeout: 10}
  - assert_text: {target: tng_spaces.save_btn, equals: "Save"}
  - clear: tng_spaces.rename_field
  - tap: tng_spaces.save_btn
  - assert_text: {target: tng_spaces.input_error, equals: "Please enter a name for your Space"}
  - screenshot: tng_space_rename_empty
  - type: {target: tng_spaces.rename_field, value: "${renamed_name}"}
  - tap: tng_spaces.save_btn
  - wait_for_visible: {target: tng_spaces.setting_row, params: {text: "${renamed_name}"}, timeout: 20}
  # Savings goal.
  - tap: {target: tng_spaces.setting_row, params: {text: "Savings goal"}}
  - assert_visible: {target: tng_common.copy, params: {text: "Set a goal amount for this Space."}, timeout: 10}
  - assert_text: {target: tng_spaces.control_title, equals: "Savings goal"}
  - set_toggle: {target: tng_spaces.switch, state: "on"}
  - assert_text: {target: tng_spaces.amount_label, equals: "Goal amount", timeout: 5}
  - tap: tng_spaces.save_btn
  # The label says "Goal amount" and the error "target amount" (finding).
  - assert_visible: tng_spaces.input_error
  - screenshot: tng_space_goal_empty
  - type: {target: tng_spaces.amount_field, value: "${goal_amount}"}
  - hide_keyboard: true
  - tap: tng_spaces.save_btn
  - wait_for_visible: {target: tng_spaces.setting_value, params: {text: "${goal_display}"}, timeout: 20}
  - back: true
  - assert_text: {target: tng_spaces.space_name, equals: "${renamed_name}", timeout: 10}
  - assert_text: {target: tng_spaces.goal_title, equals: "${goal_away_display} away from goal"}
  - assert_text: {target: tng_spaces.goal_subtitle, equals: "Goal amount: ${goal_display}"}
  - screenshot: tng_space_goal
  # Archive. Archived Spaces cannot be deleted, so each run leaves one behind.
  - tap: tng_spaces.manage_btn
  - tap: {target: tng_spaces.setting_row, params: {text: "Archive Space"}, timeout: 10}
  - assert_text: {target: tng_spaces.alert_title, equals: "Archive Space", timeout: 5}
  - assert_text: {target: tng_spaces.alert_message, equals: "If you archive this Space, any money in it will be returned to your main balance."}
  - assert_text: {target: android_dialog.cancel_btn, equals: "Cancel"}
  - assert_text: {target: android_dialog.ok_btn, equals: "Archive"}
  - screenshot: tng_space_archive_dialog
  - tap: android_dialog.cancel_btn
  - assert_visible: {target: tng_spaces.setting_row, params: {text: "Archive Space"}}
  - tap: {target: tng_spaces.setting_row, params: {text: "Archive Space"}}
  - tap: {target: android_dialog.ok_btn, timeout: 5}
  - wait_for_visible: {target: tng_common.copy, params: {text: "View Archived Spaces"}, timeout: 20}
  - tap: {target: tng_common.copy, params: {text: "View Archived Spaces"}}
  - assert_visible: {target: tng_common.copy, params: {text: "Archived Spaces"}, timeout: 10}
  - assert_text: {target: tng_spaces.archived_description, equals: "Archived Spaces can't be deleted, since you may need to access past transactions."}
  - assert_visible: {target: tng_spaces.archived_item, params: {name: "${renamed_name}"}}
  - screenshot: tng_spaces_archived
---

# Spaces: create, move money, manage and archive

Opens Spaces from a savings account and walks the whole life of a Space: the
empty name error, the 30-character name limit, the automatic transfer options,
Transfer Out of an empty Space, Transfer In, rename, savings goal and archive.
Archiving returns the money to the account. Archived Spaces cannot be deleted,
so each run leaves one archived Space behind.

Known findings, checked in TNG-copy-defects: "Shouldn't be empty" for an empty
name and for a $0 amount, a $0 goal accepted silently, the "Got It" picker button,
"Transfer In", "Transfer Out" and "Manage Space" in title case, the photo tile
labelled "Manage Space", "Current (CAD)" as the transfer source, and "Goal amount"
vs "target amount".
