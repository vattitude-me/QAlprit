# Changelog

All notable backend, UI, packaging, and documentation changes to **QAlprit** are recorded in this file, along with root causes and prevention notes ("Lessons Learned") from resolved issues so past mistakes are never repeated.

---

## Upcoming

Ideas planned for a later release. Nothing here ships yet.

- Open a saved workflow in the recorder to add, remove or reorder steps, then save it back under the same name. Today the recorder only starts empty, and saving under an existing name replaces that workflow after you confirm.

---

## [v0.1.41] - 2026-11-21

### Added

- **Handoff documentation.** `AGENTS.md` now maps every module, explains how a test goes from recording to report, and lists the open work, so a new agent can pick the repo up cold.
- **Record on a USB Android phone.** Pick a plugged-in phone (shown as *USB phone*, with its model name) on the Record page and use the phone itself. The recorder listens to the phone's touchscreen with `adb shell getevent` and turns each gesture into a step: tap, long press, swipe (with how far the finger moved), Back (edge swipe or a hardware Back key), and typing (read from the text field). No app file is needed: the app already installed on the phone is used, and **Restart app** (on by default for phones) reopens it with its login and data kept. Saved tests use `reset: none`, so replays never wipe the phone. When the environment names no app, the flow starts with `launch: {app: <package>, restart: true}` for the app that was open.
- `long_press` step (`long_press: screen.element` or `{target, duration}`), replayed with `mobile: longClickGesture` on Android and `mobile: touchAndHold` on iOS.
- `launch` accepts an installed app id and `restart: true`.
- `POST /api/recorder/start` takes `start_mode`: `clear_data`, `restart` or `as_is`. Without it, `fresh_start` behaves as before.
- **Record across apps.** Opening an app from the home screen or recents while recording on Android becomes a `launch` step (`restart: true` the first time). A switch the app makes on its own, such as a browser sign-in, is not a step. Taps on the home screen itself are not recorded.
- **A screenshot for every recorded step.** The recorder keeps the screen each step led to (always redacted), shows it as a thumbnail in the step list, and saves it with the workflow under `references/flows/<workflow>/` with a `manifest.json`. Runs copy the matching screenshot next to each replay screenshot (`*_REF.png`) and store how many pixels changed (`reference_mismatch`). A flow edited after recording skips the stale screenshot and says so.
- **Jira and Confluence references.** Journeys take `references:` (issue keys or links). `qalprit refs`, `POST /api/references/fetch` and the journey settings' **Fetch** button pull them with `ATLASSIAN_BASE_URL`, `ATLASSIAN_EMAIL` and `ATLASSIAN_API_TOKEN` from `.env` and save them under `references/docs/`. The token is only sent to the configured site. Jira fields named like acceptance criteria, steps or expected results are included.
- **AI screenshot check.** `qalprit run --ai-review`, `qalprit review <results dir>`, the run dialog's **Check screenshots with AI** and **Review → Check with AI** send each step's recorded and replay screenshot, plus the reference text, to Claude (`claude-opus-5-5`, structured JSON output, server-side refusal fallback). Each step gets *pass*, *fail* or *unclear* with a reason and the requirement it checked; the test gets a summary and the requirements no step showed. Verdicts never change pass or fail. Results go to `ai_review.json`, `results.json` and the report. Needs `ANTHROPIC_API_KEY`.
- **Review dialog.** **Runs & reports → Review** shows recorded and replay screenshots side by side with the AI verdict, and **Looks right** / **Wrong** with a note per step (`POST /api/runs/review`). Decisions are saved in `results.json` and the HTML report. The Review button counts flagged steps nobody has reviewed yet.
- New dependency: `anthropic>=2.10,<3` (bundled in the Windows and macOS installers).
- **AI check provider.** The AI check can run on the Antigravity CLI (`agy`) or the GitHub Copilot CLI (`copilot`) as well as the Anthropic API. Pick one with `--ai-provider anthropic|agy|copilot` on `qalprit run` and `qalprit review`, the **Provider** menu in the run options and the Review dialog, or `QALPRIT_AI_PROVIDER` in `.env`. The CLIs run in a throwaway folder that holds the redacted screenshots as JPEGs plus a `review_request.md`, with no flag that approves tool use, and use their own sign-in. `QALPRIT_AGY_BIN`, `QALPRIT_COPILOT_BIN` and `QALPRIT_<NAME>_MODEL` override the program path and model. `ai_review.json` records the `provider`. Checked against `agy` 1.5.14 on macOS. The Copilot CLI path follows its documented `-p`, `--silent` and `--add-dir` flags but has not been run against a real install yet.
- Fix: the AI check showed "The anthropic package is not installed" for any error when that package was missing, hiding the real reason.
- **Jira & Confluence connection form.** **Settings & setup → Jira & Confluence** takes the site address, an account email and an API token or PAT, with optional separate site, email and token for each product. **Test connection** (`POST /api/atlassian/test`) reports Jira and Confluence separately, using values typed but not yet saved. **Save** (`GET`/`POST /api/atlassian/settings`) writes the keys to the workspace `.env` (new file is owner-only, other lines and comments are kept). Tokens are never sent back to the UI, only whether each is saved. A blank token keeps the saved one, and **Remove saved tokens** clears them. `JIRA_EMAIL`, `JIRA_API_TOKEN`, `CONFLUENCE_EMAIL` and `CONFLUENCE_API_TOKEN` are new; a product with its own token uses its own email (blank means a Bearer PAT), otherwise it falls back to `ATLASSIAN_*`. Missing-setup and 401 messages now point at Settings & setup instead of `.env`.
- **References on workflows.** A workflow takes `references:` in its front matter and has the same References field with **Fetch** in its Settings tab. A journey's effective references are its own plus those of every workflow it runs (`effective_references()`), used for the run result, the AI check, `qalprit refs --id` and the journey's settings note. Saving steps or recording again keeps a workflow's links.
- Android 16: the "Android App Compatibility" (16 KB page size) dialog is dismissed automatically when an element is not found because that dialog covers the app (`_dismiss_android_compat_dialog`). `appWaitActivity: "*"` is set on the `android_device` and `android_emulator` profiles so apps that open a different first activity start cleanly.
- Sample journey MDA-004 hides the keyboard after the address field so the City field stays visible.

### Changed

- Gestures done on the device itself (emulators too) are queued and hit-tested against the screen as it was when the finger landed. Before, a tap was dropped if the recorder was busy for more than a second, and a fast tap could match an element on the next screen.
- Key presses on the on-screen keyboard are no longer recorded as taps on the app element behind the keyboard. The recorder reads the keyboard's position from `dumpsys window InputMethod`.
- The touchscreen is picked from all input devices by preferring a multi-touch screen marked `INPUT_PROP_DIRECT`, so fingerprint sensors and styluses are skipped.
- Stopping a recording on a USB phone no longer shuts down running emulators, and the emulator Stop button is hidden while a phone is selected.

### Lessons learned

