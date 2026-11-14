# Security Policy

QAlprit is designed around **defense-in-depth** so credentials, API keys, TOTP secrets, and personally identifiable information (PII) never leak into test artifacts, logs, or pull requests.

---

## 1. Secret & PII Protection Architecture

### Layer 1: Environment & String Scrubbing (`src/qalprit/env_loader.py`)
- Credentials live strictly in `.env` (gitignored) and are resolved via `${VAR}` / `${TOTP:VAR}` at runtime.
- `SecretMasker` tracks raw secrets as well as URL-encoded and HTML-escaped forms (minimum length 3, ignoring boolean/status literals) and replaces occurrences with `***REDACTED***` across:
  - Step parameters and error messages in `results.json`
  - `report.html`, `summary.md`, and `junit.xml`
  - `appium.log` scrubbed on `AppiumManager.__exit__`

### Layer 2: In-Memory Screenshot & XML Redaction (`src/qalprit/evidence.py`)
- Any target with `sensitive: true` in `screens/screens.yaml` or `secret: true` on a `type` step triggers in-memory bounding-box black-out via Pillow **before** PNG bytes are written to disk.
- Window-to-pixel scaling handles Retina (`@2x`/`@3x`) iOS simulators and high-DPI Android displays accurately.
- Captured `page_source` failure XML dumps redact text and value attributes on matching sensitive elements.

### Layer 3: Fail-Safe Evidence Mode (`secure_evidence: true` default)
- With `secure_evidence: true` enabled in `config/environments.yaml` (default), if a visible `sensitive: true` element has missing/zero-area bounds or redaction raises an error, QAlprit **withholds saving the screenshot** rather than risking an unredacted PNG on disk.
- `redaction_warning` is surfaced in both `results.json` and the `report.html` summary banner so operators notice broken redaction immediately.

---

## 2. Local Workbench (`qalprit ui`) & Report Safety

- `qalprit ui` binds exclusively to `127.0.0.1` by default and logs an explicit stderr warning if bound to a non-loopback host.
- File serving enforces resolved path containment inside `results/` (blocking both `..` traversal and symlink escapes).
- `report/generator.py` uses Jinja2 autoescaping and validates asset paths before embedding or linking evidence images.

---

## 3. Repository Hygiene

Never commit or upload:
- `.env` files, API tokens, or service credentials
- Raw `results/` folders or unredacted screenshots
- Application binaries (`.apk`, `.ipa`, `.app`) in `apps/`

---

## 4. Reporting a Vulnerability

If you discover a redaction bypass, secret leak, path traversal bug, or security issue in QAlprit:

1. **Do not** open a public issue containing exploit payloads, real credentials, or unredacted artifacts.
2. Report privately via [GitHub Security Advisories](https://github.com/vattitude-me/QAlprit/security/advisories/new) or email **vatsakrish@gmail.com** with reproduction steps and affected version.

We aim to acknowledge reports promptly and release patched packages cleanly on PyPI.
