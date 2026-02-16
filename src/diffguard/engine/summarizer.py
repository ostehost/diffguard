"""Tiered summary generation.

Orders output by review priority, NOT file order.
Priority: breaking changes > new public API > behavioral modifications > structural/refactors.
"""

from __future__ import annotations

import os
import re
from collections import Counter

from diffguard.schema import FileChange, Summary, SymbolChange, TieredSummary

# ---- Priority buckets (lower = higher priority) ----
_P_BREAKING = 0
_P_REMOVED = 1
_P_SIG_CHANGED = 2
_P_ADDED = 3
_P_MODIFIED = 4
_P_MOVED = 5

_KIND_PRIORITY: dict[str, int] = {
    "signature_changed": _P_SIG_CHANGED,
    "function_removed": _P_REMOVED,
    "class_removed": _P_REMOVED,
    "function_added": _P_ADDED,
    "class_added": _P_ADDED,
    "function_modified": _P_MODIFIED,
    "class_modified": _P_MODIFIED,
    "moved": _P_MOVED,
}

# ---- Test file detection ----
_TEST_PATH_RE = re.compile(
    r"(^|/)(tests?/|spec/|__tests__/)"
    r"|"
    r"(^|/)test_[^/]*\.py$"
    r"|"
    r"(^|/)[^/]*_test\.py$"
    r"|"
    r"(^|/)[^/]*[._]spec\.(ts|js|tsx|jsx)$"
    r"|"
    r"(^|/)[^/]*[._]test\.(ts|js|tsx|jsx)$",
    re.IGNORECASE,
)

_DETAILED_CAP = 15


def is_test_file(path: str) -> bool:
    """Return True if *path* looks like a test file."""
    return _TEST_PATH_RE.search(path) is not None


def _change_priority(c: SymbolChange) -> int:
    if c.breaking:
        return _P_BREAKING
    return _KIND_PRIORITY.get(c.kind, _P_MODIFIED)


def _all_changes_sorted(files: list[FileChange]) -> list[tuple[str, SymbolChange]]:
    """Collect all changes across files, sorted by review priority."""
    pairs: list[tuple[str, SymbolChange]] = []
    for fc in files:
        for c in fc.changes:
            pairs.append((fc.path, c))
    pairs.sort(key=lambda p: (_change_priority(p[1]), p[0], p[1].name))
    return pairs


def _partition_files(
    files: list[FileChange],
    *,
    include_tests: bool,
) -> tuple[list[FileChange], list[FileChange]]:
    """Split files into (production, test). If include_tests, test list is populated."""
    prod: list[FileChange] = []
    test: list[FileChange] = []
    for fc in files:
        if is_test_file(fc.path):
            test.append(fc)
        else:
            prod.append(fc)
    if include_tests:
        return prod, test
    return prod, []


# ---------------------------------------------------------------------------
# Summary (structured)
# ---------------------------------------------------------------------------


def build_summary(
    files: list[FileChange],
    *,
    include_tests: bool = True,
) -> Summary:
    """Build the structured Summary from classified file changes.

    When *include_tests* is False the summary counts/focus still reflect all
    files (they feed the JSON output), so this function always uses all files.
    """
    counter: Counter[str] = Counter()
    breaking: list[SymbolChange] = []
    focus: list[str] = []

    for fc in files:
        for c in fc.changes:
            counter[c.kind] += 1
            if c.breaking:
                breaking.append(c)

    # Build focus list (3-5 items, priority ordered)
    sorted_changes = _all_changes_sorted(files)
    seen: set[str] = set()
    for path, c in sorted_changes:
        if len(focus) >= 5:
            break
        label = _focus_label(path, c)
        if label not in seen:
            seen.add(label)
            focus.append(label)

    return Summary(
        change_types=dict(counter),
        breaking_changes=breaking,
        focus=focus,
    )


def _focus_label(path: str, c: SymbolChange) -> str:
    """Human-readable focus item."""
    if c.breaking:
        return f"BREAKING: `{c.name}` signature changed"
    kind_map = {
        "function_removed": f"Removed `{c.name}`",
        "class_removed": f"Removed class `{c.name}`",
        "function_added": f"New `{c.name}`",
        "class_added": f"New class `{c.name}`",
        "signature_changed": f"Signature change: `{c.name}`",
        "function_modified": f"Modified `{c.name}`",
        "class_modified": f"Modified class `{c.name}`",
        "moved": f"Moved `{c.name}` from {c.file_from}",
    }
    return kind_map.get(c.kind, f"Changed `{c.name}` in {path}")


