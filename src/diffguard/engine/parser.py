"""Tree-sitter parsing and symbol extraction."""

from __future__ import annotations

import tree_sitter

from diffguard.engine._types import ParseResult, Symbol, compute_body_hash
from diffguard.languages import get_language_module, get_parser

# Re-export for backwards compatibility
__all__ = ["ParseResult", "Symbol", "compute_body_hash", "parse_file"]


def parse_file(source: str, language: str) -> ParseResult:
    """Parse source code and extract symbols.

    Args:
        source: The source code text
        language: Language identifier ("python", "typescript", "javascript", "go")

    Returns:
        ParseResult with extracted symbols
    """
    try:
        parser = get_parser(language)
    except ValueError as e:
        return ParseResult(
            symbols=[],
            language=language,
            parse_error=True,
            error_message=str(e),
        )

    source_bytes = source.encode("utf-8")
    tree: tree_sitter.Tree = parser.parse(source_bytes)

    has_error = tree.root_node.has_error

    lang_module = get_language_module(language)
    symbols = lang_module.extract_symbols(tree, source_bytes)

    return ParseResult(
        symbols=symbols,
        language=language,
        parse_error=has_error,
        error_message="Parse errors detected in source" if has_error else None,
    )
