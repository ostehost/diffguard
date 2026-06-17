"""Dependency reference scanning — find files that reference changed symbols.

Git access is delegated entirely to :mod:`diffguard.git`; this module owns the
tree-sitter scanning that confirms textual matches are real references.
"""

from __future__ import annotations

import tree_sitter

from diffguard.engine._types import Reference
from diffguard.git import get_file_at_ref, grep_files, list_files_at_ref
from diffguard.languages import detect_language, get_parser

# File globs the git-grep pre-filter restricts to (the supported languages).
_GREP_GLOBS = ("*.py", "*.ts", "*.js", "*.go", "*.tsx", "*.jsx")

# Identifier node types per language
_IDENTIFIER_TYPES: dict[str, set[str]] = {
    "python": {"identifier"},
    "typescript": {"identifier", "property_identifier"},
    "javascript": {"identifier", "property_identifier"},
    "go": {"identifier", "field_identifier"},
}

# Node types that indicate an import context
_IMPORT_PARENT_TYPES: set[str] = {
    "import_statement",
    "import_from_statement",
    "import_clause",
    "import_specifier",
    "import_declaration",
    "import_spec",
}


def _is_import_context(node: tree_sitter.Node) -> bool:
    """Check if a node is in an import context by walking parents."""
    current = node.parent
    while current is not None:
        if current.type in _IMPORT_PARENT_TYPES:
            return True
        current = current.parent
    return False


def _scan_file_for_symbols(
    source: str,
    language: str,
    symbol_names: set[str],
) -> list[tuple[str, int, str, str]]:
    """Scan a file for references to symbol names.

    Returns list of (symbol_name, line, context, source_line).
    """
    parser = get_parser(language)
    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)
    source_lines = source.splitlines()

    id_types = _IDENTIFIER_TYPES.get(language, {"identifier"})
    results: list[tuple[str, int, str, str]] = []

    def _walk(node: tree_sitter.Node) -> None:
        if node.type in id_types:
            name = source_bytes[node.start_byte : node.end_byte].decode("utf-8")
            if name in symbol_names:
                line = node.start_point.row + 1
                ctx = "import" if _is_import_context(node) else "call"
                src_line = source_lines[line - 1].strip() if line <= len(source_lines) else ""
                results.append((name, line, ctx, src_line))
        for child in node.children:
            _walk(child)

    _walk(tree.root_node)
    return results


def _candidate_files(symbols: set[str], ref: str, repo_path: str) -> set[str]:
    """Pre-filter to files that textually contain any symbol name (git grep).

    Returns an empty set when git grep is unavailable, signalling the caller to
    fall back to scanning all files at the ref.
    """
    candidates: set[str] = set()
    for symbol in symbols:
        hits = grep_files(symbol, ref, repo_path, _GREP_GLOBS)
        if hits is None:  # git grep unavailable -> scan all
            return set()
        candidates.update(hits)
    return candidates


def find_references(
    repo_path: str,
    changed_symbols: list[str],
    ref: str,
    changed_files: set[str],
) -> list[Reference]:
    """Find references to changed symbols in files NOT in the diff.

    Uses git grep as a pre-filter to avoid scanning all files with tree-sitter.

    Args:
        repo_path: Path to the git repository.
        changed_symbols: List of symbol names to search for.
        ref: Git ref to scan files at (e.g. HEAD or the "after" ref).
        changed_files: Set of file paths that are in the diff (to exclude).

    Returns:
        List of Reference objects sorted by file path and line.
    """
    if not changed_symbols:
        return []

    symbol_names = set(changed_symbols)

    # Pre-filter with git grep
    candidate_files = _candidate_files(symbol_names, ref, repo_path)
    if candidate_files:
        files_to_scan = sorted(candidate_files - changed_files)
    else:
        # Fallback: scan all files
        all_files = list_files_at_ref(ref, repo_path)
        files_to_scan = [f for f in all_files if f not in changed_files]

    references: list[Reference] = []

    for file_path in files_to_scan:
        language = detect_language(file_path)
        if language is None:
            continue

        source = get_file_at_ref(ref, file_path, repo_path=repo_path)
        if source is None:
            continue

        hits = _scan_file_for_symbols(source, language, symbol_names)
        for sym_name, line, ctx, src_line in hits:
            references.append(
                Reference(
                    file_path=file_path,
                    line=line,
                    symbol_name=sym_name,
                    context=ctx,
                    source_line=src_line,
                )
            )

    references.sort(key=lambda r: (r.file_path, r.line))
    return references