- Linux input only reports axis values that changed. A second tap on the same spot sends no X/Y, so the start point is taken at the first `SYN_REPORT` after the finger lands, not on the first X/Y line.
- Android leaves the on-screen keyboard out of the app's element tree. Any recorder that hit-tests real touches has to cut out the keyboard area first.
- A step's screenshot must be the screen *after* the step. Captures that happen while the next gesture is being handled belong to that gesture, so the recorder keeps the pre-touch screen for the previous step and only then refreshes.
- Every save path for a journey has to pass new fields through. The Workbench's step-only save rebuilt the spec from scratch and would have dropped `references:`.

---

## [v0.1.40] - 2026-11-18

### Fixed

- Android locators with a bare resource id, such as `id: Button`, now find their element. Appium's `id` strategy turns a bare id into `<package>:id/Button`, so apps that expose bare ids (Jetpack Compose test tags, for one) never matched and the step timed out with `locator_not_found`. QAlprit now matches both `Button` and `<package>:id/Button`. Fully qualified ids and iOS are unchanged.

- Choosing an app on the Record, Inspect or Run page no longer snaps back to the Settings app. A silent workspace reload (window focus, save, upload) copied the Settings "App in use" into every page picker. A reload now only fills a picker that is still empty.

### Changed

- The recorder step list, its edit bar and the run step trace show the element name without the screen prefix (`toggle_toggle`, not `recorded_flow.toggle_toggle`). The full name is in the tooltip, the editors and the saved files.

### Lessons learned

- A shared "current app" setting must only seed page pickers. If a periodic refresh writes it back into them, it overrides what the user chose.
- When a locator times out but the element is visible in the failure screenshot, compare the locator with `resource-id` in `page_sources/*_FAIL.xml`. A missing package prefix in the XML explains it.

---

## [v0.1.39] - 2026-11-18

### Changed

- `qalprit ui` in a browser no longer uses the folder you run it from. It opens the workspace saved in Settings, else `~/Documents/QAlpritWorkspace`, the same as the desktop app. Running it inside a source checkout used to turn the checkout into the workspace, so recorded workflows, screens, datasets and results landed in tracked files. Pass `--workspace <folder>` (alias `--project-root`) to open a specific folder. QAlprit does not add files to a folder you pass this way.

### Added

- Settings > Storage & cleanup has a **Choose folder** button for the workspace and the SDK data folder. It opens the operating system's folder chooser (macOS, Windows, and Linux with zenity or kdialog), then Save & Apply switches the workspace without a restart.
- A workspace inside a git repository gets a `.gitignore` containing `*`, so its files never show up in the repository's `git status`. QAlprit skips this when the folder is a repository itself, already has a `.gitignore`, or holds files git tracks. The Settings message says when this applies.

### Fixed

- Apps in subfolders of `apps/` (for example `apps/android/tng-non-prod.apk`) now appear in the app lists, and can be removed from the UI.

### Lessons learned

- A tool that edits files must never treat the current folder as its data folder by default. Keep user data in a folder the tool owns, and take a source checkout only when asked with an explicit flag.

---

## [v0.1.38] - 2026-11-18

### Fixed

- The top menus (More, device and the run-card menu) no longer sit open on top of the page in the macOS app. They are native `popover` elements, and WKWebView turns that feature off for an app linked against an old macOS SDK. The CI build used SDK 12.1, so every menu showed as a permanent box. This happened in earlier releases too, and it came back because the local build used a new SDK and hid the problem.
- `build_mac.sh` now relinks the app's executables against the current SDK with `vtool` and stops the build if the result is older than SDK 17.
- The UI no longer depends on the Popover API alone. If the browser lacks it, a small fallback gives the three menus the same open, close, outside-click and Escape behavior.

### Lessons learned

- A UI that works from `qalprit ui` in a browser, or from a locally built app, can still break in the released app. The CI PyInstaller bootloader is linked against SDK 12.1, and WKWebView hides newer web features (the Popover API here) from apps linked that way. Check a released DMG, not only a local build.
- Never ask the browser for `:popover-open` directly. Use `isPopOpen(el)`, which also works when the Popover API is missing.
- Run `vtool -show-build QAlprit.app/Contents/MacOS/QAlprit` on every macOS build. The `sdk` line must be 17 or higher.
- When a new web platform feature drives visible UI, add a fallback and test it with the feature switched off. `tests/test_webview_compat.py` guards the menus.

---

## [v0.1.37] - 2026-11-18

### Fixed

- Saving a journey after "Validate workspace" no longer fails with "null is not an object (evaluating 'b.hidden = false')". The editor finds its status banner by a marker class, and the status updates replaced the whole class list, so the next Save could not find the banner. A `setBannerKind()` helper now changes only the status class. Nine places had the same pattern, including the settings and run banners.
- Runs from a workflow or journey page use the app chosen in the Run Configuration card. They used to take the app from the top-right pill, so an iOS app picked there could launch for an Android run. "Re-run" on the Runs page now picks an app that fits the run's platform.

### Added

- Run Configuration has an **App** picker, filtered to the chosen platform. It starts on the app the workflow was recorded against, then the top-right app if it fits, then the first app that fits. The device and app you pick stay selected while you browse rows.
- Recorded workflows remember their app. `flows/*.md` gains an optional `apps:` map (`android: apps/x.apk`, `ios: apps/X.app`), saved by the recorder and kept when you edit the workflow. A journey uses the app of the first workflow it runs.

### Lessons learned

- Do not assign `className` on an element other code looks up by class. Toggle only the classes that change.

---

## [v0.1.36] - 2026-11-17

### Fixed

- Recording now starts from a fresh app. The recorder clears the app's data before the first screen, the same way test runs do, so a replay begins where the recording did. Turn off "Fresh app" next to the Start button to record from the app's current state.


### Switches and saving (`steps/`, `recorder.py`, `ui/index.html`)
- New `set_toggle` and `assert_toggle` steps with `state: on | off`. A recorded tap on a switch flips it, so a rerun could turn it off when it was already on. `set_toggle` reads the state first and taps only when needed. The recorder now saves `set_toggle` with the state the switch ended in, and the assert bar has a **Switch state** button that records `assert_toggle`.
- The steps editor has a state dropdown (On/Off) for both steps.
- The workflow and journey editor shows **Unsaved changes** after any edit, asks before switching to another test and warns before the window closes. Saving now shows a toast, because the old confirmation sat below the step list and was easy to miss.

### New `enter_otp` step (`otp.py`, `steps/interaction_steps.py`, `recorder.py`, `ui_server.py`, `ui/index.html`)
- A recorded flow no longer replays the OTP you typed while recording, which fails on the next run. `enter_otp: login.otp_field` stops the run, asks for the current code, and types it into the field.
- Workbench: a prompt appears in the live run strip with a code box. Terminal: the run asks on the console. CI: set `OTP` in `.env` or a dataset column and nothing pauses. With none of these the step fails with a message that says what to set, instead of hanging.
- Recorder: the type bar has an **Enter OTP** button. It types the code you give into the device but saves only `enter_otp`, so no code lands in the flow, dataset or report.
- The captured code is masked everywhere and stored as `${OTP}` for later steps in the same run.

---

## [v0.1.34] - 2026-11-17

