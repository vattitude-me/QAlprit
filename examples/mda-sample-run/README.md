# Sample MDA Run Output (`20261113_135128_android_mda`)

Real end-to-end Android execution against **Sauce Labs My Demo App (`mda-2.3.0-27.apk`)** using the [`specs/mda/`](../../specs/mda/) suite (`MDA-001` through `MDA-005`).

## Files in This Folder

- **[`report.html`](report.html)** — Self-contained HTML proof report with all step screenshots embedded as compressed WebP images and interactive status/tag filtering (`5/5 passed` in `2m 20.0s`).
- **[`summary.md`](summary.md)** — Markdown summary ready to paste into GitHub Pull Request comments.
- **[`results.json`](results.json)** — Machine-readable structured `RunResult` schema (with local file paths sanitized and runtime `appium.log` omitted).
- **[`previews/`](previews/)** — Small preview images from [`MDA-002`](../../specs/mda/mda_02_login_locked_out.md) and [`MDA-004`](../../specs/mda/mda_04_e2e_checkout.md) showing in-memory Pillow secret redaction on Android.

## Reproduce This Run Locally

1. Check environment prerequisites and connected Android device/emulator:

   ```bash
   qalprit doctor
   qalprit devices
   ```

2. Download [`mda-2.3.0-27.apk`](https://github.com/saucelabs/my-demo-app-android/releases) into `apps/mda-2.3.0-27.apk` (gitignored locally).

3. Copy the sample `.env` file with Sauce Labs My Demo App test credentials:

   ```bash
   cp config/env.example .env
   ```

4. Validate specs, flows, locators, and environment variables offline:

   ```bash
   qalprit validate --suite mda --env mda
   ```

5. Execute all 5 MDA specs (Appium 2 is started and stopped automatically):

   ```bash
   qalprit run \
     --platform android \
     --device android_emulator \
     --app apps/mda-2.3.0-27.apk \
     --env mda \
     --suite mda
   ```

6. Open your generated report in `results/<run-id>/report.html` or launch the local workbench UI:

   ```bash
   qalprit ui
   ```
