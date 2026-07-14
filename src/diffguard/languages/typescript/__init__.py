"""TypeScript/JavaScript language support."""

from __future__ import annotations

import tree_sitter
import tree_sitter_javascript
import tree_sitter_typescript

from diffguard.engine._types import Symbol, compute_body_hash
from diffguard.languages._utils import node_text

# Default to TypeScript grammar (superset of JS)
_ts_lang = tree_sitter.Language(tree_sitter_typescript.language_typescript())
_tsx_lang = tree_sitter.Language(tree_sitter_typescript.language_tsx())
_js_lang = tree_sitter.Language(tree_sitter_javascript.language())

_CALLABLE_MODIFIER_TYPES = {
    "abstract",
    "accessibility_modifier",
    "async",
    "get",
    "override_modifier",
    "set",
    "static",
}


def get_language() -> tree_sitter.Language:
    """Return the tree-sitter Language object for TypeScript."""
    return _ts_lang


def get_tsx_language() -> tree_sitter.Language:
    """Return the tree-sitter Language object for TSX."""
    return _tsx_lang


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
    declaration_modifiers: str = "",
) -> None:
    """Recursively walk the tree and extract symbols."""
    for child in node.children:
        if child.type in {"function_declaration", "generator_function_declaration"}:
            _extract_function(child, symbols, declaration_modifiers)
        elif child.type in {"abstract_class_declaration", "class_declaration"}:
            _extract_class(child, source, symbols, declaration_modifiers)
        elif child.type in ("lexical_declaration", "variable_declaration"):
            _extract_arrow_functions(child, symbols, declaration_modifiers)
        elif child.type == "export_statement":
            _walk_node(
                child,
                source,
                symbols,
                parent_class,
                _export_modifiers(child),
            )


def _extract_function(
    node: tree_sitter.Node,
    symbols: list[Symbol],
    declaration_modifiers: str,
) -> None:
    """Extract a function declaration."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    name = node_text(name_node)
    type_params = node.child_by_field_name("type_parameters")
    generic = node_text(type_params) if type_params else ""
    params_node = node.child_by_field_name("parameters")
    params = node_text(params_node) if params_node else "()"
    return_type = node.child_by_field_name("return_type")
    returns = node_text(return_type) if return_type else ""
    modifiers = _callable_modifiers(node)
    prefix = _declaration_prefix(declaration_modifiers, modifiers)
    function_keyword = "function*" if _has_child_type(node, "*") else "function"
    signature = f"{prefix}{function_keyword} {name}{generic}{params}{returns}"
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
    declaration_modifiers: str,
) -> None:
    """Extract a class declaration and its methods."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    class_name = node_text(name_node)
    type_params = node.child_by_field_name("type_parameters")
    generic = node_text(type_params) if type_params else ""
    body_node = node.child_by_field_name("body")
    body_text = node_text(body_node) if body_node else ""

    heritage = ""
    for child in node.children:
        if child.type == "class_heritage":
            heritage = f" {node_text(child)}"
            break

    class_keyword = "abstract class" if node.type == "abstract_class_declaration" else "class"
    prefix = _declaration_prefix(declaration_modifiers)
    symbols.append(
        Symbol(
            name=class_name,
            kind="class",
            signature=f"{prefix}{class_keyword} {class_name}{generic}{heritage}",
            start_line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
            body_hash=compute_body_hash(body_text),
        )
    )

    if body_node:
        for child in body_node.children:
            if child.type in {"abstract_method_signature", "method_definition"}:
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
    type_params = node.child_by_field_name("type_parameters")
    generic = node_text(type_params) if type_params else ""
    params_node = node.child_by_field_name("parameters")
    params = node_text(params_node) if params_node else "()"
    return_type = node.child_by_field_name("return_type")
    returns = node_text(return_type) if return_type else ""
    modifiers = _callable_modifiers(node)
    prefix = f"{modifiers} " if modifiers else ""
    name_prefix = "*" if _has_child_type(node, "*") else ""
    optional = "?" if _has_child_type(node, "?") else ""
    signature = f"{prefix}{name_prefix}{name}{optional}{generic}{params}{returns}"
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
    declaration_modifiers: str,
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

                return_type = value_node.child_by_field_name("return_type")
                returns = node_text(return_type) if return_type else ""
                type_params = value_node.child_by_field_name("type_parameters")
                generic = node_text(type_params) if type_params else ""
                modifiers = _callable_modifiers(value_node)
                declaration_prefix = _declaration_prefix(declaration_modifiers)
                callable_prefix = _declaration_prefix(modifiers)
                signature = (
                    f"{declaration_prefix}{keyword} {name} = "
                    f"{callable_prefix}{generic}{params}{returns} =>"
                )
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


def _callable_modifiers(node: tree_sitter.Node) -> str:
    """Return source-ordered callable modifiers represented by tree-sitter."""
    return " ".join(
        node_text(child) for child in node.children if child.type in _CALLABLE_MODIFIER_TYPES
    )


def _export_modifiers(node: tree_sitter.Node) -> str:
    """Return the export wrapper state omitted from a nested declaration node."""
    return " ".join(
        node_text(child) for child in node.children if child.type in {"default", "export"}
    )


def _declaration_prefix(*modifier_groups: str) -> str:
    """Join non-empty declaration modifier groups with one trailing space."""
    modifiers = " ".join(group for group in modifier_groups if group)
    return f"{modifiers} " if modifiers else ""


def _has_child_type(node: tree_sitter.Node, node_type: str) -> bool:
    """Return whether a direct child carries a callable syntax marker."""
    return any(child.type == node_type for child in node.children)
