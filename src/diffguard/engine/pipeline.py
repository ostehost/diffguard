"""End-to-end pipeline: diff → DiffGuardOutput."""

from __future__ import annotations

import logging
import time
from typing import Callable

from diffguard.engine._refs import split_ref_range
from diffguard.engine._types import MatchedSymbol, Symbol
from diffguard.engine.classifier import classify_changes
from diffguard.engine.matcher import UnmatchedByFile, match_cross_file, match_symbols
from diffguard.engine.parser import parse_file
from diffguard.engine.signatures import compare_signatures
from diffguard.engine.summarizer import build_summary, build_tiered_summary
from diffguard.diff import FileDiff, parse_diff
from diffguard.languages import detect_language
from diffguard.schema import (
    DiffStats,
    DiffGuardOutput,
    FileChange,
    Meta,
    SymbolChange,
)

logger = logging.getLogger(__name__)

FileContentProvider = Callable[[str, str], str | None]
"""(ref, path) -> source text or None."""

_INCOMPLETE_DIFF_WARNING = (
    "diff contains file headers that could not be parsed — analysis incomplete"
)


def _split_diff_records(diff_text: str) -> list[str]:
    """Return each raw ``diff --git`` record, excluding leading preamble."""
    records: list[str] = []
    current: list[str] = []
    for line in diff_text.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if current:
                records.append("".join(current))
            current = [line]
        elif current:
            current.append(line)
    if current:
        records.append("".join(current))
    return records


def _has_unparsed_diff_records(diff_text: str, *, skip_generated: bool) -> bool:
    """Detect raw file records discarded by the tolerant diff parser.

    Parsing each record independently preserves valid duplicate headers used by
    mode changes and worktree recreation while still exposing a malformed
    record beside otherwise valid files.
    """
    records = _split_diff_records(diff_text)
    parsed_records = sum(
        len(parse_diff(record, skip_generated=skip_generated)) for record in records
    )
    return parsed_records != len(records)


