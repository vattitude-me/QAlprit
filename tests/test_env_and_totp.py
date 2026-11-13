"""Tests for stdlib .env parsing, RFC 6238 TOTP generation, and SecretMasker."""

from __future__ import annotations

import base64
from pathlib import Path

from qalprit.env_loader import SecretMasker, generate_totp, load_env, parse_env_file


def test_parse_env_file_handles_quotes_exports_and_comments(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        """
# comment line
QA_USER=alice@example.com # inline comment
export QA_PASS="s3cr3t!#pass"
SINGLE='literal value'
EMPTY=
""",
        encoding="utf-8",
    )
    parsed = parse_env_file(env_file)
    assert parsed == {
        "QA_USER": "alice@example.com",
        "QA_PASS": "s3cr3t!#pass",
        "SINGLE": "literal value",
        "EMPTY": "",
    }


def test_rfc6238_totp_known_vector() -> None:
    # RFC 4226 / 6238 SHA-1 test seed "12345678901234567890" at t=59 -> 287082 (6 digits)
    secret_b32 = base64.b32encode(b"12345678901234567890").decode("ascii")
    assert generate_totp(secret_b32, for_time=59) == "287082"
    # Base32 with spaces/lowercase should also normalize cleanly
    spaced = " ".join([secret_b32[i : i + 4].lower() for i in range(0, len(secret_b32), 4)])
    assert generate_totp(spaced, for_time=59) == "287082"


def test_load_env_substitution_and_secret_masking(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("QA_USER=base_user\nQA_PASS=SuperSecret123\n", encoding="utf-8")
    (tmp_path / ".env.qa").write_text("QA_USER=qa_user\nQA_TOTP_SECRET=JBSWY3DPEHPK3PXP\n", encoding="utf-8")

    ctx = load_env(tmp_path, env_name="qa", include_os_env=False)
    assert ctx.substitute("${QA_USER}") == "qa_user"
    resolved_pass = ctx.substitute("${QA_PASS}", record_secret=True)
    assert resolved_pass == "SuperSecret123"
    code = ctx.substitute("${TOTP:QA_TOTP_SECRET}", for_time=59)
    assert len(code) == 6 and code.isdigit()

    raw_log = f"user=qa_user pass={resolved_pass} otp={code}"
    masked = ctx.masker.mask(raw_log)
    assert "SuperSecret123" not in masked
    assert code not in masked
    assert "qa_user" in masked

    masker = SecretMasker(["abc", "abcdef"])
    assert masker.mask("val=abcdef") == "val=***REDACTED***"
