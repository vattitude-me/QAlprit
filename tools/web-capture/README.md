# QAlprit Web Capture

A small Python script that signs in to a web portal, clicks through a fixed list of steps, and saves a screenshot at each point you mark. The steps repeat once per row of a CSV file, so you can search for a different customer, account or order on every pass and capture the screens for each one.

It is separate from the QAlprit mobile engine and is not part of the PyPI package.

## Install

```bash
cd tools/web-capture
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
```

## Set up

```bash
cp samples/.env.example .env            # portal address and your SSO email
cp samples/capture.yaml capture.yaml    # the steps
cp samples/data.csv data.csv            # one row per pass
```

## Samples

The `samples/` folder is shared with the team and ships with each release as `qalprit-web-capture-<version>.zip`.

| File | What it shows |
| --- | --- |
| `samples/capture.yaml` | A full config: SSO sign-in, a search driven by the CSV, an extracted value, masking, and a step that only runs when a tab shows. |
| `samples/data.csv` | The CSV that feeds it, one customer per row. |
| `samples/.env.example` | The two values the tool reads from `.env`. |
| `samples/sample-run/` | The output of running that config against a test portal. Open `index.html` to see the report, screenshots and extracted values. |

Your own copies sit next to `web_capture.py`. `capture.yaml`, `data.csv` and `.env` there are gitignored, as are `auth/` (the saved session) and `captures/` (your runs), so real portal details and screens stay on your machine.

## Run

```bash
.venv/bin/python web_capture.py --check          # check the config and every CSV row, no browser
.venv/bin/python web_capture.py                  # run every row
.venv/bin/python web_capture.py --rows 1-3,7     # only these rows
.venv/bin/python web_capture.py --fresh-login    # throw away the saved session and sign in again
.venv/bin/python web_capture.py --login manual   # skip SSO and sign in by hand
.venv/bin/python web_capture.py --headless       # hide the browser
```

Each run writes to `captures/<run-id>/`:

| File | What it holds |
| --- | --- |
| `<row label>/01_<name>.png` | Screenshots in step order. A failed row ends with `NN_FAIL.png`. |
| `index.html` | Every row's screenshots and extracted values on one page. |
| `extracted.csv` | One line per row with the values from `extract` steps. |
| `manifest.json` | Row status, errors, screenshot files, page URLs and titles. |
| `capture.log` | What each step did. Secret values show as `***`. |

The exit code is 0 when every row passed, 1 when a row failed, and 2 when the config or login is wrong.

## How sign-in works

Sign-in happens once. The cookies are saved to `auth/session.json` and reused until `session.max_age_hours` passes or the portal stops accepting them. Then the tool signs in again by itself.

With `login.mode: auto` (the default):

1. The tool opens `login.url` in a visible browser.
2. It types `SSO_EMAIL` from `.env` into any email field it finds and presses Enter. It does this on each page of the sign-in chain, so a portal that hands over to Microsoft, Okta or Google works without extra steps.
3. If your device already has an SSO session, the portal opens and the run starts.
4. If the identity provider asks for a password or MFA, or nothing happens within `sso_timeout`, the tool stops trying. The browser stays open and the terminal asks you to sign in. The run starts as soon as the portal shows its signed-in page.

The other modes:

| Mode | What it does |
| --- | --- |
| `sso` | Tries SSO only and fails if it cannot finish. Use it for unattended runs. |
| `manual` | Opens the login page and waits for you. |
| `none` | The portal needs no login. |

`login.logged_in_when` tells the tool that sign-in has finished. Give it a `selector` for something only the signed-in portal shows (a main menu, your avatar), a `url_contains`, or both.

Some SSO setups only work in your real browser, because the device itself proves who you are. Set `channel: msedge` or `channel: chrome` to use the installed Edge or Chrome.

If your portal needs a button click before the email field, set `login.sso_button`. For anything more unusual, write the sign-in as steps under `login.sso_steps`.

The session file holds live cookies. Treat it like a password. Portals that keep their login only in `sessionStorage` cannot be resumed from a saved session, and the tool says so.

## Writing steps

Each step is one action. Values can use `${NAME}`, which reads a CSV column first, then `.env`, then the environment. `${row}` is the row number and `${run_id}` the run's id.

```yaml
steps:
  - goto: /customers
  - fill: {placeholder: Search customers}
    value: "${customer_name}"
  - press: Enter
  - wait_for: {text: "${customer_name}"}
  - click: {role: link, name: "${customer_name}"}
  - extract: status
    selector: ".status-badge"
  - screenshot: customer-detail
    mask: [".ssn"]
```

Quote any value that contains `${...}` inside `{ }`, or YAML will not read it.

### Actions

| Action | Example | Notes |
| --- | --- | --- |
| `goto` | `goto: /orders` | Relative paths join `base_url`. |
| `click` | `click: "#save"` | |
| `fill` | `fill: "#q"` with `value:` | Replaces the field's text. |
| `type` | `type: "#q"` with `value:` | Types key by key, for fields that react to each key. `delay:` in ms. |
| `press` | `press: Enter` | Add `selector:` to press inside one field. |
| `select` | `select: "#country"` with `value:` | Matches the option's value or label. |
| `check`, `uncheck` | `check: "#agree"` | |
| `hover` | `hover: {text: Reports}` | Opens hover menus. |
| `scroll` | `scroll: bottom` | `top`, `bottom` or an element. |
| `wait_for` | `wait_for: ".results"` | `state:` visible (default), hidden, attached or detached. |
| `wait_for_url` | `wait_for_url: /orders/` | Waits until the URL contains the text. |
| `wait` | `wait: 2` | Seconds. Prefer `wait_for`. |
| `screenshot` | `screenshot: order-detail` | Full page by default. `full_page: false`, `selector:` for one element, `mask:` to black out elements. |
| `extract` | `extract: total` with `selector:` | Saves the element's text to `extracted.csv`. `attribute:` reads an attribute, `all: true` joins every match. |
| `if_visible` | `if_visible: "#cookie-banner"` with `then:` and `else:` | Runs `then` if the element shows within `timeout` (3 s by default), else `else`. |

### Options on any step

| Option | Meaning |
| --- | --- |
| `optional: true` | A failure is logged and the row carries on. |
| `timeout: 30` | Seconds for this step. The default is the top-level `timeout`. |
| `secret: true` | Shows `***` instead of the value in the log. |
| `settle: false` | After `click` or `press`, do not wait for the page to go quiet. |

### Pointing at elements

A string is any Playwright selector: CSS (`"#save"`, `".row >> nth=2"`), `"text=Save"` or XPath. A mapping finds elements the way a person sees them, which survives page redesigns better:

```yaml
click: {role: button, name: Save}
fill: {label: Email}
fill: {placeholder: Search}
click: {text: View details, exact: true}
click: {test_id: submit}
click: {css: ".result a", nth: 2}     # the third match
```

Without `nth`, the first match is used.

### Secrets and PII

- `.env` keys and CSV columns whose names contain `PASS`, `SECRET`, `TOKEN`, `API_KEY`, `OTP` or `PIN` are masked as `***` in the log, errors and extracted values. List other columns under `data.secret_columns`.
- Use `mask:` on a screenshot to black out account numbers, SINs and similar fields.
- `captures/` holds real portal screens. Keep it out of git and share it with care.

## Tests

`tests/test_web_capture.py` in the repo root covers the config checks and runs a full capture against a fake portal. The browser tests skip when Playwright is not installed.

```bash
.venv/bin/pytest ../../tests/test_web_capture.py
```
