"""Versioned domain contracts for the HOP platform (M0/M1).

Every record carries ``schema_version`` so stored artifacts can be migrated
explicitly. Validation happens at admission (CLI), worker admission (runner),
and artifact import (artifact store).
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from hop import SCHEMA_VERSION


class AopBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=SCHEMA_VERSION, frozen=True)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str, limit_bytes: int | None = None) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        remaining = limit_bytes
        while True:
            chunk = fh.read(65536 if remaining is None else min(65536, remaining))
            if not chunk:
                break
            h.update(chunk)
            if remaining is not None:
                remaining -= len(chunk)
                if remaining <= 0:
                    break
    return h.hexdigest()
