"""TypeScript/JavaScript language support."""

from __future__ import annotations

import tree_sitter
import tree_sitter_javascript
import tree_sitter_typescript

from diffguard.engine._types import Symbol, compute_body_hash
from diffguard.languages._utils import node_text

# Default to TypeScript grammar (superset of JS)
_ts_lang = tree_sitter.Language(tree_sitter_typescript.language_typescript())
_js_lang = tree_sitter.Language(tree_sitter_javascript.language())


def get_language() -> tree_sitter.Language:
    """Return the tree-sitter Language object for TypeScript."""
    return _ts_lang


def get_js_language() -> tree_sitter.Language:
    """Return the tree-sitter Language object for JavaScript."""
    return _js_lang


def extract_symbols(tree: tree_sitter.Tree, source: bytes) -> list[Symbol]:
    """Extract symbols from a parsed JavaScript/TypeScript tree."""
    symbols: list[Symbol] = []
    _walk_node(tree.root_node, source, symbols, parent_class=None)
    return symbols


def _walk_node(
    node: tree_sitter.Node,
    source: bytes,
    symbols: list[Symbol],
    parent_class: str | None,
) -> None:
    """Recursively walk the tree and extract symbols."""
    for child in node.children:
        if child.type == "function_declaration":
            _extract_function(child, symbols)
        elif child.type == "class_declaration":
            _extract_class(child, source, symbols)
        elif child.type in ("lexical_declaration", "variable_declaration"):
            _extract_arrow_functions(child, symbols)
        elif child.type == "export_statement":
            _walk_node(child, source, symbols, parent_class)


def _extract_function(
    node: tree_sitter.Node,
    symbols: list[Symbol],
) -> None:
    """Extract a function declaration."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    name = node_text(name_node)
    params_node = node.child_by_field_name("parameters")
    params = node_text(params_node) if params_node else "()"
    signature = f"function {name}{params}"
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


def _extract_class(
    node: tree_sitter.Node,
    source: bytes,
    symbols: list[Symbol],
) -> None:
    """Extract a class declaration and its methods."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    class_name = node_text(name_node)
    body_node = node.child_by_field_name("body")
    body_text = node_text(body_node) if body_node else ""

    heritage = ""
    for child in node.children:
        if child.type == "class_heritage":
            heritage = f" {node_text(child)}"
            break

    symbols.append(
        Symbol(
            name=class_name,
            kind="class",
            signature=f"class {class_name}{heritage}",
            start_line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
            body_hash=compute_body_hash(body_text),
        )
    )

    if body_node:
        for child in body_node.children:
            if child.type == "method_definition":
                _extract_method(child, symbols, class_name)


def _extract_method(
    node: tree_sitter.Node,
    symbols: list[Symbol],
    class_name: str,
) -> None:
    """Extract a method definition."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    name = node_text(name_node)
    params_node = node.child_by_field_name("parameters")
    params = node_text(params_node) if params_node else "()"
    signature = f"{name}{params}"
    body_node = node.child_by_field_name("body")
    body_text = node_text(body_node) if body_node else ""

    symbols.append(
        Symbol(
            name=name,
            kind="method",
            signature=signature,
            start_line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
            body_hash=compute_body_hash(body_text),
            parent=class_name,
        )
    )


def _extract_arrow_functions(
    node: tree_sitter.Node,
    symbols: list[Symbol],
) -> None:
    """Extract arrow functions assigned to variables."""
    # Detect keyword (const/let/var)
    keyword = "const"
    for sib in node.children:
        if not sib.is_named:
            t = node_text(sib)
            if t in ("const", "let", "var"):
                keyword = t
                break

    for child in node.children:
        if child.type == "variable_declarator":
            name_node = child.child_by_field_name("name")
            value_node = child.child_by_field_name("value")
            if name_node and value_node and value_node.type == "arrow_function":
                name = node_text(name_node)
                params_node = value_node.child_by_field_name("parameters")
                if params_node:
                    params = node_text(params_node)
                else:
                    param_node = value_node.child_by_field_name("parameter")
                    params = f"({node_text(param_node)})" if param_node else "()"

                signature = f"{keyword} {name} = {params} =>"
                body_node = value_node.child_by_field_name("body")
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