### Devices run in the background (`background_devices.py`, `emulator_manager.py`, `driver_factory.py`)
- Android emulators start with `-no-window -no-audio` and iOS simulators start without opening Simulator.app, so no device window covers the Workbench. The recorder mirror shows the screen instead.
- This applies however the device starts: from the device menu, from the recorder, or from a run where Appium boots the AVD (`appium:avdArgs`) or the simulator (`appium:isHeadless`).
- New setting "Show the device window" under Settings, Devices, for people who want the window. It is off by default.
- QAlprit records the devices it starts in `started-devices.json` next to `settings.json`, and drops entries for devices that are no longer running.
- The top bar shows a background device as "Running in background", with Stop. For an iOS simulator it also has Show window (`POST /api/emulator/show_window`). An Android emulator started without a window cannot show one later, and the message says to turn the setting on and start it again.

### Clean quit (`desktop.py`, `ui_server.py`, `cli.py`)
- Quitting the Workbench stops the recorder, any run, any device boot still in progress, and the Appium servers they started. It then shuts down the background devices QAlprit started. Devices you started yourself, or started with a visible window, keep running.
- The cleanup also runs on Cmd+Q and the window close button. pywebview's Cocoa host exits the process from C on Cmd+Q, so Python `finally` blocks never ran there before.

### Ask before quitting mid-work (`desktop.py`, `ui_server.py`)
- Quitting while a test run is going, while the recorder has steps you have not saved, or while a device is still starting now asks first, with "Keep working" and "Quit anyway". It names what would be cut short.
- It uses the native dialog on each platform (NSAlert on macOS, a message box on Windows) and covers Cmd+Q, Alt+F4 and the window close button. If nothing is in progress, the app quits without asking.

### Test installers without publishing (`.github/workflows/publish.yml`)
- The PyPI upload now runs only for a published GitHub Release. Running the workflow by hand builds the Windows `.exe` and macOS `.dmg` as downloadable files for testing and publishes nothing.

### Progress you can follow (`recorder.py`, `ui/index.html`)
- The recorder shows each phase (starting Appium, starting the device, opening the app, capturing the first screen) with elapsed seconds. After 45 seconds it says it is still working and that Stop cancels.
- Stop now works while the recorder is still starting. It kills the Appium server it launched and the half-started session is discarded.
- The live mirror refreshes every 0.8 seconds instead of 2, and the status polls every 0.7 seconds instead of 1.1.
- Device boots report progress in the device menu and end with a toast that says the device is ready or why it failed.

### Run tests page (`ui/index.html`)
- Running has its own page in the sidebar instead of a modal. The modal hid errors behind its grey backdrop.
- Two steps replace eight dropdowns. "What to run" is One test, A suite or All tests, with one test picker that groups journeys and workflows. "Where to run it" holds the platform, environment, device and app. Retries, redaction and data rows sit under Options, in plain words.
- A summary card shows the choices before you start, and errors appear on the page in that card until you change something.
- Device names say "running" or "off, starts when needed" instead of `[Booted]`, `[Shutdown]` and `[Offline AVD]`, and iOS versions read "iOS 26.5" instead of "iOS 26 5".
- Toasts now show above any open dialog.

### Runs & reports (`ui/index.html`)
- The first column is the run date in local time ("Today, 1:12 PM", "Yesterday, 9:05 AM", "Nov 14, 4:30 PM"). Hover shows the full timestamp and run id.

### Clearer run errors (`workbench_api.py`)
- Missing `${variables}` are grouped into one line that names the test and says where to add a value (a data row or the environment), instead of one repeated line per variable and per step.

### Recorder starts each new test empty (`recorder.py`, `ui_server.py`, `ui/index.html`)
- Saving a workflow clears the recorded steps, so "Record a new test" no longer shows the workflow you just saved. The test name changes to an unused one such as `new-test-2`.
- "Record a new test" asks before it throws away steps you have not saved. Cancel keeps them on the page.
- New "Clear all" button next to Undo last, and `POST /api/recorder/clear`. A live recording keeps running.
- Save asks before it replaces a workflow that already has that name. Before, it replaced it without asking.

### Journeys use their workflow's data rows (`workbench_api.py`, `validator.py`, `ui/index.html`)
- A journey that runs a workflow with data rows now runs once per row, using that workflow's rows. Before, it looked only for rows on the journey itself and stopped with "No selected datasets found".
- Validation counts a workflow's data row columns as values for its params when a journey calls it, so the journey no longer has to pass `username` and `password` itself.
- The Run tests page ticks "Run once for each data row" when the picked test or its workflow has rows, and shows the row count. Unticking it stays unticked until you pick another test.

### Lessons learned
- A modal dialog lives in the browser's top layer, so toasts and banners outside it render under its backdrop. Show errors inside the dialog, or put the toast host in the top layer too (`popover="manual"`).
- Recorded workflows can contain `${name}` values typed by hand. The recorder only registers variables it creates itself, so a hand-typed one has no source until a data row or environment value is added. The run error now says so.

---

## [v0.1.33] - 2026-11-16

### Guided start (`ui/index.html`)
- New welcome screen on launch. It explains what QAlprit does in a few lines, then asks for the platform (Android or iPhone), the app file and the device, and opens the recorder with the test named.
- Apps you added before show as cards, and new ones can be dropped in or picked from disk. Devices list as "Running now", "Starts automatically" or "A real phone plugged in", without internal profile ids.
- Returning users get "Skip to Testing space". A checkbox turns the welcome screen off, and "Show the welcome guide" in the top bar menu brings it back.

### Testing space (`ui/index.html`)
- The app now opens on a home view. It shows counts, a setup checklist for new workspaces, every workflow and journey as a card, and the latest results.
- Each card has Run, plus a menu with Open and edit, Rename, Duplicate and Delete. Run starts right away with the platform, app and device from the top bar and the workflow's selected dataset rows. "Custom run..." keeps the full run dialog.
- Search and an All, Workflows and Journeys filter sit above the cards.

### Create, rename and delete everywhere (`workbench_api.py`, `ui_server.py`)
- Rename for workflows and journeys (`POST /api/flows/rename`). It moves the dataset CSV with the file and rewrites every `run_flow` that points at a renamed workflow, in steps and teardown.
- Delete for runs (`POST /api/runs/delete`, which removes a batch and its per-row runs), app files (`POST /api/apps/delete`), saved elements (`POST /api/screens/delete`) and environments (`POST /api/environments/delete`, which keeps at least one).
- Duplicating or deleting a workflow now copies or removes its dataset too.
- Every delete checks its path stays inside the workspace and asks for confirmation in the UI.

### Clearer navigation and copy (`ui/index.html`, `recorder.py`)
- Sidebar items have a one line description: Testing space, Record a test, Workflows, Journeys, Runs & reports, Elements, Settings & setup.
- Labels use plain sentence case ("Capture screen", "Find elements", "App") instead of tool jargon such as "Inspect XML Dump" or "App Binary".
- Elements has a search box, wraps long locators instead of cutting them off, and shows more rows.
- Fixed run step lists overflowing their card, app actions stacking in Settings, and the unstyled file picker.
- The Settings apps table keeps sizes on one line and breaks long app IDs at the dots. A running Android emulator no longer shows its serial twice in the device list.

### Device list (`devices.py`)
- The Android emulator list now reads the same AVD folder that Appium and the emulator manager boot from. Before, it could offer an emulator from `~/.android/avd` that failed to start with "Avd ... is not available".

