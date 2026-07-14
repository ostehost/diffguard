"""Dependency reference scanning — find files that reference changed symbols.

Git access is delegated entirely to :mod:`diffguard.git`; this module owns the
tree-sitter scanning that confirms textual matches are real references.
"""

from __future__ import annotations

import tree_sitter

from diffguard.engine._types import Reference, ReferenceScan, RefContext
from diffguard.git import (
    get_file_at_snapshot,
    grep_files,
    list_files_at_snapshot_with_status,
)
from diffguard.languages import detect_language, get_parser

# File globs the git-grep pre-filter restricts to (the supported languages).
_GREP_GLOBS = ("*.py", "*.ts", "*.js", "*.go", "*.tsx", "*.jsx")

# Identifier node types per language
_IDENTIFIER_TYPES: dict[str, set[str]] = {
    "python": {"identifier"},
    "typescript": {
        "identifier",
        "private_property_identifier",
        "property_identifier",
        "shorthand_property_identifier",
        "type_identifier",
    },
    "javascript": {
        "identifier",
        "private_property_identifier",
        "property_identifier",
        "shorthand_property_identifier",
    },
    "go": {"identifier", "field_identifier", "type_identifier"},
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


def _unreadable_candidate_warning(file_path: str, ref: str) -> str:
    """Return the stable analysis-gap warning for an unreadable grep candidate."""
    return (
        f"{file_path}: reference candidate at snapshot {ref} is unreadable — "
        "reference analysis incomplete"
    )


def _unavailable_listing_warning(ref: str) -> str:
    """Return the stable analysis-gap warning for a failed fallback listing."""
    return f"reference file listing unavailable at snapshot {ref} — reference analysis incomplete"


def _is_import_context(node: tree_sitter.Node) -> bool:
    """Check if a node is in an import context by walking parents."""
    current = node.parent
    while current is not None:
        if current.type in _IMPORT_PARENT_TYPES:
            return True
        current = current.parent
    return False


_CALL_TYPES = {"call", "call_expression"}
_CALLABLE_EXPRESSION_TYPES = {"attribute", "member_expression", "selector_expression"}
_CALLABLE_MEMBER_FIELDS = {
    "attribute": "attribute",
    "member_expression": "property",
    "selector_expression": "field",
}
_SINGLE_CALL_OPERAND_WRAPPERS: set[str] = {
    "non_null_expression",
    "parenthesized_expression",
}
_LEADING_CALL_OPERAND_WRAPPERS: set[str] = {
    "as_expression",
    "satisfies_expression",
}
_TRAILING_CALL_OPERAND_WRAPPERS: set[str] = {"type_assertion"}
_NAMED_DECLARATION_TYPES = {
    "abstract_class_declaration",
    "abstract_method_signature",
    "class_declaration",
    "class_definition",
    "const_spec",
    "enum_assignment",
    "enum_body",
    "enum_declaration",
    "field_declaration",
    "field_definition",
    "function_declaration",
    "function_definition",
    "function_signature",
    "generator_function_declaration",
    "interface_declaration",
    "internal_module",
    "method_elem",
    "method_declaration",
    "method_definition",
    "method_signature",
    "property_signature",
    "public_field_definition",
    "type_alias_declaration",
    "type_parameter",
    "type_parameter_declaration",
    "type_spec",
    "var_spec",
}
_BINDING_FIELDS: dict[str, set[str | None]] = {
    "aliased_import": {"alias"},
    "catch_clause": {"parameter"},
    "as_pattern": {"alias"},
    "assignment": {"left"},
    "assignment_expression": {"left"},
    "assignment_statement": {"left"},
    "for_in_clause": {"left"},
    "for_in_statement": {"left"},
    "for_statement": {"left"},
    "field_definition": {"property"},
    "import_specifier": {"alias"},
    "keyword_argument": {"name"},
    "named_expression": {"name"},
    "parameter_declaration": {"name"},
    "range_clause": {"left"},
    "required_parameter": {"pattern"},
    "optional_parameter": {"pattern"},
    "short_var_declaration": {"left"},
    "variable_declarator": {"name"},
    "variadic_parameter_declaration": {"name"},
}
_PARAMETER_LIST_TYPES = {
    "formal_parameters",
    "lambda_parameters",
    "parameter_list",
    "parameters",
}
_BINDING_PATTERN_TYPES = {
    "array_pattern",
    "as_pattern_target",
    "assignment_pattern",
    "default_parameter",
    "dictionary_splat_pattern",
    "expression_list",
    "list_pattern",
    "list_splat_pattern",
    "literal_element",
    "object_assignment_pattern",
    "object_pattern",
    "pair_pattern",
    # Python uses ``pattern_list`` for comma-separated targets without outer
    # parentheses, including assignment, ``for``, and comprehension targets.
    # Treat it as a binding container so the walk can reach the owning node's
    # ``left`` field; expressions on the value/iterable side never cross it.
    "pattern_list",
    "rest_pattern",
    "tuple_pattern",
    "typed_default_parameter",
    "typed_parameter",
}


def _field_name(node: tree_sitter.Node) -> str | None:
    """Return *node*'s field name in its parent, if it has one."""
    parent = node.parent
    if parent is None:
        return None
    for index, child in enumerate(parent.children):
        if child == node:
            return parent.field_name_for_child(index)
    return None


def _has_ancestor_type(node: tree_sitter.Node, node_type: str) -> bool:
    """Return whether *node* is contained by an ancestor of *node_type*."""
    current = node.parent
    while current is not None:
        if current.type == node_type:
            return True
        current = current.parent
    return False


def _is_match_pattern_binding(node: tree_sitter.Node) -> bool:
    """Identify Python capture bindings without hiding value-pattern references.

    Tree-sitter represents both a bare capture (``case captured``) and a value
    pattern (``case Color.RED``) as ``dotted_name`` nodes.  Their topology is
    distinct: a capture is a single-component dotted name, while a value
    pattern contains a dot.  The dotted name directly owned by ``class_pattern``
    is also a reference even when it has only one component (``case Point()``).
    Alias, splat, and class-keyword label identifiers are separate syntax roles
    and are bindings/labels rather than references.
    """
    current = node
    while current.parent is not None:
        parent = current.parent

        if parent.type == "dotted_name":
            if not _has_ancestor_type(parent, "case_pattern"):
                return False
            if any(child.type == "." for child in parent.children):
                return False
            return parent.parent is None or parent.parent.type != "class_pattern"

        if parent.type in {"as_pattern", "splat_pattern"} and current == node:
            return _has_ancestor_type(parent, "case_pattern")

        # In ``case Point(field=value)``, ``field`` names the class attribute;
        # it neither binds a local nor references a changed symbol.  The value
        # side reaches its own dotted/class-pattern node before this branch.
        if parent.type == "keyword_pattern" and current == node:
            return _has_ancestor_type(parent, "case_pattern")

        if parent.type == "case_clause":
            return False
        current = parent
    return False


_GO_EVALUATED_KEY_TYPE_NODES = {
    "array_type",
    "implicit_length_array_type",
    "map_type",
    "slice_type",
}


def _go_key_is_struct_field_label(keyed_element: tree_sitter.Node) -> bool:
    """Distinguish Go field labels from evaluated composite-literal keys.

    Go represents both ``map[K]V{key: value}`` and ``Record{field: value}``
    with ``keyed_element`` and ``literal_element`` nodes.  The containing
    composite type is the available syntactic discriminator: explicit map,
    array, and slice keys are expressions, while struct and named composite
    keys retain the conservative field-label treatment.  Elided literals also
    retain that treatment because their element type is not present locally.
    """
    literal_value = keyed_element.parent
    if literal_value is None or literal_value.type != "literal_value":
        return False

    composite_literal = literal_value.parent
    if composite_literal is None or composite_literal.type != "composite_literal":
        return True

    type_node = composite_literal.child_by_field_name("type")
    return type_node is None or type_node.type not in _GO_EVALUATED_KEY_TYPE_NODES


def _is_syntax_only_label(
    parent: tree_sitter.Node,
    field: str | None,
    language: str,
) -> bool:
    """Return whether an AST key role names a field rather than an expression."""
    if field != "key":
        return False
    if language in {"javascript", "typescript"} and parent.type == "pair":
        return True
    if language == "go" and parent.type == "keyed_element":
        return _go_key_is_struct_field_label(parent)
    return False


def _transparent_call_operand(wrapper: tree_sitter.Node) -> tree_sitter.Node | None:
    """Return the runtime expression carried by a transparent call wrapper."""
    single_operand = wrapper.type in _SINGLE_CALL_OPERAND_WRAPPERS
    leading_operand = wrapper.type in _LEADING_CALL_OPERAND_WRAPPERS
    trailing_operand = wrapper.type in _TRAILING_CALL_OPERAND_WRAPPERS
    if not single_operand and not leading_operand and not trailing_operand:
        return None
    named_children = [child for child in wrapper.named_children if child.type != "comment"]
    if not named_children:
        return None
    if single_operand:
        return named_children[0] if len(named_children) == 1 else None
    if leading_operand:
        return named_children[0]
    return named_children[-1]


def _is_call_context(node: tree_sitter.Node) -> bool:
    """Return whether the identifier is within the callable side of a call AST."""
    current = node
    parent = current.parent
    while parent is not None:
        if parent.type in _CALL_TYPES:
            return parent.child_by_field_name("function") == current
        transparent_operand = _transparent_call_operand(parent)
        if transparent_operand is not None:
            if transparent_operand != current:
                return False
            current = parent
            parent = current.parent
            continue
        if parent.type not in _CALLABLE_EXPRESSION_TYPES:
            return False
        # A receiver is referenced to resolve a member call, but is not itself
        # the called symbol.  Only the selected member may inherit call context.
        if _field_name(current) != _CALLABLE_MEMBER_FIELDS[parent.type]:
            return False
        current = parent
        parent = current.parent
    return False


def _is_declaration_context(node: tree_sitter.Node, language: str) -> bool:
    """Exclude declarations, binding targets, and syntax-only labels."""
    if _is_match_pattern_binding(node):
        return True

    current = node
    while current.parent is not None:
        parent = current.parent
        field = _field_name(current)

        if parent.type in _NAMED_DECLARATION_TYPES and field == "name":
            return True
        if _is_syntax_only_label(parent, field, language):
            return True
        if field in _BINDING_FIELDS.get(parent.type, set()):
            return True

        # Python's bare parameters have no field name. Typed/default and
        # destructured parameters reach the list through one of the binding
        # pattern nodes below. Default values and annotations encounter a
        # non-pattern expression first and therefore remain references.
        if parent.type in _PARAMETER_LIST_TYPES:
            return current.type == "identifier" or current.type in _BINDING_PATTERN_TYPES

        if parent.type not in _BINDING_PATTERN_TYPES:
            return False
        if parent.type in {"assignment_pattern", "object_assignment_pattern"} and field == "right":
            # JavaScript/TypeScript destructuring defaults evaluate the right-hand
            # expression; only the left-hand side introduces a binding.
            return False
        if field in {"type", "value"} and parent.type in {
            "default_parameter",
            "typed_default_parameter",
            "typed_parameter",
        }:
            return False
        current = current.parent
    return False


def _scan_file_for_symbols(
    source: str,
    language: str,
    symbol_names: set[str],
    *,
    file_path: str | None = None,
) -> list[tuple[str, int, RefContext, str]]:
    """Scan a file for references to symbol names."""
    hits, _ = _scan_file_for_symbols_with_status(
        source,
        language,
        symbol_names,
        file_path=file_path,
    )
    return hits


def _scan_file_for_symbols_with_status(
    source: str,
    language: str,
    symbol_names: set[str],
    *,
    file_path: str | None = None,
) -> tuple[list[tuple[str, int, RefContext, str]], bool]:
    """Scan a file and retain tree-sitter's parse-gap status.

    Returns hits plus whether tree-sitter reported a parse gap.
    """
    parser = get_parser(language, file_path=file_path)
    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)
    source_lines = source.splitlines()

    id_types = _IDENTIFIER_TYPES.get(language, {"identifier"})
    results: list[tuple[str, int, RefContext, str]] = []

    def _walk(node: tree_sitter.Node) -> None:
        if node.type in id_types:
            name = source_bytes[node.start_byte : node.end_byte].decode("utf-8")
            if name in symbol_names and not _is_declaration_context(node, language):
                line = node.start_point.row + 1
                if _is_import_context(node):
                    ctx: RefContext = "import"
                elif _is_call_context(node):
                    ctx = "call"
                else:
                    ctx = "reference"
                src_line = source_lines[line - 1].strip() if line <= len(source_lines) else ""
                results.append((name, line, ctx, src_line))
        for child in node.children:
            _walk(child)

    _walk(tree.root_node)
    return results, tree.root_node.has_error


