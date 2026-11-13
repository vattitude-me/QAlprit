"""Environment file (.env) parser, RFC 6238 TOTP generator, and secret masker."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

_VAR_PATTERN = re.compile(r"\$\{([A-Za-z0-9_:.]+)\}")
_SENSITIVE_KEY_MARKERS = ("PASS", "SECRET", "TOKEN", "PIN", "KEY", "OTP", "TOTP", "AUTH")


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse a .env file using Python stdlib only."""
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if not key:
            continue
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
            quote = val[0]
            val = val[1:-1]
            if quote == '"':
                val = (
                    val.replace("\\n", "\n")
                    .replace("\\r", "\r")
                    .replace("\\t", "\t")
                    .replace('\\"', '"')
                    .replace("\\\\", "\\")
                )
        else:
            # Strip inline comments on unquoted values
            if " #" in val:
                val = val.split(" #", 1)[0].rstrip()
        result[key] = val
    return result


def generate_totp(
    secret: str,
    for_time: int | None = None,
    interval: int = 30,
    digits: int = 6,
) -> str:
    """Generate an RFC 6238 TOTP code using Python stdlib only."""
    cleaned = re.sub(r"[\s-]+", "", secret).upper()
    if not cleaned:
        raise ValueError("TOTP secret is empty")
    padding = (-len(cleaned)) % 8
    cleaned_padded = cleaned + ("=" * padding)
    try:
        key = base64.b32decode(cleaned_padded, casefold=True)
    except Exception as exc:
        raise ValueError(f"Invalid Base32 TOTP secret: {exc}") from exc

    timestamp = int(time.time() if for_time is None else for_time)
    counter = timestamp // interval
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code_int = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    otp = code_int % (10**digits)
    return str(otp).zfill(digits)


class SecretMasker:
    """Tracks secret values and masks them before writing logs, JSON, or reports."""

    REPLACEMENT = "***REDACTED***"

    def __init__(self, initial_secrets: Iterable[str] = ()) -> None:
        self._secrets: set[str] = set()
        for item in initial_secrets:
            self.add(item)

    def add(self, value: str | None) -> None:
        if value and len(value) >= 2:
            self._secrets.add(value)

    def mask(self, text: str) -> str:
        if not text or not self._secrets:
            return text
        masked = text
        # Replace longest secrets first to avoid partial substring leakage
        for secret in sorted(self._secrets, key=len, reverse=True):
            if secret in masked:
                masked = masked.replace(secret, self.REPLACEMENT)
        return masked

    def mask_obj(self, data: Any) -> Any:
        if isinstance(data, str):
            return self.mask(data)
        if isinstance(data, list):
            return [self.mask_obj(v) for v in data]
        if isinstance(data, dict):
            return {k: self.mask_obj(v) for k, v in data.items()}
        return data


@dataclass
class EnvContext:
    """Resolved environment variables and secret tracker."""

    variables: dict[str, str] = field(default_factory=dict)
    masker: SecretMasker = field(default_factory=SecretMasker)

    def register_sensitive_keys(self) -> None:
        for key, val in self.variables.items():
            upper = key.upper()
            if any(marker in upper for marker in _SENSITIVE_KEY_MARKERS):
                self.masker.add(val)

    def get(self, key: str) -> str | None:
        return self.variables.get(key)

    def find_missing(self, text: str, extra_vars: dict[str, str] | None = None) -> list[str]:
        missing: list[str] = []
        merged = {**self.variables, **(extra_vars or {})}
        for match in _VAR_PATTERN.finditer(text):
            token = match.group(1)
            if token.startswith("TOTP:"):
                var_name = token[len("TOTP:") :]
                if not merged.get(var_name):
                    missing.append(token)
            else:
                if token not in merged:
                    missing.append(token)
        return missing

    def substitute(
        self,
        text: str,
        extra_vars: dict[str, str] | None = None,
        record_secret: bool = False,
        for_time: int | None = None,
    ) -> str:
        merged = {**self.variables, **(extra_vars or {})}

        def _replace(match: re.Match[str]) -> str:
            token = match.group(1)
            if token.startswith("TOTP:"):
                var_name = token[len("TOTP:") :]
                secret_val = merged.get(var_name)
                if not secret_val:
                    raise KeyError(f"Missing TOTP environment variable: {var_name}")
                self.masker.add(secret_val)
                code = generate_totp(secret_val, for_time=for_time)
                self.masker.add(code)
                return code
            if token not in merged:
                raise KeyError(f"Missing environment variable: {token}")
            val = merged[token]
            if record_secret or any(m in token.upper() for m in _SENSITIVE_KEY_MARKERS):
                self.masker.add(val)
            return val

        return _VAR_PATTERN.sub(_replace, text)


def load_env(
    project_root: Path,
    env_name: str | None = None,
    include_os_env: bool = True,
    extra_vars: dict[str, str] | None = None,
) -> EnvContext:
    """Load .env, .env.<env_name>, and optional OS environment variables."""
    variables: dict[str, str] = {}
    candidates = [
        project_root / ".env",
        project_root / "config" / ".env",
    ]
    if env_name:
        candidates.extend(
            [
                project_root / f".env.{env_name}",
                project_root / "config" / f".env.{env_name}",
            ]
        )
    for candidate in candidates:
        variables.update(parse_env_file(candidate))

    if include_os_env:
        for k, v in os.environ.items():
            variables.setdefault(k, v)
        # Explicit overrides from .env.<env> already loaded; let OS env override if explicitly set?
        # Keep OS env taking precedence over .env files for CI overrides.
        variables.update(os.environ)

    if extra_vars:
        variables.update(extra_vars)

    ctx = EnvContext(variables=variables)
    ctx.register_sensitive_keys()
    return ctx