### Workspace loading (`datasets.py`, `ui_server.py`)
- Deleting the last data row of a workflow left a CSV with only a header. The next overview load raised "CSV contains headers but zero data rows", the server closed the connection, and the Workbench showed "QAlprit is not responding" with every list empty. The files were never lost. A header-only CSV now loads as a workflow with variables and no rows. Importing one is still rejected.
- If a dataset CSV still cannot be read, the overview skips it and names the file in a warning instead of failing the whole request.
- GET endpoints now return a JSON error with the reason, as POST endpoints already did, instead of dropping the connection.

### Docs
- README and AGENTS.md describe the welcome guide and the new view names.
- Website and README screenshots are retaken from 0.1.33, with live recordings on the iOS Simulator and an Android emulator. The README adds a Testing space screenshot.

### Lessons learned
- v0.1.32 changed a lot under the hood but looked the same, so users saw no improvement. UI work now gets a local .dmg build and a walk-through with screenshots before release.
- A file the app writes must be one it can read back. The dataset editor saved a header-only CSV that the loader rejected, and one unguarded loader call took down the whole Workbench. Loaders that feed the overview now degrade per file.

---

## [v0.1.32] - 2026-11-16

### Live recorder reliability (`recorder.py`, `driver_factory.py`, `inspector.py`, `error_text.py`)
- The recorder now spots the iOS "Save Password" sheet and similar system prompts and taps "Not Now" for you. Before, the sheet covered the app, every tap missed, and the mirror looked frozen.
- iOS sessions turn off password autofill (`appium:autoFillPasswords: false`), so the sheet shows up less often to begin with.
- The iOS mirror takes screenshots with `xcrun simctl io <udid> screenshot`, which includes the keyboard. WDA screenshots leave it out. If simctl fails twice, the mirror goes back to WDA.
- Keyboard detection on iOS checks for a visible `XCUIElementTypeKeyboard` element instead of trusting WDA's flag, which stays "shown" after the keyboard closes.
- If the Appium session dies, the recorder reports "Session lost" with a next step and keeps the recorded steps so you can still save them.
- Only editable fields count as sensitive. Short tokens such as "pin" must stand alone to match, so labels like "Spinner" are no longer masked.
- New `error_text.py` turns stack traces into one readable line with a hint (`friendly_error`, `clean_error`).

### Typed values as variables (`recorder.py`, `workbench_api.py`, `ui_server.py`)
- "Save" can turn typed values into `${variables}` (on by default). The variable name comes from the field, so a login becomes `${username}` and `${password}`.
- Saving seeds `datasets/flows/<name>.csv` with a `recorded` row and marks password columns as secret. Saving again keeps rows you added. If "Also create a journey" is on, the journey gets the same dataset.
- The password stays out of the flow file, reports, and logs.

### Flows remember their platform (`spec_loader.py`, `workbench_api.py`, `validator.py`)
- Flows accept an optional `platforms:` list, like journeys. The recorder writes the platform it recorded on, and editing a flow keeps it.
- Validation only checks locators for those platforms. A flow recorded on iOS no longer fails with "missing definition for platform 'android'". Flows without the field behave as before.
- The Workflows view opens a single-platform flow on that platform.

### Workbench UI (`ui/index.html`, `desktop.py`)
- Replaced every browser `alert`, `confirm`, and `prompt` with in-app toasts and dialogs. These work in the .dmg and .exe, where pywebview blocked or hid the browser ones.
- The Record panel is simpler. Removed the nested CSV import (it stays in the Workflows rail), the Wait mode, the duplicate redaction checkbox, and the extra hide keyboard button. Added gesture buttons (swipe up and down, back, hide keyboard, wait 2s), Enter and Escape shortcuts, and cancel buttons on the type and assert panels.
- The recorder sends one action at a time and shows "Working..." while it runs, so double clicks no longer record duplicate steps.
- Secret values show as dots in the step list and use password inputs when edited.
- Starting a new recording asks before it discards unsaved steps. Save stays disabled until there are steps, and it then opens the workflow on its Variables tab.
- Desktop builds allow downloads (`ALLOW_DOWNLOADS`), so CSV export works in the .dmg and .exe.
- When the server stops responding, the UI says so in plain words instead of showing "Failed to fetch".

### Docs and diagrams
- `README.md` covers the desktop installers and a new "Record in the Workbench and replay with CSV data" walkthrough.
- `docs/authoring-guide.md` has a new section on flow files (`platforms:`), recording with variables, and CSV datasets, including `${ENV_VAR}` cells for real credentials.
- `docs/agent-instructions.md` and `AGENTS.md` describe recorded flows, their datasets, and `error_text.py`.
- `web/architecture.svg` and `docs/architecture.drawio` show "Record or Author" and one run per CSV row. The website's recorder card mentions dataset replay.
- `docs/QAlprit-Overview.pptx` shows the current version and test count.

### New logo (`web/`, `docs/logo/`, `ui/`, `report.html.j2`, `packaging/`)
- Replaced the cartoon phone logo with a flat mark: a magnifier with a check inside, on a teal tile. A wide lockup with the wordmark lives in `qalprit-logo.svg`.
- The same mark is used for the web favicon, the Workbench header, both report headers, the macOS `.icns` and the Windows `.ico` (drawn by the build scripts).
- Swapped the mark in the three checked-in sample reports in place. Their embedded screenshots are unchanged because the original run folders no longer exist.

### README and website (`README.md`, `web/index.html`, `web/styles.css`, `web/screenshots/`)
- README now leads with the desktop app and a five step walkthrough: install, record by clicking, save, add data rows, run. CLI, CI and hand-edited files moved to a section for engineers.
- Website hero and copy are click-first, with a "How it works" tour and a download call to action.
- Added five real Workbench screenshots (iOS login recording, Android add to cart recording, workflow steps, datasets, runs) taken against the public Sauce Labs demo app.
- Added `web/social-card.png` (1200x630) for link previews.

### Root causes and lessons learned
- **Frozen mirror on "Save Password".** iOS shows the sheet above the app after a password field is filled. The recorder kept tapping app coordinates under it. System prompts need their own detection pass on each refresh, before any hit test.
- **Keyboard missing from the mirror.** WDA screenshots render the app layer only. Use the simulator's own screenshot when you need what the user sees.
- **Recorded iOS flows failed validation.** Flows had no platform field, so the validator checked them for both platforms. Anything the recorder creates should carry the context it was recorded in.

### Tests
- Added `tests/test_recorder_reliability.py`: prompt detection, sensitivity rules, friendly errors, typed value parameterization, saving with variables and dataset seeding, iOS-only flow validation, iOS keyboard detection, and simctl screenshots with WDA fallback.
- Updated the UI structure tests for the new Record panel and the in-app dialogs.
- Live check on the iOS Simulator: recorded a login, saved it with variables, added a second CSV row, and ran both rows. Both passed and the password appeared nowhere in the output.

---

## [v0.1.31] — 2026-11-16

