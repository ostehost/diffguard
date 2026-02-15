"""DiffGuard output schema — Pydantic v2 models."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel


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


class SymbolChange(BaseModel):
    """A single symbol-level change."""

    kind: Literal[
        "function_added",
        "function_removed",
        "function_modified",
        "class_added",
        "class_removed",
        "class_modified",
        "signature_changed",
        "moved",
    ]
    name: str
    signature: str | None = None
    before_signature: str | None = None
    after_signature: str | None = None
    file_from: str | None = None
    line: int | None = None
    breaking: bool = False
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
    """Top-level DiffGuard output."""

    schema_version: str = "1.1"
    meta: Meta
    files: list[FileChange] = []
    summary: Summary = Summary()
    tiered: TieredSummary = TieredSummary()


def export_json_schema() -> str:
    """Export the JSON schema as a string."""
    return json.dumps(DiffGuardOutput.model_json_schema(), indent=2)
