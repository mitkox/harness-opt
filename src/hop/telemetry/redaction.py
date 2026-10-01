"""M2 redaction and data classification (AOP-014).

Classifications: public, internal, confidential, secret.
Secrets are never placed in OTel attributes, Prometheus labels, logs, or
trajectory metadata. Raw payloads with secrets live only as
access-controlled artifacts referenced by digest.
"""

from __future__ import annotations

import hashlib
import re
from enum import Enum


class DataClassification(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    SECRET = "secret"


# Conservative patterns for synthetic-secret tests and real hygiene.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("api_key", re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)(['\"]?)([A-Za-z0-9_\-]{8,})\2")),
    ("password", re.compile(r"(?i)(password\s*[:=]\s*)(['\"]?)([^\s'\"]{4,})\2")),
    ("bearer", re.compile(r"(Bearer\s+)([A-Za-z0-9_\-\.~\+/]+=*)")),
    ("authorization", re.compile(r"(?i)(authorization\s*[:=]\s*)(['\"]?)([^\s'\"]{6,})\2")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("gh_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}")),
    ("sk_secret", re.compile(r"sk-[A-Za-z0-9]{16,}")),
    ("aws_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("url_credentials", re.compile(r"(https?://)([^/\s:@]+):([^/\s:@]+)@")),
]

_ENV_SECRET_KEYS = (
    "TOKEN",
    "PASSWORD",
    "API_KEY",
    "SECRET",
    "PRIVATE_KEY",
    "AUTHORIZATION",
    "CREDENTIALS",
)

_SECRET_KEY_MARKERS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "auth",
    "private_key",
    "credential",
    "bearer",
)


def classify_environment(env: dict[str, str]) -> dict[str, str]:
    """Classify env keys; anything secret must be scrubbed before telemetry."""
    out = {}
    for key in env:
        upper = key.upper()
        if any(marker in upper for marker in _ENV_SECRET_KEYS):
            out[key] = DataClassification.SECRET.value
        else:
            out[key] = DataClassification.INTERNAL.value
    return out


def redact_text(text: str) -> tuple[str, list[str]]:
    """Redact secrets in free text. Returns (redacted, redaction_kinds)."""
    kinds: list[str] = []
    redacted = text
    for kind, pattern in _PATTERNS:

        def _sub(match: re.Match, kind: str = kind) -> str:
            groups = match.groups()
            if kind == "private_key":
                return "***REDACTED:private_key***"
            if kind == "url_credentials":
                return f"{groups[0]}***REDACTED***:***REDACTED***@"
            if len(groups) >= 3 and groups[0] and groups[2]:
                return f"{groups[0]}{groups[1]}***REDACTED:{kind}***"
            if len(groups) >= 2 and groups[0]:
                return f"{groups[0]}***REDACTED:{kind}***"
            return f"***REDACTED:{kind}***"

        new_text, count = pattern.subn(_sub, redacted)
        if count:
            kinds.append(kind)
            redacted = new_text
    return redacted, kinds


def sanitize_attributes(attrs: dict, *, max_str: int = 2000) -> dict:
    """Sanitize an attribute dict for trajectory/OTel use.

    - Redacts secret-looking strings.
    - Truncates long strings (large prompts/diffs/tool output must be
      artifacts, never inline metadata).
    - Drops Prometheus-hostile unbounded values (handled by caller label policy).
    """
    clean: dict = {}
    for key, value in attrs.items():
        lowered = str(key).lower()
        if isinstance(value, str) and any(m in lowered for m in _SECRET_KEY_MARKERS):
            # Secret by key name (e.g. {"password": "hunter2"}): the value
            # alone carries no "password:" prefix, so pattern matching on the
            # value would miss it. Redact wholesale, keep a digest for joins.
            clean[key] = "***REDACTED:key:" + lowered + "***"
            clean[key + "_digest"] = digest_of(value)
            continue
        if isinstance(value, str):
            redacted, _ = redact_text(value)
            if len(redacted) > max_str:
                digest = "sha256:" + hashlib.sha256(value.encode()).hexdigest()
                clean[key] = redacted[:max_str] + f"...[truncated sha256:{digest[7:15]}]"
                clean[key + "_artifact_digest"] = digest
                clean[key + "_truncated"] = True
            else:
                clean[key] = redacted
        elif isinstance(value, dict):
            clean[key] = sanitize_attributes(value, max_str=max_str)
        elif isinstance(value, list):
            clean[key] = [
                sanitize_attributes({"v": v}, max_str=max_str)["v"]
                if isinstance(v, (dict, str))
                else v
                for v in value
            ][:50]
        else:
            clean[key] = value
    return clean


def contains_secret(text: str) -> bool:
    _, kinds = redact_text(text)
    return bool(kinds)


def digest_of(*parts: str) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode())
        h.update(b"\0")
    return "sha256:" + h.hexdigest()