### macOS DMG & Workbench Redesign (`src/qalprit/ui/index.html`, `driver_factory.py`, `recorder.py`, `emulator_manager.py`, `ui_server.py`, `workbench_api.py`)
- **Daily-First Navigation (`Workflows` Default + `Settings & Setup` Sub-Tabs)**: Promoted daily views to the top of `WORKBENCH` (`Workflows` default landing page, `Journeys`, `Record`, `Runs & Reports`) and moved 1-time setup (`Setup & Devices`, Environment & Package Config, App Binaries, Toolchain Doctor, Storage & Cleanup) into clean sub-tabs inside **`Settings & Setup`** (`Environments & Apps`, `Devices & Toolchain`, `Storage & Cleanup`), eliminating overlapping tables and card clutter.
- **Auto-Booting on `Start recording` & `Run`, Stop-All Cleanup, Flow Naming & Hide Keyboard (`src/qalprit/driver_factory.py`, `recorder.py`, `ui/index.html`)**:
  - Updated `_ensure_ios_simulator_ready()` in `create_appium_driver()` so any live `Start recording`, `Workflow`, `Journey`, or CLI run automatically boots a shutdown macOS iOS Simulator and populates valid `udid` + `platformVersion` (eliminating `'' cannot be coerced to a valid version number` without needing a separate Boot click).
  - Simplified the `Record` toolbar (removed redundant `Boot` button; **`Start recording`** boots device + app, **`Stop recording`** ends recording + shuts down the virtual emulator/simulator, replaced confusing **`Screen tag`** input with synced **`Flow name`** deriving clean locator prefixes).
  - Cleared `#recPhoneImg`, `#recOverlays`, and elements immediately in `RecorderManager.stop()` and `applyRecorderState()` whenever stopped or shut down.
  - Added **`Hide keyboard`** (`#btnRecHideKeyboard` & `#btnRecTypeHideKb`), verified `is_keyboard_shown()` after `self._driver.hide_keyboard()` (falling through to keyboard dismiss keys, `mobile: hideKeyboard`, safe header tap, and scroll-drag dismiss when XCUITest returns silently), filtered `XCUIElementTypeKeyboard` / `XCUIElementTypeKey` boxes out of `inspector.py` overlays (so keyboard outlines never block app CTAs like Login), treated accessible React Native `XCUIElementTypeOther` CTAs as clickable, and widened `.rec-step-row` action badge track to prevent `hide_keyboard` label overlap.
  - Made `#recStartForm` the single source of truth on the Record page (hiding competing topbar `.hide-on-record` switchers while recording), preserved `avd:<name>` and `sim:ios:<udid>` dropdown choices into `_resolve_device_profile()` (shutting down mismatched virtual devices before switching targets), and ensured `Stop recording` kills both Android emulators (`adb emu kill` + `_emulator_proc`) and iOS Simulators (`xcrun simctl shutdown` + closing `Simulator.app`).
  - Added step reordering (`↑` / `↓` + drag-and-drop) and step duplication (`Duplicate` / `⧉`) to both the Live Recorder (`RecorderManager.update_step` + `/api/recorder/update`) and Workflows/Journeys Steps Editors, while hiding unnecessary value/secret/update inputs for `tap`, `assert_visible`, `back`, and `hide_keyboard` steps.
- **Resizable Split Panels, Default `Steps` Tab & `Record New` CTA (`src/qalprit/ui/index.html`)**:
  - Added draggable `split-handle` resizers (clamped `min`/`max` limits + `localStorage` persistence + double-click reset) across Sidebar, Workflows, Journeys, Dataset/Run Config, Recorder, and Runs & Reports panels, and widened the default Workflows/Journeys rail (`272px`) so names stay readable.
  - Changed default active sub-tab on **Workflows** (`flowTab`) and **Journeys** (`specTab`) from `Variables & datasets` to **`Steps`**.
  - Added prominent **`Record New`** buttons in the Workflows rail and header (`#btnRailRecordNew`, `#btnWorkflowRecordNew`) that launch straight into the Live Recorder.
  - Made topbar `.ctx-chip` labels cleanly ellipsize on narrower windows.
- **Added Emulator / Simulator Shutdown API (`src/qalprit/emulator_manager.py`, `src/qalprit/ui_server.py`)**: Implemented `EmulatorJobManager.shutdown()` and `POST /api/emulator/shutdown` to shut down running Android emulators (`adb emu kill`) and macOS iOS Simulators (`xcrun simctl shutdown <udid>`), wired to `#devicePopoverMenu`, per-row `Shutdown` buttons, and `Stop recording`.
- **Fixed `Device 'android_emulator' is for 'android', not 'ios'` Mismatch**: Fixed `/api/recorder/start`, `/api/inspect/live`, and `RunJobManager.start()` defaulting `device_profile` to `"ios_simulator"` when `platform == "ios"`, added built-in `ios_simulator`/`android_emulator` fallback in `_resolve_device_profile()`, and synced `Platform` ↔ `Device` ↔ `App Binary` across Record/Journeys/Workflows.
- **Fixed Top-Right Overflow Menu (`#topMoreMenu`), Horizontal Grid Clipping & Settings Keys**: Anchored `.menu-popover` cleanly below triggers, removed duplicate `"Workflows library"`, constrained CSS grid tracks (`minmax(0, 1fr)`, `min-width: 0`), and aligned `loadSystemPreflight()`, `loadCleanupTargets()`, `btnCleanupSelected`, and `btnRecSaveAll` with exact backend JSON keys.

### Root Causes & Lessons Learned
- **iOS `'' cannot be coerced to a valid version number` on Shutdown Simulator**: Leaving `platformVersion` blank while no iOS Simulator is booted caused Appium XCUITest to pass an empty runtime string during version parsing. Resolving and booting the target simulator in `create_appium_driver()` guarantees every run and recorder session starts cleanly even from a cold Shutdown state.
- **Hardcoded Fallback `android_emulator` vs Nested UI Popups**: Omitting or clearing `device_profile` on `/api/recorder/start`, `/api/inspect/live`, or `/api/runs/start` previously defaulted to `"android_emulator"` even when `platform == "ios"`. Always derive platform-matched defaults on the server and expose live platform/device selection explicitly.
- **CSS `[popover]` Placement & `minmax(0, 1fr)`**: Native `[popover]` defaults to `(0, 0)` unless anchored via coordinates, and CSS Grid `1fr` expands to `min-content` width unless written `minmax(0, 1fr)` with `min-width: 0` on ancestors.

### Tests
- Added `test_hide_keyboard_ios_continues_when_silent_no_op` in `tests/test_driver_factory.py` and `test_ui_redesign_layout_ios_boot_and_api_contracts`, `test_ios_default_device_profile_and_emulator_shutdown`, `test_ios_auto_boot_in_driver_factory_and_recorder_stop_clears_preview`, and `test_inspector_ignores_ios_keyboard_overlays_and_marks_cta_clickable` in `tests/test_datasets_and_redesign.py`, and verified all views via headless Chrome DevTools DOM + screenshot checks.

---

## [v0.1.30] — 2026-11-16

