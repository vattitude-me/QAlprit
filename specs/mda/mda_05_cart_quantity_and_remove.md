---
id: MDA-005
title: Cart quantity update and item removal
tags: [mda, cart, smoke]
platforms: [android, ios]
targets: [simulator, device]
priority: high
preconditions:
  - Sauce Labs My Demo App APK installed
reset: clear_data
auto_accept_alerts: true
steps:
  - wait_for_visible: mda_catalog.first_product_image
  - tap: mda_catalog.first_product_image
  - wait_for_visible: mda_product.add_to_cart_button
  - tap: mda_product.add_to_cart_button
  - assert_text: {target: mda_nav.cart_count, equals: "1"}
  - tap: mda_nav.open_cart
  - wait_for_visible: mda_cart.items_count
  - assert_text: {target: mda_cart.items_count, equals: "1 Items"}
  - assert_text: {target: mda_cart.total_price, equals: "$ 29.99"}
  - tap: mda_cart.remove_item_button
  - assert_not_visible: mda_cart.checkout_button
  - screenshot: mda_cart_empty
expected: Adding a product updates the cart badge, cart reflects price and count, and removing the item empties the cart.
---

# MDA-005: Cart quantity update and item removal

Verifies cart state mutations and empty-cart UI transition.
