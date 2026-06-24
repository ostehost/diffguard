"""Tree-sitter parsing and symbol extraction."""

from __future__ import annotations

import tree_sitter

from diffguard.engine._types import ParseResult
from diffguard.languages import get_language_module, get_parser


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
    except ValueError:
        return ParseResult(symbols=[], parse_error=True)

    source_bytes = source.encode("utf-8")
    tree: tree_sitter.Tree = parser.parse(source_bytes)

    lang_module = get_language_module(language)
    symbols = lang_module.extract_symbols(tree, source_bytes)

    return ParseResult(symbols=symbols, parse_error=tree.root_node.has_error)