# ---------------------------------------------------------------------------
# Tiered summaries (human-readable text)
# ---------------------------------------------------------------------------


def build_tiered_summary(
    files: list[FileChange],
    summary: Summary,
    *,
    include_tests: bool = False,
    show_skipped: bool = False,
) -> TieredSummary:
    """Generate oneliner / short / detailed summaries.

    Args:
        include_tests: Include test-file symbols in the text output.
        show_skipped: Show "Skipped (unsupported)" files in detailed output.
    """
    prod_files, test_files = _partition_files(files, include_tests=include_tests)
    # For oneliner/short we only use production changes
    prod_changes = _all_changes_sorted(prod_files)

    # Check if there are any test-file changes (even when not included)
    all_test_files = [f for f in files if is_test_file(f.path)]
    has_test_changes = any(c for f in all_test_files for c in f.changes)

    all_changes = _all_changes_sorted(files)
    if not all_changes:
        n = sum(1 for f in files if not f.generated and not f.binary and not f.unsupported_language)
        if n == 0:
            return TieredSummary(
                oneliner="No substantive code changes.",
                short="No substantive code changes.",
                detailed="No substantive code changes.",
            )
        return TieredSummary(
            oneliner=f"Changed {n} file(s) with no symbol-level modifications.",
            short=f"Changed {n} file(s) with no symbol-level modifications.",
            detailed=f"Changed {n} file(s) with no symbol-level modifications.",
        )

    # If there are only test changes (no prod), still show something meaningful
    if not prod_changes and has_test_changes:
        oneliner = "Test-only changes"
        short = "Test-only changes"
    elif not prod_changes:
        oneliner = "No substantive code changes."
        short = "No substantive code changes."
    else:
        oneliner = _build_oneliner(prod_changes, summary)
        short = _build_short(prod_changes, summary)

    detailed = _build_detailed(
        prod_files,
        test_files,
        files,
        summary,
        show_skipped=show_skipped,
    )

    # Unsupported-file warning (short + detailed only, when not --show-skipped)
    warning = _unsupported_warning(files, show_skipped=show_skipped)
    if warning:
        short = f"{short}\n{warning}"
        detailed = f"{detailed}\n{warning}" if detailed else warning

    return TieredSummary(oneliner=oneliner, short=short, detailed=detailed)


def _unsupported_warning(
    files: list[FileChange],
    *,
    show_skipped: bool,
) -> str | None:
    """Return a ⚠ warning line if there are unsupported files and show_skipped is off."""
    if show_skipped:
        return None
    unsupported = [f for f in files if f.unsupported_language]
    if not unsupported:
        return None
    exts: set[str] = set()
    for f in unsupported:
        _, ext = os.path.splitext(f.path)
        exts.add(ext if ext else f.path.rsplit("/", 1)[-1])
    sorted_exts = ", ".join(sorted(exts))
    n = len(unsupported)
    return (
        f"⚠ {n} file{'s' if n != 1 else ''} skipped (unsupported: {sorted_exts}) — review manually"
    )


def _build_oneliner(
    sorted_changes: list[tuple[str, SymbolChange]],
    summary: Summary,
) -> str:
    """~20 tokens: most impactful change only."""
    if summary.breaking_changes:
        bc = summary.breaking_changes[0]
        return f"BREAKING: `{bc.name}` signature changed"
    _, top = sorted_changes[0]
    verb = {
        "function_added": "Add",
        "class_added": "Add",
        "function_removed": "Remove",
        "class_removed": "Remove",
        "function_modified": "Modify",
        "class_modified": "Modify",
        "signature_changed": "Change signature of",
        "moved": "Move",
    }.get(top.kind, "Change")
    return f"{verb} `{top.name}`"


