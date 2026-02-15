"""Go language support."""

from __future__ import annotations

import tree_sitter
import tree_sitter_go

from diffguard.engine._types import Symbol, compute_body_hash
from diffguard.languages._utils import node_text


def get_language() -> tree_sitter.Language:
    """Return the tree-sitter Language object for Go."""
    return tree_sitter.Language(tree_sitter_go.language())


def extract_symbols(tree: tree_sitter.Tree, source: bytes) -> list[Symbol]:
    """Extract symbols from a parsed Go tree."""
    symbols: list[Symbol] = []
    for child in tree.root_node.children:
        if child.type == "function_declaration":
            _extract_function(child, symbols)
        elif child.type == "method_declaration":
            _extract_method(child, symbols)
    return symbols


def _extract_function(
    node: tree_sitter.Node,
    symbols: list[Symbol],
) -> None:
    """Extract a Go function declaration."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    name = node_text(name_node)
    params_node = node.child_by_field_name("parameters")
    params = node_text(params_node) if params_node else "()"
    result_node = node.child_by_field_name("result")
    result = f" {node_text(result_node)}" if result_node else ""
    signature = f"func {name}{params}{result}"
    body_node = node.child_by_field_name("body")
    body_text = node_text(body_node) if body_node else ""

    symbols.append(
        Symbol(
            name=name,
            kind="function",
            signature=signature,
            start_line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
            body_hash=compute_body_hash(body_text),
        )
    )


def _extract_method(
    node: tree_sitter.Node,
    symbols: list[Symbol],
) -> None:
    """Extract a Go method declaration."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    name = node_text(name_node)
    receiver_node = node.child_by_field_name("receiver")
    receiver = node_text(receiver_node) if receiver_node else ""
    params_node = node.child_by_field_name("parameters")
    params = node_text(params_node) if params_node else "()"
    result_node = node.child_by_field_name("result")
    result = f" {node_text(result_node)}" if result_node else ""
    signature = f"func {receiver} {name}{params}{result}"
    body_node = node.child_by_field_name("body")
    body_text = node_text(body_node) if body_node else ""
    parent_type = _extract_receiver_type(receiver_node) if receiver_node else None

    symbols.append(
        Symbol(
            name=name,
            kind="method",
            signature=signature,
            start_line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
            body_hash=compute_body_hash(body_text),
            parent=parent_type,
        )
    )


def _extract_receiver_type(receiver_node: tree_sitter.Node) -> str | None:
    """Extract the type name from a receiver parameter list."""
    for child in receiver_node.children:
        if child.type == "parameter_declaration":
            for tc in child.children:
                if tc.type == "type_identifier":
                    return node_text(tc)
                if tc.type == "pointer_type":
                    for ptc in tc.children:
                        if ptc.type == "type_identifier":
                            return node_text(ptc)
    return None
