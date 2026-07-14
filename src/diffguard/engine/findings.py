"""High-signal findings — the domain layer between analysis and presentation.

A *finding* is a change worth surfacing to a reviewer: a signature change, a
breaking change, a removed symbol, or a moved symbol. This module is the single
source of truth for "what counts as high-signal" and for attaching syntactic
reference evidence. Both text and JSON reporters consume :class:`Finding`
objects so the trigger logic lives in exactly one place.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from diffguard.engine._paths import is_test_file
from diffguard.engine._types import Reference
from diffguard.schema import DiffGuardOutput, FileChange, SymbolChange


def is_high_signal(sc: SymbolChange) -> bool:
    """Return True if a symbol change is worth surfacing to a reviewer.

    High-signal changes are the ones that can affect callers: signature
    changes, breaking changes, removed symbols, and moved symbols. Body-only
    changes (same signature, different implementation) are *not* high-signal.
    """
    return bool(
        (sc.before_signature and sc.after_signature)
        or sc.breaking
        or sc.kind.endswith("_removed")
        or sc.kind == "moved"
    )


def categorize_change(sc: SymbolChange) -> str:
    """Return a category label for a high-signal change."""
    if sc.kind.endswith("_removed"):
        return "SYMBOL REMOVED"
    if sc.kind == "moved":
        return sc.category or "POSSIBLE SYMBOL MOVE"
    if sc.category:
        return sc.category
    return "CHANGED"


@dataclass(frozen=True)
class Finding:
    """A high-signal change plus unresolved syntactic reference evidence.

    References are name matches in explicit AST contexts. They are split by
    whether the containing file looks like a test, but they do not prove symbol
    ownership.
    """

    file: FileChange
    change: SymbolChange
    category: str
    prod_references: list[Reference] = field(default_factory=list)
    test_references: list[Reference] = field(default_factory=list)

    @property
    def path(self) -> str:
        """Path of the file containing the change."""
        return self.file.path


def changed_symbol_names(output: DiffGuardOutput) -> list[str]:
    """Collect changed symbol names for the orchestration layer."""
    return [sc.name for fc in output.files for sc in fc.changes]


def has_high_signal(output: DiffGuardOutput) -> bool:
    """Return True if the output contains any high-signal change.

    Dependency references never trigger output on their own — a body-only
    change with many callers is still silence — so this looks only at the
    symbol changes themselves.
    """
    return any(is_high_signal(sc) for fc in output.files for sc in fc.changes)


def extract_findings(
    output: DiffGuardOutput,
    dep_refs: list[Reference] | None = None,
) -> list[Finding]:
    """Extract all high-signal findings from a pipeline result.

    Each finding is annotated with syntactic production and test references.
    """
    refs_by_symbol: dict[str, list[Reference]] = {}
    for ref in dep_refs or []:
        refs_by_symbol.setdefault(ref.symbol_name, []).append(ref)

    findings: list[Finding] = []
    for fc in output.files:
        for sc in fc.changes:
            if not is_high_signal(sc):
                continue
            references = refs_by_symbol.get(sc.name, [])
            findings.append(
                Finding(
                    file=fc,
                    change=sc,
                    category=categorize_change(sc),
                    prod_references=[ref for ref in references if not is_test_file(ref.file_path)],
                    test_references=[ref for ref in references if is_test_file(ref.file_path)],
                )
            )
    return findings
