"""Symbol matching — old↔new symbol pairing for change detection."""

from __future__ import annotations

from diffguard.engine._types import MatchedSymbol, Symbol

SymbolKey = tuple[str, str, str | None]
CrossFileSymbolKey = tuple[str, str, str, str | None]

UnmatchedFileSymbols = tuple[str, list[Symbol]]
"""Parser language and symbols left unmatched within one file."""

UnmatchedByFile = dict[str, UnmatchedFileSymbols]
"""File path -> language-aware cross-file move candidates."""

LocatedSymbol = tuple[str, Symbol]


def _key(s: Symbol) -> SymbolKey:
    return (s.name, s.kind, s.parent)


def _cross_file_key(language: str, symbol: Symbol) -> CrossFileSymbolKey:
    """Return move identity, including the parser language that produced it."""
    return (language, symbol.name, symbol.kind, symbol.parent)


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


def _match_unique_cross_file_evidence(
    old_records: list[LocatedSymbol],
    new_records: list[LocatedSymbol],
    matched_old: set[int],
    matched_new: set[int],
    results: list[MatchedSymbol],
    *,
    require_signature: bool,
    require_body: bool,
) -> None:
    """Match one evidence tier only where both sides have one candidate."""
    candidates_by_old: dict[int, list[int]] = {}
    candidates_by_new: dict[int, list[int]] = {}
    for old_index, (old_file, old_symbol) in enumerate(old_records):
        if old_index in matched_old:
            continue
        for new_index, (new_file, new_symbol) in enumerate(new_records):
            if new_index in matched_new or new_file == old_file:
                continue
            if require_signature and old_symbol.signature != new_symbol.signature:
                continue
            if require_body and old_symbol.body_hash != new_symbol.body_hash:
                continue
            candidates_by_old.setdefault(old_index, []).append(new_index)
            candidates_by_new.setdefault(new_index, []).append(old_index)

    for old_index, candidates in candidates_by_old.items():
        if len(candidates) != 1:
            continue
        new_index = candidates[0]
        if len(candidates_by_new[new_index]) != 1:
            continue
        old_file, old_symbol = old_records[old_index]
        new_file, new_symbol = new_records[new_index]
        results.append(
            MatchedSymbol(
                old=old_symbol,
                new=new_symbol,
                file_from=old_file,
                file_to=new_file,
            )
        )
        matched_old.add(old_index)
        matched_new.add(new_index)


def match_cross_file(
    unmatched_old: UnmatchedByFile,
    unmatched_new: UnmatchedByFile,
) -> list[MatchedSymbol]:
    """Match unmatched symbols across files to detect bounded move candidates.

    Evidence is considered strongest-first: exact signature plus body, exact
    signature, then body. Every tier requires a unique relationship from both
    directions; ambiguous duplicates are left as additions/removals instead of
    inventing a move identity or source path.
    """
    results: list[MatchedSymbol] = []

    old_by_key: dict[CrossFileSymbolKey, list[LocatedSymbol]] = {}
    for file_path, (language, symbols) in unmatched_old.items():
        for symbol in symbols:
            old_by_key.setdefault(_cross_file_key(language, symbol), []).append((file_path, symbol))

    new_by_key: dict[CrossFileSymbolKey, list[LocatedSymbol]] = {}
    for file_path, (language, symbols) in unmatched_new.items():
        for symbol in symbols:
            new_by_key.setdefault(_cross_file_key(language, symbol), []).append((file_path, symbol))

    for key in dict.fromkeys([*old_by_key, *new_by_key]):
        old_records = old_by_key.get(key, [])
        new_records = new_by_key.get(key, [])
        matched_old: set[int] = set()
        matched_new: set[int] = set()

        for require_signature, require_body in (
            (True, True),
            (True, False),
            (False, True),
        ):
            _match_unique_cross_file_evidence(
                old_records,
                new_records,
                matched_old,
                matched_new,
                results,
                require_signature=require_signature,
                require_body=require_body,
            )

    return results
