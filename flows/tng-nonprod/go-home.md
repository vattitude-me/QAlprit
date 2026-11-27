---
name: tng-nonprod/go-home
platforms: [android]
steps:
  - repeat:
      until: tng_home.nav_home
      max: 6
      until_timeout: 2
      steps:
        - back: true
  - tap: tng_home.nav_home
  - wait_for_visible: {target: tng_home.personal_accounts_title, timeout: 15}
---

# Back to Home

Presses Back until the bottom navigation shows, then opens the Home tab. Every
TNG journey ends here so the next one can start in any order.
