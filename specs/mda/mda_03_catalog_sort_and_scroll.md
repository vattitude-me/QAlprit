---
id: MDA-003
title: Sort catalog by price descending, inspect top product, and scroll catalog
tags: [mda, catalog, navigation]
platforms: [android]
targets: [simulator, device]
priority: medium
preconditions:
  - Sauce Labs My Demo App APK installed
reset: clear_data
auto_accept_alerts: true
steps:
  - wait_for_visible: mda_catalog.title
  - tap: mda_catalog.sort_button
  - wait_for_visible: mda_catalog.sort_price_desc
  - tap: mda_catalog.sort_price_desc
  - assert_text: {target: mda_catalog.first_product_title, equals: "Sauce Labs Fleece Jacket"}
  - assert_text: {target: mda_catalog.first_product_price, equals: "$ 49.99"}
  - tap: mda_catalog.first_product_image
  - wait_for_visible: mda_product.title
  - assert_text: {target: mda_product.title, equals: "Sauce Labs Fleece Jacket"}
  - screenshot: mda_fleece_detail
  - back: true
  - scroll_until_visible:
      target: mda_catalog.product_by_title
      params: {title: "Sauce Labs Onesie"}
      direction: down
      max_swipes: 5
  - assert_visible:
      target: mda_catalog.product_by_title
      params: {title: "Sauce Labs Onesie"}
  - screenshot: mda_sorted_catalog_bottom
expected: Sorting reorders highest-priced item first, tapping opens Fleece Jacket detail, back returns to catalog, and scrolling locates Sauce Labs Onesie.
---

# MDA-003: Sort catalog by price descending, inspect top product, and scroll catalog

Exercises dynamic catalog sorting, product detail navigation, back navigation, and `scroll_until_visible`.
