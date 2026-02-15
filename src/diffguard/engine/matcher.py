"""Symbol matching — old↔new symbol pairing for change detection."""

from __future__ import annotations

from dataclasses import dataclass

from diffguard.engine._types import Symbol

SymbolKey = tuple[str, str, str | None]


@dataclass(frozen=True)
class MatchedSymbol:
    """A matched pair of old/new symbols, or an unmatched symbol."""

    old: Symbol | None  # None = added
    new: Symbol | None  # None = removed
    file_from: str | None = None  # for cross-file moves
    file_to: str | None = None  # destination file for cross-file moves


def _key(s: Symbol) -> SymbolKey:
    return (s.name, s.kind, s.parent)


def _build_index(symbols: list[Symbol]) -> dict[SymbolKey, list[Symbol]]:
    index: dict[SymbolKey, list[Symbol]] = {}
    for s in symbols:
        index.setdefault(_key(s), []).append(s)
    return index


def match_symbols(
    old_symbols: list[Symbol],
    new_symbols: list[Symbol],
) -> list[MatchedSymbol]:
    """Match old symbols to new symbols by (name, kind, parent) key.

    For duplicates with the same key, falls back to signature comparison,
    then positional order.
    """
    old_index = _build_index(old_symbols)
    new_index = _build_index(new_symbols)

    all_keys = dict.fromkeys([*old_index, *new_index])
    results: list[MatchedSymbol] = []

    for key in all_keys:
        olds = list(old_index.get(key, []))
        news = list(new_index.get(key, []))

        # Match by signature first for duplicates
        if len(olds) > 1 or len(news) > 1:
            _match_duplicates(olds, news, results)
        else:
            if olds and news:
                results.append(MatchedSymbol(old=olds[0], new=news[0]))
            elif olds:
                for o in olds:
                    results.append(MatchedSymbol(old=o, new=None))
            else:
                for n in news:
                    results.append(MatchedSymbol(old=None, new=n))

    return results


def _match_duplicates(
    olds: list[Symbol],
    news: list[Symbol],
    results: list[MatchedSymbol],
) -> None:
    """Match duplicates by signature, then positional order."""
    remaining_old = list(olds)
    remaining_new = list(news)

    # Pass 1: exact signature match
    for o in list(remaining_old):
        for n in list(remaining_new):
            if o.signature == n.signature:
                results.append(MatchedSymbol(old=o, new=n))
                remaining_old.remove(o)
                remaining_new.remove(n)
                break

    # Pass 2: positional pairing
    while remaining_old and remaining_new:
        results.append(MatchedSymbol(old=remaining_old.pop(0), new=remaining_new.pop(0)))

    # Leftovers
    for o in remaining_old:
        results.append(MatchedSymbol(old=o, new=None))
    for n in remaining_new:
        results.append(MatchedSymbol(old=None, new=n))


def match_cross_file(
    unmatched_old: dict[str, list[Symbol]],
    unmatched_new: dict[str, list[Symbol]],
) -> list[MatchedSymbol]:
    """Match unmatched symbols across files to detect moves."""
    results: list[MatchedSymbol] = []

    # Flatten new symbols with file info
    new_by_key: dict[SymbolKey, list[tuple[str, Symbol]]] = {}
    for file_path, symbols in unmatched_new.items():
        for s in symbols:
            new_by_key.setdefault(_key(s), []).append((file_path, s))

    matched_new: set[int] = set()

    for old_file, old_symbols in unmatched_old.items():
        for old_sym in old_symbols:
            key = _key(old_sym)
            candidates = new_by_key.get(key, [])
            for i, (new_file, new_sym) in enumerate(candidates):
                if id(new_sym) in matched_new:
                    continue
                # Guard: require matching signature or body hash to avoid
                # false-positive moves between unrelated same-named symbols.
                if old_sym.signature != new_sym.signature and old_sym.body_hash != new_sym.body_hash:
                    continue
                if new_file != old_file:
                    results.append(MatchedSymbol(old=old_sym, new=new_sym, file_from=old_file, file_to=new_file))
                    matched_new.add(id(new_sym))
                    break

    return results
