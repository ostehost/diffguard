"""Change classification — takes matched symbols, produces SymbolChange objects."""

from __future__ import annotations

from collections.abc import Callable

from diffguard.engine._types import MatchedSymbol, SignatureComparison
from diffguard.schema import SymbolChange, SymbolKind

SignatureComparator = Callable[[str, str], SignatureComparison]

# (symbol category, change action) -> schema kind Literal.
_KIND_MAP: dict[tuple[str, str], SymbolKind] = {
    ("function", "added"): "function_added",
    ("function", "removed"): "function_removed",
    ("function", "modified"): "function_modified",
    ("class", "added"): "class_added",
    ("class", "removed"): "class_removed",
    ("class", "modified"): "class_modified",
}


def _change_kind(symbol_kind: str, action: str) -> SymbolKind:
    """Map a parsed symbol kind + change action to the schema kind Literal."""
    category = "class" if symbol_kind == "class" else "function"
    return _KIND_MAP[(category, action)]


def classify_changes(
    matches: list[MatchedSymbol],
    compare_signature: SignatureComparator,
) -> list[SymbolChange]:
    """Classify matched symbols into schema-compatible SymbolChange objects."""
    results: list[SymbolChange] = []

    for m in matches:
        change = _classify_one(m, compare_signature)
        if change is not None:
            results.append(change)

    return results


def _classify_one(
    m: MatchedSymbol,
    compare_signature: SignatureComparator,
) -> SymbolChange | None:
    """Classify a single matched symbol pair."""
    # Moved symbol
    if m.file_from is not None and m.old is not None and m.new is not None:
        return SymbolChange(
            kind="moved",
            name=m.new.name,
            signature=m.new.signature,
            file_from=m.file_from,
            line=m.new.start_line,
            rule_id="DG202",
            category_id="possible_symbol_move",
            category="POSSIBLE SYMBOL MOVE",
            confidence="medium",
            evidence=["Matching symbol name/signature or body exists at a different file path"],
            analysis_gaps=["Move identity and import/export ownership were not resolved"],
        )

    # Added
    if m.old is None and m.new is not None:
        return SymbolChange(
            kind=_change_kind(m.new.kind, "added"),
            name=m.new.name,
            signature=m.new.signature,
            line=m.new.start_line,
            confidence="high",
            evidence=["Symbol declaration exists only in the after snapshot"],
        )

    # Removed
    if m.new is None and m.old is not None:
        return SymbolChange(
            kind=_change_kind(m.old.kind, "removed"),
            name=m.old.name,
            signature=m.old.signature,
            line=m.old.start_line,
            rule_id="DG201",
            category_id="symbol_removed",
            category="SYMBOL REMOVED",
            confidence="high",
            evidence=["Symbol declaration exists only in the before snapshot"],
            analysis_gaps=["Public/export status and dependent ownership were not resolved"],
        )

    # Both exist — check for changes
    assert m.old is not None and m.new is not None

    # Signature comparison is independent of body comparison. A default,
    # annotation, or parameter-only edit commonly leaves the body hash equal.
    if m.old.signature != m.new.signature:
        comparison = compare_signature(m.old.signature, m.new.signature)
        if comparison.assessment is not None:
            assessment = comparison.assessment
            return SymbolChange(
                kind="signature_changed",
                name=m.new.name,
                before_signature=m.old.signature,
                after_signature=m.new.signature,
                line=m.new.start_line,
                breaking=assessment.breaking,
                rule_id=assessment.rule_id,
                category_id=assessment.category_id,
                category=assessment.category,
                confidence=assessment.confidence,
                evidence=list(assessment.evidence),
                analysis_gaps=list(assessment.analysis_gaps),
            )

    # Truly unchanged
    if m.old.body_hash == m.new.body_hash:
        return None

    # Body modified, with an equal or structurally equivalent signature.
    return SymbolChange(
        kind=_change_kind(m.new.kind, "modified"),
        name=m.new.name,
        signature=m.new.signature,
        line=m.new.start_line,
        confidence="high",
        evidence=[
            "Symbol body syntax changed while its signature remained structurally equivalent"
        ],
    )