def run_pipeline(
    diff_text: str,
    ref_range: str,
    get_content: FileContentProvider | None = None,
    *,
    skip_generated: bool = False,
    include_tests: bool = False,
    show_skipped: bool = False,
) -> DiffGuardOutput:
    """Run the full analysis pipeline on a unified diff.

    Args:
        diff_text: Raw unified diff text.
        ref_range: e.g. ``"abc123..def456"`` — used only for metadata.
        get_content: Optional callback ``(ref, path) -> source``.
            When *None*, file-level symbol analysis is skipped and only
            diff-level stats are reported.

    Returns:
        Fully populated :class:`DiffGuardOutput`.
    """
    t0 = time.monotonic()

    file_diffs = parse_diff(diff_text, skip_generated=skip_generated)
    file_changes: list[FileChange] = []
    warnings: list[str] = []
    if _has_unparsed_diff_records(diff_text, skip_generated=skip_generated):
        warnings.append(_INCOMPLETE_DIFF_WARNING)

    # For cross-file move detection
    unmatched_old: UnmatchedByFile = {}
    unmatched_new: UnmatchedByFile = {}

    # A path can occur more than once in an assembled worktree patch (for
    # example, malformed or otherwise ambiguous split records).  Path alone
    # cannot identify which FileChange owns a possible move in that case, so
    # retain every record as classified and exclude the ambiguous path from
    # cross-file reconciliation.
    path_counts: dict[str, int] = {}
    for file_diff in file_diffs:
        path_counts[file_diff.path] = path_counts.get(file_diff.path, 0) + 1

    for fd in file_diffs:
        fc = _process_file(
            fd,
            ref_range,
            get_content,
            unmatched_old,
            unmatched_new,
            warnings,
            collect_move_candidates=path_counts[fd.path] == 1,
        )
        file_changes.append(fc)

    # Cross-file moves
    if unmatched_old and unmatched_new:
        moves = match_cross_file(unmatched_old, unmatched_new)
        _apply_moves(moves, file_changes)

    total_add = sum(fd.additions for fd in file_diffs)
    total_del = sum(fd.deletions for fd in file_diffs)

    summary = build_summary(file_changes)
    tiered = build_tiered_summary(
        file_changes,
        summary,
        include_tests=include_tests,
        show_skipped=show_skipped,
    )

    elapsed_ms = (time.monotonic() - t0) * 1000
    meta = Meta(
        ref_range=ref_range,
        stats=DiffStats(files=len(file_diffs), additions=total_add, deletions=total_del),
        warnings=warnings,
        timing_ms=round(elapsed_ms, 2),
    )

    return DiffGuardOutput(
        meta=meta,
        files=file_changes,
        summary=summary,
        tiered=tiered,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _process_file(
    fd: FileDiff,
    ref_range: str,
    get_content: FileContentProvider | None,
    unmatched_old: UnmatchedByFile,
    unmatched_new: UnmatchedByFile,
    warnings: list[str],
    *,
    collect_move_candidates: bool,
) -> FileChange:
    """Process a single FileDiff into a FileChange."""
    path = fd.path

    if fd.generated:
        return FileChange(path=path, change_type=fd.change_type, generated=True)

    if fd.binary:
        return FileChange(path=path, change_type=fd.change_type, binary=True)

    language = detect_language(path)
    if language is None:
        return FileChange(
            path=path,
            change_type=fd.change_type,
            unsupported_language=True,
        )

    if get_content is None:
        return FileChange(path=path, language=language, change_type=fd.change_type)

    # Resolve refs from ref_range
    old_ref, new_ref = split_ref_range(ref_range)

    old_source = get_content(old_ref, fd.old_path or "") if fd.old_path else None
    new_source = get_content(new_ref, fd.new_path or "") if fd.new_path else None

    # If the diff says a side exists but its content can't be fetched, the
    # symbol comparison would be made against an empty baseline and fabricate
    # spurious "everything added/removed" changes. Skip analysis and surface a
    # warning instead of emitting misleading findings.
    if (fd.old_path is not None and old_source is None) or (
        fd.new_path is not None and new_source is None
    ):
        warnings.append(f"{path}: content unavailable at ref — symbol analysis skipped")
        return FileChange(path=path, language=language, change_type=fd.change_type)

    old_symbols: list[Symbol] = []
    new_symbols: list[Symbol] = []
    parse_error = False

    if old_source is not None:
        pr = parse_file(old_source, language, file_path=fd.old_path)
        if pr.parse_error:
            parse_error = True
        old_symbols = pr.symbols

    if new_source is not None:
        pr = parse_file(new_source, language, file_path=fd.new_path)
        if pr.parse_error:
            parse_error = True
        new_symbols = pr.symbols

    if parse_error:
        warnings.append(f"{path}: parse gap — symbol analysis skipped")
        return FileChange(
            path=path,
            language=language,
            change_type=fd.change_type,
            parse_error=True,
        )

    matches = match_symbols(old_symbols, new_symbols)
    changes = classify_changes(
        matches,
        lambda old, new: compare_signatures(old, new, language),
    )

    # Collect unmatched for cross-file move detection
    matched_old_ids = {id(m.old) for m in matches if m.old and m.new}
    matched_new_ids = {id(m.new) for m in matches if m.old and m.new}
    um_old = [s for s in old_symbols if id(s) not in matched_old_ids]
    um_new = [s for s in new_symbols if id(s) not in matched_new_ids]
    if collect_move_candidates and um_old:
        unmatched_old[path] = (language, um_old)
    if collect_move_candidates and um_new:
        unmatched_new[path] = (language, um_new)

    return FileChange(
        path=path,
        language=language,
        change_type=fd.change_type,
        parse_error=parse_error,
        changes=changes,
    )


def _apply_moves(
    moves: list[MatchedSymbol],
    file_changes: list[FileChange],
) -> None:
    """Inject cross-file move changes into file_changes and remove stale add/remove."""
    file_changes_by_path: dict[str, list[FileChange]] = {}
    for file_change in file_changes:
        file_changes_by_path.setdefault(file_change.path, []).append(file_change)
    for move in moves:
        if move.old is None or move.new is None or move.file_from is None or move.file_to is None:
            continue
        source_candidates = file_changes_by_path.get(move.file_from, [])
        destination_candidates = file_changes_by_path.get(move.file_to, [])
        if len(source_candidates) != 1 or len(destination_candidates) != 1:
            continue
        src_fc = source_candidates[0]
        dst_fc = destination_candidates[0]
        if src_fc.language != dst_fc.language:
            continue
        move_changes = classify_changes(
            [move],
            lambda old, new: compare_signatures(old, new, dst_fc.language or "unknown"),
        )
        if not move_changes:
            continue
        move_change = move_changes[0]
        src_fc.changes = _remove_one_symbol_change(src_fc.changes, move.old, "_removed")
        dst_fc.changes = _remove_one_symbol_change(dst_fc.changes, move.new, "_added")
        dst_fc.changes.append(move_change)

        # A body-equal move can still change its signature. Preserve that
        # contract finding instead of letting the move classification hide it.
        if move.old.signature != move.new.signature:
            signature_changes = classify_changes(
                [MatchedSymbol(old=move.old, new=move.new)],
                lambda old, new: compare_signatures(old, new, dst_fc.language or "unknown"),
            )
            dst_fc.changes.extend(signature_changes)


def _remove_one_symbol_change(
    changes: list[SymbolChange],
    symbol: Symbol,
    kind_suffix: str,
) -> list[SymbolChange]:
    """Remove the one add/remove record represented by *symbol*.

    Duplicate declarations can legally share a name within one file.  Match
    the classifier's signature and source line first so reconciling one move
    cannot erase an unrelated same-named addition or removal.  The name-only
    fallback keeps this helper tolerant of older/manually built model values
    that do not carry signature/line evidence, while still removing at most
    one record.
    """
    matching_index: int | None = None
    fallback_index: int | None = None
    for index, change in enumerate(changes):
        if change.name != symbol.name or not change.kind.endswith(kind_suffix):
            continue
        if fallback_index is None:
            fallback_index = index
        if change.signature == symbol.signature and change.line == symbol.start_line:
            matching_index = index
            break

    remove_index = matching_index if matching_index is not None else fallback_index
    if remove_index is None:
        return changes
    return changes[:remove_index] + changes[remove_index + 1 :]
