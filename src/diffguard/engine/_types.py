"""Shared types for the DiffGuard engine."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Symbol:
    """A parsed symbol from source code."""

    name: str
    kind: str  # "function", "class", "method"
    signature: str  # full signature text
    start_line: int
    end_line: int
    body_hash: str  # hash of the body text for change detection
    parent: str | None = None  # parent class name for methods


@dataclass(frozen=True)
class ParseResult:
    """Result of parsing a source file."""

    symbols: list[Symbol] = field(default_factory=list)
    language: str = ""
    parse_error: bool = False
    error_message: str | None = None


def compute_body_hash(body: str) -> str:
    """Compute hash of body text, normalized to ignore whitespace differences."""
    normalized = re.sub(r"\s+", " ", body.strip())
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()  # noqa: S324