### Desktop Workbench Redesign (`src/qalprit/ui/index.html`)
- **Modern Dark IDE Workbench Shell**: Replaced the 4-step wizard with a sleek dark-slate + teal (`#2dd4bf`) desktop shell shared across both `QAlprit.exe` and `QAlprit.app` (`.dmg`), featuring a compact top context bar (`Env`, live device status dot, active app binary), left Workbench & Library sidebar (`Setup & Devices`, `Record`, `Journeys`, `Runs & Reports`, `Workflows`, `Screens & locators`, `Settings`), and live bottom status bar (`Appium 2`, `Emulator`, `Toolchain`, `v0.1.30`).
- **Redesigned Record Workspace (`Record`)**: Clean single-line toolbar (`Screen tag`, `Session target`, `Redact secrets`, `Import workflow`, `Start/Stop recording`), phone bezel live mirror with `Tap / Type / Assert text / Wait` modes, editable `STEPS` sequence, and `INSPECTOR` pane showing live locator + redacted XML snippet (`src/qalprit/inspector.py`).
- **Variables & CSV Datasets Workspace (`Workflows` & `Journeys`)**: Added tabbed detail views (`Steps`, `Variables & datasets`, `Settings`) with CSV import/export, row selection checkboxes, `${ENV_VAR}` badges, masked secret cells (`•••••• secret`), live `Step 1 preview`, and right-side `Run Configuration` (`Dry run` & sequential `Run N datasets`).
- **Batch Runs & Proof Inspector (`Runs & Reports`)**: Added grouped batch rows (`batch_report.html`) with expandable dataset child rows (`HTML · MD · JUnit`) and a right-side failure inspector showing step error details, clickable screenshot thumbnail + lightbox, step timeline, and `Re-run dataset`.

### Backend & CLI Dynamic Variables (`src/qalprit/datasets.py`, `runner.py`, `validator.py`, `cli.py`)
- Added `src/qalprit/datasets.py` for CSV parsing/export, `${ENV_VAR}` cell resolution, secret-column masking (keeping raw secrets out of `.md` files and UI payloads), and variable extraction from steps.
- Updated `TestSpec` (`params`), `StepContext` (`dataset_vars`), `validate_project` (`--data` CSV and `datasets/` variable support), `TestRunner` (session-reusing batch dataset execution + `batch_id`/`dataset_name`), `generate_batch_report()`, `/api/datasets*` endpoints, and CLI `qalprit validate --data` / `qalprit run --data --dataset`.

### Tests
- Added `tests/test_datasets_and_redesign.py` covering CSV parsing/masking, validator dynamic variables, `/api/datasets*` endpoints, sequential batch dataset execution with secret redaction, CLI `--data`, and inspector `xml_snippet` generation.

---

## [v0.1.29] — 2026-11-16

### Backend & Live Mirror Fixes (iOS Scrolling & Bottom Tab Bar Controls)
- **Fix iOS Retina Scaling on Scrollable Screens (`src/qalprit/recorder.py`)**: Never set `already_pixel_space = True` when `self._platform == "ios"` (and require both `x` and `y` dimensions beyond `1.15×` on non-iOS). Previously, any iOS `ScrollView` / catalog screen with off-screen items below `win_h * 1.15` (e.g., `y2 = 1170 > 874 * 1.15` on `iPhone 17 Pro`) falsely skipped `3x` Retina scaling, shrinking all overlays into the top-left `1/3` corner and making bottom controls (`Catalog`, `Cart`, `More`) unclickable.
- **Prioritize Visible On-Screen Elements & Raise Mirror Element Cap (`src/qalprit/recorder.py`)**: Raised `RecorderManager.status()["elements"]` cap from `80` to `250`, sorted visible on-screen elements ahead of scrolled off-screen items, and skipped `visible=False` items in `hit_test_element()`.

### UI Improvements
- **Interactive-First Overlay Z-Order & Offscreen Clamping (`src/qalprit/ui/index.html`)**: Updated `renderRecorderElements()` to clamp partially off-screen boxes cleanly to `[0..100%]`, skip invisible/zero-area/oversized (`>75%`) wrappers, and sort static labels below clickable/editable controls so buttons and bottom tab bars always sit cleanly on top with accurate hover tooltips.

### Tests
- Extended `test_recorder_scales_ios_retina_rects_redacts_and_swipes_in_points` (`tests/test_workbench_and_desktop.py`) with an offscreen scrolled card (`y2 = 1170 > 874 * 1.15`) and bottom `TabBar` `Cart` button to prove `3x` Retina scaling and on-screen priority hold on scrolling iOS pages.

### Root Cause & Lesson Learned
- Checking `max_y2 > win_h * 1.15 or max_x2 > win_w * 1.15` confused scrollable logical-point content extending past the viewport with raw pixel coordinates — passing single-screen pages (e.g., Product Details) while breaking scrollable Catalog/Feed screens. XCUITest XML always reports logical points regardless of scroll depth, and visible viewport controls must never be crowded out by an arbitrary `[:80]` slice before rendering overlays.

---

## [v0.1.28] — 2026-11-15

### Backend & macOS Android Emulator Setup Fix
- **Copy Real `cmdline-tools` Directory in Writable SDK (`src/qalprit/doctor.py`)**: Updated `_link_or_copy_dir()` and `_sync_bundled_sdk_into()` to always copy `cmdline-tools` as a real directory (`copy_real=True`) instead of symlinking it on macOS/Linux, and to automatically heal any pre-existing POSIX symlink left by earlier builds into a real directory while preserving already-downloaded `system-images/` and `emulator/`.

### Tests
- Updated `test_prepare_writable_android_sdk_and_find_sdkmanager` (`tests/test_workbench_and_desktop.py`) to verify `cmdline-tools` is never a symlink and that stale POSIX symlinks at `cmdline-tools` are healed into real directories.

### Root Cause & Lesson Learned
- On macOS `.dmg`, `_sync_bundled_sdk_into()` symlinked `cmdline-tools -> /Applications/QAlprit.app/Contents/Resources/runtime/android-sdk/cmdline-tools`. Although `sdkmanager --sdk_root=...` downloaded `system-images;android-34;google_apis;arm64-v8a` into the writable user SDK, `avdmanager` computes its SDK root via shell `APP_HOME="$(pwd -P)"` and Java `AvdManagerCli` `Path.toRealPath()` (`../../`), resolving symlinks back into the read-only `.app` bundle where zero system images exist (`Error: Package path is not valid. Valid system image paths are: null`). Always materialize `cmdline-tools` as a real directory inside the writable SDK across all OSes.

---

## [v0.1.27] — 2026-11-15

### Backend Fixes
- **Direct Workflow Execution & Dry-Run Mode (`src/qalprit/validator.py`, `src/qalprit/workbench_api.py`)**: Added `flow_name: str | None = None` support to `validate_project()` and `RunJobManager.start()`, allowing any saved workflow under `flows/` to be validated, previewed via Dry Run, and executed directly in Step 4 as a synthetic `FLOW-<SLUG>` test spec even before wrapping it into `specs/*.md`.
- **iOS `.zip` Binary Validation (`src/qalprit/validator.py`)**: Accepted `.zip` alongside `.app` and `.ipa` in `valid_exts["ios"]` so compressed iOS Simulator app bundles no longer trigger misleading extension warnings.
- **Actionable Mismatch Errors (`src/qalprit/workbench_api.py`)**: Replaced the generic `"No matching test journeys found to run."` error with specific diagnostics naming existing journeys on other platforms (e.g., `SMK-001 [android]`), other suites, or available `flows/`.