def _build_short(
    sorted_changes: list[tuple[str, SymbolChange]],
    summary: Summary,
) -> str:
    """~80 tokens: breaking first, then behavioral, then structural."""
    parts: list[str] = []

    # Breaking
    if summary.breaking_changes:
        names = ", ".join(f"`{c.name}`" for c in summary.breaking_changes[:3])
        parts.append(f"Breaking: {names}")

    # Behavioural (added/removed/sig changed — non-breaking)
    behavioural: list[str] = []
    for _, c in sorted_changes:
        if c.breaking:
            continue
        if c.kind in (
            "function_added",
            "class_added",
            "function_removed",
            "class_removed",
            "signature_changed",
        ):
            behavioural.append(f"`{c.name}` ({c.kind.split('_')[-1]})")
        if len(behavioural) >= 4:
            break
    if behavioural:
        parts.append("; ".join(behavioural))

    # Structural only if nothing else
    if not parts:
        mod_count = sum(1 for _, c in sorted_changes if c.kind.endswith("_modified"))
        move_count = sum(1 for _, c in sorted_changes if c.kind == "moved")
        bits: list[str] = []
        if mod_count:
            bits.append(f"{mod_count} modified")
        if move_count:
            bits.append(f"{move_count} moved")
        parts.append("Refactor: " + ", ".join(bits) if bits else "Minor changes")

    return ". ".join(parts)


def _emit_change_sections(
    sorted_changes: list[tuple[str, SymbolChange]],
    lines: list[str],
    *,
    cap: int | None = None,
    include_breaking: bool = True,
) -> int:
    """Append change sections to *lines*. Returns number of items emitted."""
    sections: dict[str, list[str]] = {
        "Removed": [],
        "Signature Changes": [],
        "Added": [],
        "Modified": [],
        "Moved": [],
    }
    total = 0
    for path, c in sorted_changes:
        if c.breaking and not include_breaking:
            continue
        if c.breaking:
            continue  # breaking handled separately
        if c.kind in ("function_removed", "class_removed"):
            sections["Removed"].append(f"- `{c.name}` ({path})")
        elif c.kind == "signature_changed":
            sections["Signature Changes"].append(
                f"- `{c.name}`: {c.before_signature} → {c.after_signature}"
            )
        elif c.kind in ("function_added", "class_added"):
            sections["Added"].append(f"- `{c.name}` ({path})")
        elif c.kind in ("function_modified", "class_modified"):
            sections["Modified"].append(f"- `{c.name}` ({path})")
        elif c.kind == "moved":
            sections["Moved"].append(f"- `{c.name}` from {c.file_from}")
        total += 1

    emitted = 0
    for heading, items in sections.items():
        if items:
            to_show = items
            if cap is not None:
                remaining = cap - emitted
                if remaining <= 0:
                    break
                to_show = items[:remaining]
            lines.append(f"## {heading}")
            lines.extend(to_show)
            lines.append("")
            emitted += len(to_show)

    return emitted


def _build_detailed(
    prod_files: list[FileChange],
    test_files: list[FileChange],
    all_files: list[FileChange],
    summary: Summary,
    *,
    show_skipped: bool = False,
) -> str:
    """Full detail, ordered by review priority, capped at top-N."""
    lines: list[str] = []
    prod_changes = _all_changes_sorted(prod_files)
    test_changes = _all_changes_sorted(test_files)

    # Breaking changes (always shown, not counted toward cap)
    if summary.breaking_changes:
        lines.append("## Breaking Changes")
        for c in summary.breaking_changes:
            lines.append(f"- `{c.name}`: {c.before_signature} → {c.after_signature}")
        lines.append("")

    # Production changes (capped)
    total_prod = len(prod_changes)
    emitted = _emit_change_sections(prod_changes, lines, cap=_DETAILED_CAP)
    remaining_prod = total_prod - emitted
    # Count breaking that were skipped from sections (they're shown above)
    breaking_in_prod = sum(1 for _, c in prod_changes if c.breaking)
    remaining_prod -= breaking_in_prod

    # Test changes section
    if test_changes:
        lines.append("## Test Changes")
        test_emitted = _emit_change_sections(
            test_changes,
            lines,
            cap=_DETAILED_CAP - emitted if emitted < _DETAILED_CAP else 5,
        )
        remaining_test = len(test_changes) - test_emitted
        remaining_prod += remaining_test

    if remaining_prod > 0:
        lines.append(f"(and {remaining_prod} more)")
        lines.append("")

    # Skipped files (opt-in)
    if show_skipped:
        skipped = [f for f in all_files if f.generated or f.binary or f.unsupported_language]
        if skipped:
            lines.append("## Skipped")
            for f in skipped:
                reason = "generated" if f.generated else "binary" if f.binary else "unsupported"
                lines.append(f"- {f.path} ({reason})")
            lines.append("")

    return "\n".join(lines).strip()