def _candidate_files(symbols: set[str], ref: str, repo_path: str) -> set[str] | None:
    """Pre-filter to files that textually contain any symbol name (git grep).

    An empty set is a definitive no-match result. ``None`` means candidate
    discovery was unavailable and signals the caller to scan all files.
    """
    candidates: set[str] = set()
    for symbol in symbols:
        hits = grep_files(symbol, ref, repo_path, _GREP_GLOBS)
        if hits is None:  # git grep unavailable -> scan all
            return None
        candidates.update(hits)
    return candidates


def scan_references(
    repo_path: str,
    changed_symbols: list[str],
    ref: str,
    changed_files: set[str] | None = None,
) -> ReferenceScan:
    """Find syntactic references to changed names in a repository snapshot.

    Uses git grep as a pre-filter to avoid scanning all files with tree-sitter.

    Args:
        repo_path: Path to the git repository.
        changed_symbols: List of symbol names to search for.
        ref: Git ref to scan files at (e.g. HEAD or the "after" ref).
    Returns:
        References and parse-gap warnings. ``changed_files`` is accepted for
        call compatibility but deliberately not excluded: declaration AST
        nodes, rather than whole changed files, are filtered.
        Changed files are deliberately
        included; declaration nodes are excluded by AST context instead.
    """
    if not changed_symbols:
        return ReferenceScan()

    symbol_names = set(changed_symbols)

    # Pre-filter with git grep
    candidate_files = _candidate_files(symbol_names, ref, repo_path)
    warnings: list[str] = []
    if candidate_files is None:
        # Fallback: scan all files
        files_to_scan, listing_complete = list_files_at_snapshot_with_status(ref, repo_path)
        if not listing_complete:
            warnings.append(_unavailable_listing_warning(ref))
    else:
        files_to_scan = sorted(candidate_files)

    references: list[Reference] = []

    for file_path in files_to_scan:
        language = detect_language(file_path)
        if language is None:
            continue

        source = get_file_at_snapshot(ref, file_path, repo_path=repo_path)
        if source is None:
            warnings.append(_unreadable_candidate_warning(file_path, ref))
            continue

        try:
            hits, parse_error = _scan_file_for_symbols_with_status(
                source,
                language,
                symbol_names,
                file_path=file_path,
            )
        except UnicodeEncodeError:
            # Commit/index reads use surrogateescape so invalid UTF-8 can reach
            # this layer as lone surrogates rather than ``None``. Treat that as
            # the same unreadable candidate gap instead of crashing or emitting
            # a fabricated syntactic reference.
            warnings.append(_unreadable_candidate_warning(file_path, ref))
            continue
        if parse_error:
            warnings.append(f"{file_path}: reference scan has a parse gap")
            continue
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
    return ReferenceScan(references=references, warnings=sorted(set(warnings)))


def find_references(
    repo_path: str,
    changed_symbols: list[str],
    ref: str,
    changed_files: set[str] | None = None,
) -> list[Reference]:
    """Compatibility wrapper returning only syntactic references.

    This legacy surface intentionally discards completeness warnings. New
    callers that need to distinguish a complete scan from partial evidence
    must use :func:`scan_references` and inspect ``ReferenceScan.warnings``.
    """
    return scan_references(repo_path, changed_symbols, ref, changed_files).references
