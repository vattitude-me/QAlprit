# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in **QAlprit** (for example, secret leakage in logs/JSON/reports, bypasses of in-memory Pillow redaction, or path traversal in `qalprit ui`), please **do not** open a public GitHub issue.

Instead, report it privately via GitHub Security Advisories on [`vattitude-me/QAlprit`](https://github.com/vattitude-me/QAlprit/security/advisories) or email `vatsakrish@gmail.com` with:
- A description of the vulnerability and impact
- Steps or a minimal test case to reproduce
- Any suggested remediation

We will acknowledge reports within 48 hours and coordinate a fix before public disclosure.

## Supported Versions

| Version | Supported |
|---|---|
| `0.1.x` | Yes |

## Security Design Notes

- `.env` and `.env.*` files are git-ignored; only `config/env.example` should be committed.
- Always mark password, PIN, OTP, card, and PII locators with `sensitive: true` in `screens/screens.yaml` and `secret: true` on `type` steps.
- Use `capture: off` for full-screen sensitive views where bounding-box redaction is insufficient.
