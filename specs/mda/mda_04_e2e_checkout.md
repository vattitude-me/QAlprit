---
id: MDA-004
title: End-to-end authenticated purchase checkout with sensitive data redaction
tags: [mda, checkout, e2e, regression]
platforms: [android, ios]
targets: [simulator, device]
priority: critical
preconditions:
  - Sauce Labs My Demo App APK installed
reset: clear_data
auto_accept_alerts: true
steps:
  - run_flow:
      flow: mda/login
      params:
        username: "${MDA_USER}"
        password: "${MDA_PASS}"
  - tap: mda_catalog.first_product_image
  - wait_for_visible: mda_product.title
  - assert_text: {target: mda_product.title, equals: "Sauce Labs Backpack"}
  - tap: mda_product.increase_qty
  - assert_text: {target: mda_product.qty_value, equals: "2"}
  - tap: mda_product.add_to_cart_button
  - assert_text: {target: mda_nav.cart_count, equals: "2"}
  - tap: mda_nav.open_cart
  - wait_for_visible: mda_cart.items_count
  - assert_text: {target: mda_cart.items_count, equals: "2 Items"}
  - tap: mda_cart.checkout_button
  - wait_for_visible: mda_shipping.full_name_field
  - type: {target: mda_shipping.full_name_field, value: "Rebecca Winter"}
  - type: {target: mda_shipping.address1_field, value: "Mandorley 112"}
  - type: {target: mda_shipping.city_field, value: "Truro"}
  - type: {target: mda_shipping.zip_field, value: "89750"}
  - type: {target: mda_shipping.country_field, value: "United Kingdom"}
  - hide_keyboard: true
  - tap: mda_shipping.to_payment_button
  - wait_for_visible: mda_payment.card_number_field
  - type: {target: mda_payment.cardholder_name_field, value: "Rebecca Winter"}
  - type: {target: mda_payment.card_number_field, value: "${MDA_CARD_NUMBER}", secret: true}
  - type: {target: mda_payment.expiration_date_field, value: "03/25"}
  - type: {target: mda_payment.security_code_field, value: "${MDA_CARD_CVV}", secret: true}
  - hide_keyboard: true
  - screenshot: mda_payment_redacted
  - tap: mda_payment.review_order_button
  - wait_for_visible: mda_review.place_order_button
  - assert_text: {target: mda_review.title, equals: "Review your order"}
  - screenshot: mda_review_order
  - tap: mda_review.place_order_button
  - wait_for_visible: mda_complete.title
  - assert_text: {target: mda_complete.title, equals: "Checkout Complete"}
  - assert_text: {target: mda_complete.thank_you, equals: "Thank you for your order"}
  - screenshot: mda_checkout_complete
  - tap: mda_complete.continue_shopping_button
  - assert_visible: mda_catalog.title
teardown:
  - tap_if_visible: mda_nav.open_menu
  - tap_if_visible: mda_nav.menu_logout
  - tap_if_visible: mda_nav.logout_confirm_btn
expected: Complete login, item selection, cart review, shipping, redacted payment entry, order review, and checkout completion succeed.
---

# MDA-004: End-to-end authenticated purchase checkout with sensitive data redaction

Full multi-screen customer journey showcasing reusable flows, form entry, and in-memory Pillow redaction of password, card number, and CVV fields.
