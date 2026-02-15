"""End-to-end pipeline: diff → DiffGuardOutput."""

from __future__ import annotations

import logging
import time
from typing import Callable

from diffguard.engine._types import Symbol
from diffguard.engine.classifier import classify_changes
from diffguard.engine.matcher import MatchedSymbol, match_cross_file, match_symbols
from diffguard.engine.parser import parse_file
from diffguard.engine.summarizer import build_summary, build_tiered_summary
from diffguard.git import FileDiff, parse_diff
from diffguard.languages import detect_language
from diffguard.schema import (
    DiffStats,
    FileChange,
    DiffGuardOutput,
    Meta,
)

logger = logging.getLogger(__name__)

FileContentProvider = Callable[[str, str], str | None]
"""(ref, path) -> source text or None."""


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

    # For cross-file move detection
    unmatched_old: dict[str, list[Symbol]] = {}
    unmatched_new: dict[str, list[Symbol]] = {}

    for fd in file_diffs:
        fc = _process_file(fd, ref_range, get_content, unmatched_old, unmatched_new)
        file_changes.append(fc)

    # Cross-file moves
    if unmatched_old and unmatched_new:
        moves = match_cross_file(unmatched_old, unmatched_new)
        _apply_moves(moves, file_changes)

    total_add = sum(fd.additions for fd in file_diffs)
    total_del = sum(fd.deletions for fd in file_diffs)

    summary = build_summary(file_changes)
    tiered = build_tiered_summary(
        file_changes, summary,
        include_tests=include_tests,
        show_skipped=show_skipped,
    )

    elapsed_ms = (time.monotonic() - t0) * 1000
    meta = Meta(
        ref_range=ref_range,
        stats=DiffStats(files=len(file_diffs), additions=total_add, deletions=total_del),
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
    unmatched_old: dict[str, list[Symbol]],
    unmatched_new: dict[str, list[Symbol]],
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
    parts = ref_range.split("..")
    old_ref = parts[0] if len(parts) == 2 else f"{ref_range}~1"  # noqa: PLR2004
    new_ref = parts[1] if len(parts) == 2 else ref_range  # noqa: PLR2004

    old_source = get_content(old_ref, fd.old_path or "") if fd.old_path else None
    new_source = get_content(new_ref, fd.new_path or "") if fd.new_path else None

    old_symbols: list[Symbol] = []
    new_symbols: list[Symbol] = []
    parse_error = False

    if old_source is not None:
        pr = parse_file(old_source, language)
        if pr.parse_error:
            parse_error = True
        old_symbols = pr.symbols

    if new_source is not None:
        pr = parse_file(new_source, language)
        if pr.parse_error:
            parse_error = True
        new_symbols = pr.symbols

    matches = match_symbols(old_symbols, new_symbols)
    changes = classify_changes(matches)

    # Collect unmatched for cross-file move detection
    matched_old_ids = {id(m.old) for m in matches if m.old and m.new}
    matched_new_ids = {id(m.new) for m in matches if m.old and m.new}
    um_old = [s for s in old_symbols if id(s) not in matched_old_ids]
    um_new = [s for s in new_symbols if id(s) not in matched_new_ids]
    if um_old:
        unmatched_old[path] = um_old
    if um_new:
        unmatched_new[path] = um_new

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
    from diffguard.engine.classifier import classify_changes as _classify

    move_changes = _classify(moves)
    # Build a mapping from move change name to source/destination paths
    move_paths = {
        m.old.name: (m.file_from, m.file_to)
        for m in moves
        if m.old and m.file_from and m.file_to
    }
    # Index file_changes by path
    fc_map = {fc.path: fc for fc in file_changes}
    for mc in move_changes:
        if mc.kind != "moved":
            continue
        paths = move_paths.get(mc.name)
        if not paths:
            continue
        src_path, dst_path = paths
        # Remove stale added/removed only from source and destination files
        for p in (src_path, dst_path):
            fc = fc_map.get(p)
            if fc is not None:
                fc.changes = [
                    c for c in fc.changes
                    if not (c.name == mc.name and c.kind.endswith(("_added", "_removed")))
                ]
        # Attach move change to the destination file
        dst_fc = fc_map.get(dst_path)
        if dst_fc is not None:
            dst_fc.changes.append(mc)
