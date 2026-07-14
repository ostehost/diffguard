"""DiffGuard output schema — Pydantic v2 models."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, model_validator


class DiffStats(BaseModel):
    """Diff statistics."""

    files: int
    additions: int
    deletions: int


class Meta(BaseModel):
    """Run metadata."""

    ref_range: str
    stats: DiffStats
    warnings: list[str] = []
    timing_ms: float | None = None


SymbolKind = Literal[
    "function_added",
    "function_removed",
    "function_modified",
    "class_added",
    "class_removed",
    "class_modified",
    "signature_changed",
    "moved",
]
"""The set of values ``SymbolChange.kind`` may take."""


class SymbolChange(BaseModel):
    """A single symbol-level change."""

    kind: SymbolKind
    name: str
    signature: str | None = None
    before_signature: str | None = None
    after_signature: str | None = None
    file_from: str | None = None
    line: int | None = None
    breaking: bool | None = None
    rule_id: str | None = None
    category_id: str | None = None
    category: str | None = None
    confidence: Literal["high", "medium", "low"] | None = None
    evidence: list[str] = []
    analysis_gaps: list[str] = []
    detail: dict[str, Any] | None = None


class FileChange(BaseModel):
    """A changed file with its symbol-level changes."""

    path: str
    language: str | None = None
    change_type: Literal["added", "removed", "modified", "renamed"]
    generated: bool = False
    binary: bool = False
    parse_error: bool = False
    unsupported_language: bool = False
    changes: list[SymbolChange] = []


class Summary(BaseModel):
    """Aggregate summary of changes.

    Migration note (v1.0 → v1.1): Added ``focus`` field — a short list of
    the most important items for reviewer agents.  Existing consumers that
    ignore unknown fields are unaffected.
    """

    change_types: dict[str, int] = {}
    breaking_changes: list[SymbolChange] = []
    focus: list[str] = []


class TieredSummary(BaseModel):
    """Multi-tier human-readable summary."""

    oneliner: str = ""
    short: str = ""
    detailed: str = ""


class DiffGuardOutput(BaseModel):
    """Top-level DiffGuard output.

    Migration note (v1.1 → v2.0): ``SymbolChange.breaking`` is now tri-state.
    Consumers must accept ``null`` when compatibility cannot be proven.
    """

    schema_version: Literal["2.0"] = "2.0"
    meta: Meta
    files: list[FileChange] = []
    summary: Summary = Summary()
    tiered: TieredSummary = TieredSummary()


# Review JSON migration note: published 0.1.0 and the unreleased 0.2.0
# intermediate both migrated to 1.0.0. The review command is now validated by
# these models. Misleading caller/impact fields were replaced by syntactic
# references with context, confidence, evidence, and explicit unresolved
# ownership gaps. Structured warnings and tool errors are new. Version 1.1.0
# additively exposes the optional move source path on ReviewFinding.
ReviewMode = Literal["committed", "staged", "worktree"]
ReviewStatus = Literal["ok", "error"]
ReferenceKind = Literal["import", "call", "reference"]
Confidence = Literal["high", "medium", "low"]


class ReviewEvidence(BaseModel):
    """A factual observation supporting a review finding."""

    kind: Literal["syntax", "reference", "analysis_gap"]
    message: str


class ReviewReference(BaseModel):
    """A syntactic name reference without compiler-grade ownership resolution."""

    file: str
    line: int
    symbol: str
    kind: ReferenceKind
    source: str
    confidence: Confidence = "low"
    resolution: Literal["unresolved"] = "unresolved"
    evidence: str = "AST name match; symbol ownership unresolved"


class ReviewFinding(BaseModel):
    """One stable, agent-readable contract-change finding."""

    rule_id: str
    category_id: str
    category: str
    symbol: str
    file: str
    source_file: str | None = None
    line: int | None = None
    language: str | None = None
    before_signature: str | None = None
    after_signature: str | None = None
    breaking: bool | None = None
    confidence: Confidence
    evidence: list[ReviewEvidence]
    references: list[ReviewReference] = []
    analysis_gaps: list[str] = []
    review_hint: str


class ReviewWarning(BaseModel):
    """A non-fatal analysis limitation."""

    code: str
    message: str
    file: str | None = None


class ReviewStats(BaseModel):
    """Review analysis statistics."""

    files_analyzed: int
    symbols_changed: int
    parse_errors: int
    reference_count: int
    silence_reason: str | None = None


class ReviewError(BaseModel):
    """A tool failure returned when JSON was requested."""

    code: str
    message: str


class ReviewEnvelope(BaseModel):
    """Stable JSON contract emitted by ``diffguard review``."""

    version: Literal["1.1.0"] = "1.1.0"
    status: ReviewStatus = "ok"
    mode: ReviewMode
    ref_range: str
    findings: list[ReviewFinding] = []
    warnings: list[ReviewWarning] = []
    stats: ReviewStats
    error: ReviewError | None = None

    @model_validator(mode="after")
    def validate_status_error_consistency(self) -> Self:
        """Require tool errors to use the error status, and vice versa."""
        if (self.status == "error") != (self.error is not None):
            raise ValueError("status must be 'error' if and only if error is present")
        return self
