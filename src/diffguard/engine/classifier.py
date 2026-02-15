"""Change classification — takes matched symbols, produces SymbolChange objects."""

from __future__ import annotations

from diffguard.engine.matcher import MatchedSymbol
from diffguard.engine.signatures import is_breaking_change
from diffguard.schema import SymbolChange


def _kind_prefix(kind: str) -> str:
    """Map symbol kind to schema prefix ('function' or 'class')."""
    if kind == "class":
        return "class"
    return "function"


def classify_changes(matches: list[MatchedSymbol]) -> list[SymbolChange]:
    """Classify matched symbols into schema-compatible SymbolChange objects."""
    results: list[SymbolChange] = []

    for m in matches:
        change = _classify_one(m)
        if change is not None:
            results.append(change)

    return results


def _classify_one(m: MatchedSymbol) -> SymbolChange | None:
    """Classify a single matched symbol pair."""
    # Moved symbol
    if m.file_from is not None and m.old is not None and m.new is not None:
        return SymbolChange(
            kind="moved",
            name=m.new.name,
            signature=m.new.signature,
            file_from=m.file_from,
            line=m.new.start_line,
        )

    # Added
    if m.old is None and m.new is not None:
        prefix = _kind_prefix(m.new.kind)
        return SymbolChange(
            kind=f"{prefix}_added",  # type: ignore[arg-type]
            name=m.new.name,
            signature=m.new.signature,
            line=m.new.start_line,
        )

    # Removed
    if m.new is None and m.old is not None:
        prefix = _kind_prefix(m.old.kind)
        return SymbolChange(
            kind=f"{prefix}_removed",  # type: ignore[arg-type]
            name=m.old.name,
            signature=m.old.signature,
            line=m.old.start_line,
        )

    # Both exist — check for changes
    assert m.old is not None and m.new is not None

    # Unchanged
    if m.old.body_hash == m.new.body_hash:
        return None

    # Signature changed
    if m.old.signature != m.new.signature:
        breaking = is_breaking_change(m.old.signature, m.new.signature)
        return SymbolChange(
            kind="signature_changed",
            name=m.new.name,
            before_signature=m.old.signature,
            after_signature=m.new.signature,
            line=m.new.start_line,
            breaking=breaking,
        )

    # Body modified, same signature
    prefix = _kind_prefix(m.new.kind)
    return SymbolChange(
        kind=f"{prefix}_modified",  # type: ignore[arg-type]
        name=m.new.name,
        signature=m.new.signature,
        line=m.new.start_line,
    )