### UI Improvements (Windows `.exe` & macOS `.dmg` Parity)
- **Step 4 Direct Workflow Selector & Live Match Diagnostics (`src/qalprit/ui/index.html`)**: Added a `Workflow (Optional)` picker (`#runFlowFilter`), live `#runMatchHint` diagnostics explaining platform/suite matches or mismatches before running, and a `Run ->` button on saved workflows in Step 3 (`#flowsTable`), plus Step 2 `btnRecGoRun` support even when saving only a workflow.
- **Step 3 iOS Target Platform Auto-Default (`src/qalprit/ui/index.html`)**: Defaulted `#specPlatIos` to checked on macOS hosts (`Darwin`) and whenever an iOS binary/platform is active (`!STATE.specPlatUserTouched`), matching `resetJourneyBuilderToNew()` so newly authored journeys on Mac aren't accidentally saved as `[android]`-only.
- **Accurate Redaction Label (`src/qalprit/ui/index.html`)**: Updated `syncRunRedactToEnv()` copy from `"off by default for '...'"` to `"enabled/off in '...' config (editable in Step 1C)"` so it reflects the configured `environments.yaml` flag unambiguously.

### Tests
- Added `test_validate_project_accepts_ios_zip_and_single_flow_mode` (`tests/test_validator.py`) and `test_run_job_manager_supports_direct_flow_and_helpful_mismatch_error` (`tests/test_workbench_and_desktop.py`).

### Root Cause & Lesson Learned
- Step 3's `#specPlatIos` HTML checkbox started unchecked on initial load (only `resetJourneyBuilderToNew()` checked `hostOs === "Darwin"`), causing journeys created on Mac to save with `platforms: [android]` while Step 4 defaulted to `ios` (`All Journeys (0)`), and Step 4 lacked any selector to run standalone `flows/*.md` directly. Keep initial HTML/overview defaults aligned with `reset*()` helpers, validate `.zip` as a first-class iOS simulator binary extension, and surface platform/suite mismatches immediately in both the UI hint and backend error messages.

---

## [v0.1.26] — 2026-11-15

### Backend & Live Recorder Fixes
- **iOS Retina `2x`/`3x` Live Mirror Coordinate Scaling (`src/qalprit/recorder.py`)**: `RecorderManager._capture_snapshot_locked()` now scales XML element `rect` bounds from logical iOS points (`driver.get_window_size()`, e.g., `402×874`) to PNG screenshot pixels (e.g., `1206×2622` on 3x Retina), fixing overlay alignment, direct image click hit-testing, and Pillow black-marker redaction on the Live Mirror while keeping `driver.swipe()` in logical window coordinates.
- **iOS Tappable Element & Shell Filtering (`src/qalprit/inspector.py`)**: Detects native XCUITest interactive tags (`XCUIElementTypeButton`, `Link`, `Cell`, `Switch`, `Tab`, `SegmentedControl`, `Stepper`, `MenuItem`) as `clickable=True` (since iOS XML omits `clickable="true"`) and filters out full-screen container shells (`XCUIElementTypeApplication`, `XCUIElementTypeWindow`, `hierarchy`, `AppiumAUT`).

### UI & Web Showcase
- **Mirror Overlay Stacking (`src/qalprit/ui/index.html`)**: Filters hidden/oversized (>85% viewport) overlays and sorts bounding boxes largest-to-smallest with ascending `z-index` (`.rec-box:hover` at `z-index: 999`) so nested buttons sit above parent wrappers.
- **Website Update (`web/index.html`)**: Updated hero copy, execution strip, Step 01, Workbench showcase, and architecture summary to highlight live emulator screen capture for building workflows and smoke tests with minimal AI / LLM usage, and synced release badge to `v0.1.26`.

### Tests
- Added `test_inspector_detects_ios_clickable_buttons_and_skips_app_shell` (`tests/test_inspector_and_cli.py`), `test_recorder_scales_ios_retina_rects_redacts_and_swipes_in_points` (`tests/test_workbench_and_desktop.py`), and included `tests/test_appium_manager.py` macOS `/usr/bin/java` `JAVA_HOME` guard regression tests.

### Root Cause & Lesson Learned
- iOS Appium `page_source` and `get_window_size()` report **logical points**, whereas `get_screenshot_as_png()` returns **2x/3x Retina pixels**, and XCUITest uses tag names instead of `clickable="true"`. Always normalize element rectangles against `px / win` before rendering overlays, hit-testing taps, or drawing live mirror masks, and treat iOS button/cell/tab tags as `clickable=True`.

---

## Engineering Lessons Learned (Read Before Modifying Installers, CI, or Runtimes)

1. **Never mutate files inside macOS `*.app/Contents/` at build or runtime (`v0.1.23`)**: Rewriting `extensions.yaml` inside an already-signed `.app` invalidates `CodeResources` and causes macOS Gatekeeper to report `"QAlprit" is damaged and can't be opened`. Keep `.app/Contents/` read-only/immutable (`get_qalprit_data_dir() / "appium_home"` symlinks), run `codesign --verify --deep --strict` after smoke tests and `ditto`, and keep bundled runtime files `u+w` so `xattr -dr` works cleanly (`v0.1.24`, `v0.1.25`).
2. **Gate frozen GUI/WebView imports in CI installer scripts (`v0.1.24`, `v0.1.25`)**: Always install `.[desktop]` and collect platform WebView/PyObjC frameworks in both `qalprit.spec` files, and execute `--check-desktop` inside the compiled binary during `build_mac.sh` and `build_win.ps1` so packaging never silently falls back to opening a web browser.
3. **Handle Apple Xcode layout changes & `CommandLineTools` (`v0.1.24`, `v0.1.25`)**: Xcode 27 removed `Developer/Applications/Simulator.app` in favor of `Contents/Applications/DeviceHub.app`. Auto-detect via shared `find_xcode_developer_dir()` across `doctor`, `devices`, `appium_manager`, `emulator_manager`, and `driver_factory`, pass `appium:isHeadless = true` when legacy `Simulator.app` is absent, and validate simulator binaries (`iphoneos` `.ipa` vs `iphonesimulator` `.app.zip`) before calling Appium.
4. **Inno Setup `{app}` & 6.6+ Pascal Script rules (`v0.1.18`, `v0.1.19`)**: Never reference `{app}` inside `Check:` functions or `InitializeSetup` before directory selection (`An attempt was made to expand the "app" constant before it was initialized`). Match Inno Setup 6.6+ `CreateCustomForm` signatures.
5. **Android `sdkmanager` & AVD/DLL consistency (`v0.1.20`, `v0.1.21`)**: Always prefer `cmdline-tools/latest/bin/sdkmanager` over legacy `tools/bin` (which crashes under Java 17 with `NoClassDefFoundError: javax/xml/bind/annotation/XmlSchema`), resolve AVD SDK root from `image.sysdir.1` to prevent `PANIC: Broken AVD system path`, and verify Windows `AdbWinApi.dll` + `AdbWinUsbApi.dll` alongside `adb.exe`.
6. **Follow OS storage conventions & selective cleanup (`v0.1.17`, `v0.1.18`)**: Never silently create multi-GB SDKs in unapproved home folders; discover existing runtimes first, use AppData / Application Support + `Documents/QAlpritWorkspace`, and let users selectively retain or remove tools, caches, and workspaces on uninstall.

