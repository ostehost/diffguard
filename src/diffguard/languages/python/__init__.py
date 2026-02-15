"""Python language support."""

from __future__ import annotations

import tree_sitter
import tree_sitter_python

from diffguard.engine._types import Symbol, compute_body_hash
from diffguard.languages._utils import node_text


def get_language() -> tree_sitter.Language:
    """Return the tree-sitter Language object for Python."""
    return tree_sitter.Language(tree_sitter_python.language())


def extract_symbols(tree: tree_sitter.Tree, source: bytes) -> list[Symbol]:
    """Extract symbols from a parsed Python tree."""
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
        if child.type == "class_definition":
            _extract_class(child, source, symbols)
        elif child.type == "function_definition":
            _extract_function(child, source, symbols, parent_class)
        elif child.type == "decorated_definition":
            _extract_decorated(child, source, symbols, parent_class)


def _extract_class(
    node: tree_sitter.Node,
    source: bytes,
    symbols: list[Symbol],
) -> None:
    """Extract a class definition and its methods."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    class_name = node_text(name_node)
    signature = _build_class_signature(node)
    body_node = node.child_by_field_name("body")
    body_text = node_text(body_node) if body_node else ""

    symbols.append(
        Symbol(
            name=class_name,
            kind="class",
            signature=signature,
            start_line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
            body_hash=compute_body_hash(body_text),
            parent=None,
        )
    )

    if body_node:
        _walk_node(body_node, source, symbols, parent_class=class_name)


def _extract_function(
    node: tree_sitter.Node,
    source: bytes,
    symbols: list[Symbol],
    parent_class: str | None,
) -> None:
    """Extract a function/method definition."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    func_name = node_text(name_node)
    signature = _build_function_signature(node, decorators=None)
    body_node = node.child_by_field_name("body")
    body_text = node_text(body_node) if body_node else ""
    kind = "method" if parent_class else "function"

    symbols.append(
        Symbol(
            name=func_name,
            kind=kind,
            signature=signature,
            start_line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
            body_hash=compute_body_hash(body_text),
            parent=parent_class,
        )
    )

    if body_node:
        _walk_node(body_node, source, symbols, parent_class=parent_class)


def _extract_decorated(
    node: tree_sitter.Node,
    source: bytes,
    symbols: list[Symbol],
    parent_class: str | None,
) -> None:
    """Extract a decorated definition (function or class)."""
    decorators: list[str] = []
    definition: tree_sitter.Node | None = None
    for child in node.children:
        if child.type == "decorator":
            decorators.append(node_text(child))
        elif child.type in ("function_definition", "class_definition"):
            definition = child

    if definition is None:
        return

    if definition.type == "class_definition":
        name_node = definition.child_by_field_name("name")
        if name_node is None:
            return
        class_name = node_text(name_node)
        class_sig = _build_class_signature(definition)
        decorator_prefix = "\n".join(decorators) + "\n" if decorators else ""
        signature = decorator_prefix + class_sig
        body_node = definition.child_by_field_name("body")
        body_text = node_text(body_node) if body_node else ""

        symbols.append(
            Symbol(
                name=class_name,
                kind="class",
                signature=signature,
                start_line=node.start_point.row + 1,
                end_line=node.end_point.row + 1,
                body_hash=compute_body_hash(body_text),
                parent=None,
            )
        )
        if body_node:
            _walk_node(body_node, source, symbols, parent_class=class_name)
    else:
        name_node = definition.child_by_field_name("name")
        if name_node is None:
            return
        func_name = node_text(name_node)
        signature = _build_function_signature(definition, decorators=decorators)
        body_node = definition.child_by_field_name("body")
        body_text = node_text(body_node) if body_node else ""
        kind = "method" if parent_class else "function"

        symbols.append(
            Symbol(
                name=func_name,
                kind=kind,
                signature=signature,
                start_line=node.start_point.row + 1,
                end_line=node.end_point.row + 1,
                body_hash=compute_body_hash(body_text),
                parent=parent_class,
            )
        )
        if body_node:
            _walk_node(body_node, source, symbols, parent_class=parent_class)


def _build_function_signature(
    node: tree_sitter.Node,
    decorators: list[str] | None,
) -> str:
    """Build the function signature string."""
    name_node = node.child_by_field_name("name")
    params_node = node.child_by_field_name("parameters")
    return_type = node.child_by_field_name("return_type")

    if name_node is None or params_node is None:
        text = node_text(node)
        sig = text.split(":")[0] if ":" in text else text.split("\n")[0]
    else:
        sig = f"def {node_text(name_node)}{node_text(params_node)}"
        if return_type:
            sig += f" -> {node_text(return_type)}"

    if decorators:
        sig = "\n".join(decorators) + "\n" + sig

    return sig


def _build_class_signature(node: tree_sitter.Node) -> str:
    """Build the class signature string."""
    name_node = node.child_by_field_name("name")
    superclasses = node.child_by_field_name("superclasses")

    if name_node is None:
        return node_text(node).split(":")[0]

    sig = f"class {node_text(name_node)}"
    if superclasses:
        sig += node_text(superclasses)

    return sig
