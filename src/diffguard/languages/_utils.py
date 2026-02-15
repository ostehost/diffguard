"""Shared utilities for language modules."""

from __future__ import annotations

import tree_sitter


def node_text(node: tree_sitter.Node) -> str:
    """Safely get the text of a tree-sitter node."""
    text = node.text
    if text is None:
        return ""
    return text.decode("utf-8")