---

## [v0.1.25] — 2026-11-15 (`cf0e8a6`)

### Backend & Core
- Unified macOS Xcode discovery via `find_xcode_developer_dir()` and `_macos_xcode_env()` in `src/qalprit/doctor.py`, reused by `devices.py`, `appium_manager.py`, `emulator_manager.py`, and `driver_factory.py` (ensuring `DEVELOPER_DIR` resolves even when `xcode-select` points to `CommandLineTools` or non-default `Xcode*.app` installs).
- Added early iOS Simulator binary guard `_validate_ios_simulator_app_binary()` in `driver_factory.py` to reject physical-device `iphoneos` `.ipa` or Android `.apk` files with an actionable error before Appium startup.
- Updated `qalprit doctor` to warn when Xcode has `0 available simulator(s)` with instructions to install a Simulator runtime.

### UI, Packaging & CI
- Added `--check-desktop` self-check to `src/qalprit/desktop.py` (`check_desktop_webview_runtime`) and invoked it inside both `packaging/macos/build_mac.sh` and `packaging/windows/build_win.ps1`.
- Changed `chmod -R u+w "${RUNTIME_TARGET}"` across all bundled macOS runtime files prior to `codesign`.
- Synced visible `v0.1.25` badge in `web/index.html` and added `tests/test_driver_factory.py` + `tests/test_workbench_and_desktop.py` coverage.

---

## [v0.1.24] — 2026-11-15 (`0fd2bfb`)

### Backend & Core
- Added Xcode 27+ support in `driver_factory.py` (`_xcode_has_legacy_simulator_app()`): automatically sets `appium:isHeadless = True` on iOS Simulator sessions when `Developer/Applications/Simulator.app` does not exist.
- Updated `emulator_manager.py` `boot_ios_simulator()` to fall back to Apple's `DeviceHub.app` when `open -a Simulator` fails.
- Added default `ios_simulator` and `ios_device` profiles in `src/qalprit/desktop.py` (`_DEFAULT_DEVICES_YAML`).

### Packaging & Root Cause Fixes
- **Standalone macOS Window Fix**: Installed `.[desktop]` in `packaging/macos/build_mac.sh` and collected `webview`, `objc`, `Cocoa`, `Foundation`, `AppKit`, `WebKit`, `Quartz`, `Security`, and `UniformTypeIdentifiers` + `NSAppTransportSecurity` in `packaging/macos/qalprit.spec`.
- **Clean `xattr` on JRE**: Added `chmod -R u+w "${RUNTIME_TARGET}/jdk"` before `codesign` to prevent `Errno 13: Permission denied` on `0444` Temurin `jdk/legal/*` files during `xattr -dr com.apple.quarantine`.

---

## [v0.1.23] — 2026-11-15 (`46c63d6`)

### Root Cause Fix
- **macOS Gatekeeper `"QAlprit" is damaged and can't be opened`**: `ensure_portable_appium_home()` in `src/qalprit/doctor.py` previously rewrote `extensions.yaml` inside `dist/QAlprit.app` after `codesign` during CI smoke testing (and on writable `/Applications/QAlprit.app` launches), breaking the sealed signature.
- Added `_is_inside_macos_app_bundle()` so `.app/Contents/` is never mutated; `APPIUM_HOME` symlinks and `extensions.yaml` live under `get_qalprit_data_dir() / "appium_home"` with automatic stale-symlink refresh.
- Enforced `codesign --verify --deep --strict` post-smoke and post-`ditto` in `packaging/macos/build_mac.sh` and updated `HOW-TO-OPEN.txt` for macOS Sequoia/Tahoe.

---

## [v0.1.22] — 2026-11-15 (`8d5df07`)

### Backend, UI & Web
- Added environment-aware PII black-marker redaction (`redact_screenshots` in `EnvironmentConfig`: default `False` for `dev`/`development`/`local`, `True` for `qa`/`pre-prod`/`prod`).
- Added live toggle in `RecorderManager` (`POST /api/recorder/redaction`), CLI `--redact` / `--no-redact` in `cli.py`/`runner.py`, and UI switches in Step 1C, Step 2, and Step 4 (`src/qalprit/ui/index.html`).
- Updated `web/index.html` (`https://qalprit.vattitude.ca/`) showcasing interactive recording, redaction toggle, OS-standard storage, and cleanup.

---

## [v0.1.21] — 2026-11-15 (`e1bace6`)

### Backend & Installer Fixes
- Fixed Android Emulator `PANIC: Broken AVD system path` by resolving/matching `image.sysdir.1` in AVD `config.ini` against installed SDK roots (`doctor.py`, `emulator_manager.py`).
- Fixed Windows `adb.exe` `AdbWinApi.dll was not found` error by validating `AdbWinApi.dll` and `AdbWinUsbApi.dll` presence in `NeedsAndroidSdkRuntime()` (`packaging/windows/installer.iss`) and `doctor.py`.

---

## [v0.1.20] — 2026-11-15 (`86c9d6b`)

### Backend & Root Cause Fix
- Fixed `sdkmanager install failed: NoClassDefFoundError: javax/xml/bind/annotation/XmlSchema` under Java 17 by ensuring `find_android_tool("sdkmanager")` (`doctor.py`) prefers modern `cmdline-tools/latest/bin/sdkmanager` over legacy `tools/bin/sdkmanager`.

---

## [v0.1.19] — 2026-11-15 (`d025bc6`)

### Installer Fix
- Updated `packaging/windows/installer.iss` `CreateCustomForm` Pascal Script calls (`CreateCustomForm` parameter arity) for Inno Setup 6.6+ compatibility.

---

## [v0.1.18] — 2026-11-15 (`3b27694`)

### Backend, UI & Installer Fixes
- Fixed Windows installer `Runtime error (at 6:717): An attempt was made to expand the "app" constant before it was initialized` by deferring `{app}` path checks cleanly until after target directory initialization (`packaging/windows/installer.iss`).
- Added selective cleanup across backend (`src/qalprit/cleanup.py`, `qalprit cleanup` CLI, `/api/system/*` endpoints), Desktop UI (`src/qalprit/ui/index.html`), and Windows uninstaller checkboxes so users can selectively keep or remove workspace journeys, Android SDK/AVDs, and settings/caches.

---

## [v0.1.17] — 2026-11-15 (`919a730`)

### Backend & UI
- Added OS-standard directory resolution (`src/qalprit/paths.py`): `~/Documents/QAlpritWorkspace` and `%LOCALAPPDATA%\QAlprit` / `~/Library/Application Support/QAlprit`, avoiding root home folder clutter (`NTUSER.DAT`, oversized unprompted `~/.qalprit`).
- Added pre-install system software discovery (`discover_system_software`) and user-approved SDK/workspace storage wizard controls in `ui/index.html` and `packaging/windows/installer.iss`.

---

## [v0.1.16] — 2026-11-15 (`c304689`)

### Backend, UI & Installer
- Added smart Windows installer upgrade detection skipping unchanged bundled runtimes (Node, Appium, Java, Android SDK) unless **Force reinstall** is checked.
- Added streamlined 4-step workbench UI with inline workflow/journey CRUD and per-journey run selection.
