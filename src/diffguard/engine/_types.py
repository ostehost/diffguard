"""Shared types for the DiffGuard engine."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Literal

ParsedSymbolKind = Literal["function", "class", "method"]
"""The kinds of symbol the language extractors emit."""

RefContext = Literal["import", "call", "reference"]
"""The syntactic AST context in which a name appears."""

Confidence = Literal["high", "medium", "low"]
"""Confidence in an analysis statement, never a probability."""


@dataclass(frozen=True)
class Symbol:
    """A parsed symbol from source code."""

    name: str
    kind: ParsedSymbolKind
    signature: str  # full signature text
    start_line: int
    end_line: int
    body_hash: str  # hash of the body text for change detection
    parent: str | None = None  # parent class name for methods


@dataclass(frozen=True)
class ParseResult:
    """Result of parsing a source file."""

    symbols: list[Symbol] = field(default_factory=list)
    parse_error: bool = False


@dataclass(frozen=True)
class MatchedSymbol:
    """A matched pair of old/new symbols, or an unmatched symbol."""

    old: Symbol | None  # None = added
    new: Symbol | None  # None = removed
    file_from: str | None = None  # for cross-file moves
    file_to: str | None = None  # destination file for cross-file moves


@dataclass(frozen=True)
class SignatureAssessment:
    """Syntactic signature comparison with an explicit compatibility limit."""

    rule_id: str
    category_id: str
    category: str
    breaking: bool | None
    confidence: Confidence
    evidence: tuple[str, ...]
    analysis_gaps: tuple[str, ...] = ()


@dataclass(frozen=True)
class SignatureComparison:
    """Result of comparing two signatures.

    ``assessment`` is absent only when the declarations are structurally
    equivalent.  Keeping equivalence separate from a change assessment lets
    the classifier ignore formatting-only edits without manufacturing a
    placeholder rule.
    """

    assessment: SignatureAssessment | None = None

    @property
    def equivalent(self) -> bool:
        """Return whether the two signatures represent the same structure."""
        return self.assessment is None


@dataclass(frozen=True)
class Reference:
    """A syntactic name reference; symbol ownership is not resolved."""

    file_path: str
    line: int
    symbol_name: str
    context: RefContext
    source_line: str = ""  # the actual source line (stripped)
    confidence: Confidence = "low"
    evidence: str = "AST name match; symbol ownership unresolved"


@dataclass(frozen=True)
class ReferenceScan:
    """References plus non-fatal gaps encountered while scanning."""

    references: list[Reference] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def compute_body_hash(body: str) -> str:
    """Compute hash of body text, normalized to ignore whitespace differences."""
    normalized = re.sub(r"\s+", " ", body.strip())
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()  # noqa: S324
