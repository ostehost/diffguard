"""Bounded signature comparison for contract-change evidence."""

from __future__ import annotations

import ast
import re
import tokenize
from dataclasses import dataclass, field
from io import StringIO
from typing import Literal

from diffguard.engine._types import Confidence, SignatureAssessment, SignatureComparison

PythonParameterKind = Literal[
    "positional_only",
    "positional_or_keyword",
    "var_positional",
    "keyword_only",
    "var_keyword",
]


@dataclass(frozen=True)
class _PythonParameter:
    """One Python parameter with its call-shape kind preserved."""

    name: str
    kind: PythonParameterKind
    default: str | None
    annotation: str | None

    @property
    def required(self) -> bool:
        return self.kind not in {"var_positional", "var_keyword"} and self.default is None


@dataclass(frozen=True)
class _PythonSignature:
    """Python syntax needed by the bounded compatibility rules."""

    parameters: tuple[_PythonParameter, ...]
    return_annotation: str | None
    decorators: tuple[str, ...]
    type_parameters: tuple[str, ...]


@dataclass(frozen=True)
class _PythonDeclaration:
    """Host-parseable declaration plus host-independent PEP 695 syntax."""

    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
    type_parameters: tuple[str, ...]


_NON_PYTHON_NORMALIZED_LANGUAGES = {"go", "javascript", "typescript"}
_JAVASCRIPT_LANGUAGES = {"javascript", "typescript"}
_JAVASCRIPT_LINE_TERMINATORS = {"\n", "\r", "\u2028", "\u2029"}
_RESTRICTED_LINE_TERMINATOR_KEYWORDS = {"break", "continue", "return", "throw"}
_RESTRICTED_LINE_TERMINATOR_TOKEN = "<LINE_TERMINATOR>"
_MULTI_CHARACTER_SIGNATURE_TOKENS = tuple(
    sorted(
        {
            "!==",
            "&&=",
            "**=",
            "...",
            "===",
            "??=",
            "||=",
            "!=",
            "%=",
            "&&",
            "&^",
            "&^=",
            "&=",
            "**",
            "*=",
            "++",
            "+=",
            "--",
            "-=",
            "->",
            "/=",
            "::",
            ":=",
            "<=",
            "<-",
            "==",
            "=>",
            ">=",
            "?.",
            "??",
            "^=",
            "|=",
            "||",
        },
        key=len,
        reverse=True,
    )
)
_ALWAYS_EXPRESSION_PREFIX_KEYWORDS = {
    "case",
    "class",
    "const",
    "delete",
    "extends",
    "function",
    "in",
    "instanceof",
    "new",
    "return",
    "throw",
    "typeof",
    "var",
    "void",
}
_CONTROL_HEADER_KEYWORDS = {"catch", "for", "if", "switch", "while", "with"}
_BLOCK_PREFIX_KEYWORDS = {"do", "else", "finally", "try"}


def _is_identifier_start(char: str) -> bool:
    """Return whether *char* can conservatively start a supported identifier."""
    return char in {"$", "_"} or char.isalpha() or char.isidentifier()


def _is_identifier_continue(char: str) -> bool:
    """Return whether *char* can conservatively continue a supported identifier."""
    return char == "$" or f"a{char}".isidentifier()


def _identifier_token_end(value: str, start: int) -> int:
    """Return the exclusive end of an identifier token."""
    end = start + 1
    while end < len(value) and _is_identifier_continue(value[end]):
        end += 1
    return end


def _number_token_end(value: str, start: int) -> int:
    """Return the conservative exclusive end of a numeric token."""
    end = start + 1
    while end < len(value):
        candidate = value[end]
        if candidate.isalnum() or candidate in {"_", "."}:
            end += 1
            continue
        if candidate in {"+", "-"} and value[end - 1] in {"E", "P", "e", "p"}:
            end += 1
            continue
        break
    return end


def _compound_signature_token(value: str, start: int) -> str | None:
    """Return the longest compound operator token at *start*, if any."""
    return next(
        (token for token in _MULTI_CHARACTER_SIGNATURE_TOKENS if value.startswith(token, start)),
        None,
    )


@dataclass
class _ParenFrame:
    """Lexical role retained until a closing parenthesis."""

    role: Literal["control", "expression", "for_control", "function_parameters"]
    async_function: bool = False
    generator_function: bool = False
    function_expression: bool = True
    for_phase: Literal["start", "lhs", "rhs", "c_style"] | None = None


@dataclass(frozen=True)
class _BraceFrame:
    """Lexical role retained until a closing brace."""

    role: Literal["block", "function_declaration", "function_expression", "object"]
    async_function: bool = False
    generator_function: bool = False
    paren_depth: int = 0
    square_depth: int = 0


@dataclass
class _LexicalState:
    """Bounded JavaScript lexical-goal and delimiter state."""

    goal: Literal["statement", "operand", "operator"] = "statement"
    last_token: str | None = None
    pending_control: str | None = None
    pending_block: bool = False
    pending_function: tuple[bool, bool, bool] | None = None
    pending_function_body: tuple[bool, bool, bool] | None = None
    arrow_function_flags: tuple[bool, bool] | None = None
    async_arrow_candidate: bool = False
    async_started_statement: bool = False
    async_arrow_parameter_seen: bool = False
    restricted_line_terminator_pending: bool = False
    pending_method: tuple[bool, bool] | None = None
    method_name_seen: bool = False
    possible_label: bool = False
    generic_angle_depth: int = 0
    pending_arrow_body: bool = False
    concise_function_flags: tuple[bool, bool] | None = None
    concise_boundary_depth: int | None = None
    square_depth: int = 0
    parens: list[_ParenFrame] = field(default_factory=list)
    braces: list[_BraceFrame] = field(default_factory=list)

    @property
    def can_end_expression(self) -> bool:
        """Return whether a slash must be division in the current lexical goal."""
        return self.goal == "operator"

    def _active_function_flags(self) -> tuple[bool, bool]:
        if self.concise_function_flags is not None:
            return self.concise_function_flags
        if self.pending_function_body is not None:
            return self.pending_function_body[:2]
        for brace_frame in reversed(self.braces):
            if brace_frame.role in {"function_declaration", "function_expression"}:
                return brace_frame.async_function, brace_frame.generator_function
        return False, False

    def _at_object_member_boundary(self) -> bool:
        brace_frame = self.braces[-1] if self.braces else None
        return (
            brace_frame is not None
            and brace_frame.role == "object"
            and len(self.parens) == brace_frame.paren_depth
            and self.square_depth == brace_frame.square_depth
            and self.last_token in {"{", ","}
        )

    def _restricted_line_terminator_after(self, identifier: str) -> bool:
        """Return whether a newline after *identifier* changes JavaScript parsing."""
        if self.last_token in {".", "?."}:
            return False
        brace_frame = self.braces[-1] if self.braces else None
        at_object_member_level = bool(
            brace_frame is not None
            and brace_frame.role == "object"
            and len(self.parens) == brace_frame.paren_depth
            and self.square_depth == brace_frame.square_depth
        )
        if at_object_member_level:
            return False
        if identifier == "yield":
            return self._active_function_flags()[1]
        at_statement = self.goal == "statement" or bool(
            self.last_token == ":" and brace_frame is not None and brace_frame.role == "block"
        )
        return identifier in _RESTRICTED_LINE_TERMINATOR_KEYWORDS and at_statement

    def observe_line_terminator(self) -> None:
        """End lexical contexts guarded by JavaScript's no-line-terminator rules."""
        if self.restricted_line_terminator_pending:
            self.goal = "statement"
        if self.last_token == "async" and self.async_arrow_candidate:
            if self.async_started_statement:
                self.goal = "statement"
            if self.pending_method is not None and not self.method_name_seen:
                self.pending_method = None
                self.method_name_seen = False
        self.restricted_line_terminator_pending = False
        self.arrow_function_flags = None
        self.async_arrow_candidate = False
        self.async_started_statement = False
        self.async_arrow_parameter_seen = False

    def _delimiter_depth(self) -> int:
        return len(self.parens) + len(self.braces) + self.square_depth

    def _start_non_braced_arrow_body(self, token: str) -> None:
        if self.pending_arrow_body and token != "{":
            if self.pending_function_body is not None:
                self.concise_function_flags = self.pending_function_body[:2]
                self.concise_boundary_depth = self._delimiter_depth()
            self.pending_function_body = None
            self.pending_arrow_body = False

    def observe_identifier(self, identifier: str) -> None:
        """Update lexical goal after an identifier or contextual keyword."""
        restricted_line_terminator = self._restricted_line_terminator_after(identifier)
        self._start_non_braced_arrow_body(identifier)
        prior_goal = self.goal
        active_async, active_generator = self._active_function_flags()
        property_name = self.last_token in {".", "?."}
        for_frame = self.parens[-1] if self.parens else None
        in_for_header = bool(for_frame is not None and for_frame.role == "for_control")
        is_for_of = (
            identifier == "of"
            and for_frame is not None
            and for_frame.role == "for_control"
            and for_frame.for_phase == "lhs"
            and self.can_end_expression
        )
        starts_method = self._at_object_member_boundary() and not property_name
        continues_method = self.pending_method is not None and not self.method_name_seen

        self.pending_control = None
        self.pending_block = False
        self.possible_label = False

        if property_name:
            # Reserved and contextual words are ordinary property names after
            # member access.  In particular, ``obj.else / total`` is division,
            # not a statement-block prefix followed by a regular expression.
            self.goal = "operator"
        elif is_for_of:
            assert for_frame is not None
            for_frame.for_phase = "rhs"
            self.goal = "operand"
        elif identifier in _CONTROL_HEADER_KEYWORDS:
            self.pending_control = identifier
            if identifier == "catch":
                self.pending_block = True
            self.goal = "operand"
        elif identifier in _BLOCK_PREFIX_KEYWORDS:
            self.pending_block = True
            self.goal = "statement"
        elif identifier == "function":
            is_async = self.last_token == "async" and self.async_arrow_candidate
            declared_in_statement = (
                self.async_started_statement if is_async else prior_goal == "statement"
            )
            self.pending_function = (is_async, False, not declared_in_statement)
            self.async_arrow_candidate = False
            self.goal = "operand"
        elif identifier == "await" and active_async:
            self.goal = "operand"
        elif identifier == "yield" and active_generator:
            self.goal = "operand"
        elif identifier == "const" and self.last_token == "as":
            self.goal = "operator"
        elif identifier in _ALWAYS_EXPRESSION_PREFIX_KEYWORDS:
            self.goal = "operand"
        else:
            # Contextual words such as async/as/await/infer/keyof/let/readonly/
            # satisfies/yield remain ordinary identifiers outside the syntax
            # contexts above, so a following slash is division.
            self.goal = "operator"
            self.possible_label = prior_goal == "statement"

        if in_for_header and for_frame is not None and for_frame.for_phase == "start":
            if identifier not in {"const", "let", "var"}:
                for_frame.for_phase = "lhs"

        if starts_method:
            self.pending_method = (identifier == "async", False)
            self.method_name_seen = identifier not in {"async", "get", "set"}
        elif continues_method:
            self.method_name_seen = True
        elif self.pending_method is not None and identifier != "function":
            self.pending_method = None
            self.method_name_seen = False

        if identifier == "async" and not property_name:
            self.async_arrow_candidate = True
            self.async_started_statement = prior_goal == "statement"
            self.async_arrow_parameter_seen = False
        elif self.async_arrow_candidate and self.last_token == "async":
            self.async_arrow_parameter_seen = True
        elif identifier != "function":
            self.async_arrow_candidate = False
            self.async_arrow_parameter_seen = False
        self.arrow_function_flags = None
        self.restricted_line_terminator_pending = restricted_line_terminator
        self.last_token = identifier

    def observe_literal(self, token: str) -> None:
        """Update lexical goal after a string, template, regex, or number."""
        self._start_non_braced_arrow_body(token)
        self.goal = "operator"
        self.pending_control = None
        self.pending_block = False
        self.arrow_function_flags = None
        self.async_arrow_candidate = False
        self.async_arrow_parameter_seen = False
        self.restricted_line_terminator_pending = False
        self.pending_method = None
        self.method_name_seen = False
        self.possible_label = False
        self.last_token = token

    def observe_token(self, token: str) -> None:
        """Update delimiter roles and lexical goal after punctuation/operator syntax."""
        self.restricted_line_terminator_pending = False
        if token != "=>":
            self._start_non_braced_arrow_body(token)
        depth_before = self._delimiter_depth()
        prior_goal = self.goal
        if token != "(":
            self.pending_control = None
        if token == "(":
            if self.pending_control is not None:
                role: Literal["control", "expression", "for_control", "function_parameters"] = (
                    "for_control" if self.pending_control == "for" else "control"
                )
                paren_frame = _ParenFrame(
                    role,
                    for_phase="start" if role == "for_control" else None,
                )
                self.pending_control = None
            elif self.pending_function is not None:
                async_function, generator_function, function_expression = self.pending_function
                paren_frame = _ParenFrame(
                    "function_parameters",
                    async_function,
                    generator_function,
                    function_expression,
                )
                self.pending_function = None
            elif self.pending_method is not None and self.method_name_seen:
                async_function, generator_function = self.pending_method
                paren_frame = _ParenFrame(
                    "function_parameters",
                    async_function,
                    generator_function,
                    True,
                )
                self.pending_method = None
                self.method_name_seen = False
            else:
                paren_frame = _ParenFrame(
                    "expression",
                    async_function=self.async_arrow_candidate and self.last_token == "async",
                )
            self.parens.append(paren_frame)
            self.goal = "operand"
            self.pending_block = False
            self.arrow_function_flags = None
            self.async_arrow_candidate = False
            self.async_arrow_parameter_seen = False
            self.possible_label = False
        elif token == ")":
            paren_frame = self.parens.pop() if self.parens else _ParenFrame("expression")
            if paren_frame.role == "function_parameters":
                self.pending_function_body = (
                    paren_frame.async_function,
                    paren_frame.generator_function,
                    paren_frame.function_expression,
                )
                self.goal = "operand"
            elif paren_frame.role in {"control", "for_control"}:
                self.pending_block = True
                self.goal = "statement"
            else:
                self.goal = "operator"
                self.arrow_function_flags = (paren_frame.async_function, False)
        elif token == "{":
            if self.pending_function_body is not None:
                async_function, generator_function, function_expression = self.pending_function_body
                self.braces.append(
                    _BraceFrame(
                        "function_expression" if function_expression else "function_declaration",
                        async_function,
                        generator_function,
                    )
                )
                self.pending_function_body = None
                self.pending_arrow_body = False
                self.goal = "statement"
            elif self.pending_block or self.goal == "statement":
                self.braces.append(
                    _BraceFrame(
                        "block",
                        paren_depth=len(self.parens),
                        square_depth=self.square_depth,
                    )
                )
                self.goal = "statement"
            else:
                self.braces.append(
                    _BraceFrame(
                        "object",
                        paren_depth=len(self.parens),
                        square_depth=self.square_depth,
                    )
                )
                self.goal = "operand"
            self.pending_block = False
            self.pending_method = None
            self.method_name_seen = False
        elif token == "}":
            brace_frame = self.braces.pop() if self.braces else _BraceFrame("object")
            self.goal = (
                "operator" if brace_frame.role in {"function_expression", "object"} else "statement"
            )
            self.pending_block = False
        elif token == "*" and self.pending_function is not None:
            async_function, _, function_expression = self.pending_function
            self.pending_function = (async_function, True, function_expression)
            self.goal = "operand"
            self.pending_block = False
        elif token == "*" and (
            self._at_object_member_boundary()
            or (self.pending_method is not None and not self.method_name_seen)
        ):
            async_function = self.pending_method[0] if self.pending_method is not None else False
            self.pending_method = (async_function, True)
            self.method_name_seen = False
            self.goal = "operand"
        elif token == "=>":
            async_function, generator_function = self.arrow_function_flags or (
                self.async_arrow_candidate and self.async_arrow_parameter_seen,
                False,
            )
            self.pending_function_body = (async_function, generator_function, True)
            self.pending_arrow_body = True
            self.goal = "operand"
            self.async_arrow_candidate = False
            self.async_arrow_parameter_seen = False
        elif token in {"++", "--"}:
            self.goal = prior_goal
            self.pending_block = False
        elif token == "[":
            self.square_depth += 1
            self.goal = "operand"
            self.pending_block = False
        elif token == "]":
            self.square_depth = max(0, self.square_depth - 1)
            self.goal = "operator"
            self.pending_block = False
        elif token == "!" and prior_goal == "operator":
            self.goal = "operator"
            self.pending_block = False
        elif token == ">" and self.generic_angle_depth:
            self.generic_angle_depth -= 1
            self.goal = "operator" if self.generic_angle_depth == 0 else "operand"
        elif token == ":" and self.possible_label:
            self.goal = "statement"
            self.pending_block = True
            self.possible_label = False
        elif token == ";":
            in_for_header = bool(self.parens and self.parens[-1].role == "for_control")
            self.goal = "operand" if in_for_header else "statement"
            self.pending_block = False
        else:
            self.goal = "operand"
            self.pending_block = False
        if self.parens and self.parens[-1].role == "for_control":
            for_frame = self.parens[-1]
            if token == ";":
                for_frame.for_phase = "c_style"
            elif token in {"=", ",", "+", "-", "*", "/", "/=", "%"}:
                if for_frame.for_phase in {"start", "lhs"}:
                    for_frame.for_phase = "c_style"
        if token != ")" and token != "=>":
            self.arrow_function_flags = None
        if token not in {"*", "("}:
            self.pending_method = None
            self.method_name_seen = False
        if token not in {")", "=>"}:
            self.async_arrow_candidate = False
            self.async_arrow_parameter_seen = False
        if token != ":":
            self.possible_label = False
        if (
            token in {",", ";"}
            and self.concise_boundary_depth is not None
            and depth_before == self.concise_boundary_depth
        ):
            self.concise_function_flags = None
            self.concise_boundary_depth = None
        self.last_token = token

    def observe_generic_angle_open(self) -> None:
        """Record a proven TypeScript generic-instantiation delimiter."""
        self.generic_angle_depth += 1
        self.goal = "operand"
        self.last_token = "<"


@dataclass(frozen=True)
class _LexicalToken:
    """One non-Python signature token with source bounds."""

    text: str
    start: int
    end: int
    opaque: bool = False
    normalization_ignored: bool = False
    restricted_line_terminator_after: bool = False
    restricted_line_terminator_before: bool = False
    async_line_terminator_candidate: bool = False
    async_method_candidate: bool = False


def _contains_javascript_line_terminator(value: str) -> bool:
    """Return whether *value* contains an ECMAScript line terminator."""
    return any(char in _JAVASCRIPT_LINE_TERMINATORS for char in value)


def _simple_quoted_token_end(value: str, start: int, *, raw_backtick: bool) -> int:
    """Return the exclusive end of a simple quoted string token."""
    quote = value[start]
    escaped = False
    for index in range(start + 1, len(value)):
        char = value[index]
        if raw_backtick:
            if char == quote:
                return index + 1
            continue
        if char == quote and not escaped:
            return index + 1
        escaped = char == "\\" and not escaped
        if char != "\\":
            escaped = False
    return len(value)


def _template_expression_end(value: str, start: int) -> int:
    """Return the position after a template interpolation's closing brace."""
    depth = 1
    index = start
    state = _LexicalState(goal="operand")
    while index < len(value):
        if value[index].isspace():
            if value[index] in _JAVASCRIPT_LINE_TERMINATORS:
                state.observe_line_terminator()
            index += 1
            continue
        if value[index] == "}" and depth == 1:
            return index + 1
        token = _next_non_python_token(value, index, "typescript", state)
        if not token.opaque:
            if token.text == "{":
                depth += 1
            elif token.text == "}":
                depth -= 1
        index = token.end
    return len(value)


def _template_token_end(value: str, start: int) -> int:
    """Return the exclusive end of a JavaScript template, including nesting."""
    index = start + 1
    escaped = False
    while index < len(value):
        char = value[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if char == "\\":
            escaped = True
            index += 1
            continue
        if char == "`":
            return index + 1
        if char == "$" and index + 1 < len(value) and value[index + 1] == "{":
            index = _template_expression_end(value, index + 2)
            continue
        index += 1
    return len(value)


def _quoted_token_end(value: str, start: int, language: str) -> int:
    """Return the exclusive end of a string, raw string, or template token."""
    quote = value[start]
    if quote == "`" and language != "go":
        return _template_token_end(value, start)
    return _simple_quoted_token_end(
        value,
        start,
        raw_backtick=quote == "`" and language == "go",
    )


def _regex_token_end(value: str, start: int) -> int | None:
    """Return the exclusive end of a JavaScript regex literal when recognizable."""
    escaped = False
    in_character_class = False
    for index in range(start + 1, len(value)):
        char = value[index]
        if char in _JAVASCRIPT_LINE_TERMINATORS and not escaped:
            return None
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "[":
            in_character_class = True
            continue
        if char == "]" and in_character_class:
            in_character_class = False
            continue
        if char != "/" or in_character_class:
            continue
        end = index + 1
        while end < len(value) and _is_identifier_continue(value[end]):
            end += 1
        return end
    return None


def _is_generic_instantiation_before_division(value: str, start: int) -> bool:
    """Recognize a bounded TypeScript instantiation followed by division."""
    prefix = value[:start].rstrip()
    if not prefix or not (_is_identifier_continue(prefix[-1]) or prefix[-1] in ")]>"):
        return False
    close = _matching_angle_close(value, start)
    if close is None:
        return False
    return _is_bounded_division_chain(value, close + 1)


def _is_bounded_division_chain(value: str, start: int) -> bool:
    """Return whether a suffix is an unambiguous simple division chain."""
    index = start
    operand_count = 0
    while True:
        while index < len(value) and value[index].isspace():
            index += 1
        if index >= len(value) or value[index] != "/" or value.startswith(("//", "/*"), index):
            return False
        index += 1

        while index < len(value) and value[index].isspace():
            index += 1
        if index >= len(value):
            return False
        if _is_identifier_start(value[index]):
            index = _identifier_token_end(value, index)
        elif value[index].isdigit() or (
            value[index] == "." and index + 1 < len(value) and value[index + 1].isdigit()
        ):
            index = _number_token_end(value, index)
        else:
            return False
        operand_count += 1

        while index < len(value) and value[index].isspace():
            index += 1
        if index < len(value) and value[index] == "/":
            continue
        return operand_count >= 2 and (
            index == len(value) or value[index] in {",", ")", "]", "}", ":", ";", "?"}
        )


def _next_non_python_token(
    value: str,
    start: int,
    language: str,
    state: _LexicalState,
) -> _LexicalToken:
    """Read one non-whitespace token and advance the bounded lexical state."""
    if value.startswith("//", start):
        line_terminators = (
            _JAVASCRIPT_LINE_TERMINATORS if language in _JAVASCRIPT_LANGUAGES else {"\n"}
        )
        end = next(
            (index for index in range(start + 2, len(value)) if value[index] in line_terminators),
            len(value),
        )
        return _LexicalToken(
            value[start:end],
            start,
            end,
            opaque=True,
            normalization_ignored=True,
        )
    if value.startswith("/*", start):
        close = value.find("*/", start + 2)
        end = len(value) if close == -1 else close + 2
        comment = value[start:end]
        if language in _JAVASCRIPT_LANGUAGES and _contains_javascript_line_terminator(comment):
            state.observe_line_terminator()
        return _LexicalToken(
            comment,
            start,
            end,
            opaque=True,
            normalization_ignored=True,
        )

    char = value[start]
    if char in {'"', "'", "`"}:
        end = _quoted_token_end(value, start, language)
        token = value[start:end]
        state.observe_literal(token)
        return _LexicalToken(token, start, end, opaque=True)

    if char == "/":
        if language in {"javascript", "typescript"} and not state.can_end_expression:
            regex_end = _regex_token_end(value, start)
            if regex_end is not None:
                token = value[start:regex_end]
                state.observe_literal(token)
                return _LexicalToken(token, start, regex_end, opaque=True)
        operator = "/=" if value.startswith("/=", start) else "/"
        state.observe_token(operator)
        return _LexicalToken(operator, start, start + len(operator))

    if _is_identifier_start(char):
        end = _identifier_token_end(value, start)
        identifier = value[start:end]
        async_candidate = identifier == "async" and state.last_token not in {".", "?."}
        async_method_candidate = async_candidate and state._at_object_member_boundary()
        state.observe_identifier(identifier)
        return _LexicalToken(
            identifier,
            start,
            end,
            restricted_line_terminator_after=state.restricted_line_terminator_pending,
            async_line_terminator_candidate=async_candidate,
            async_method_candidate=async_method_candidate,
        )

    if char.isdigit() or (char == "." and start + 1 < len(value) and value[start + 1].isdigit()):
        end = _number_token_end(value, start)
        token = value[start:end]
        state.observe_literal(token)
        return _LexicalToken(token, start, end)

    if (
        language == "typescript"
        and char == "<"
        and (state.generic_angle_depth or _is_generic_instantiation_before_division(value, start))
    ):
        state.observe_generic_angle_open()
        return _LexicalToken(char, start, start + 1)

    # Keep repeated generic closers/openers as individual tokens. In type
    # contexts ``>>`` may be two nested generic delimiters, while in default
    # expressions it is a shift; splitting remains conservative because a real
    # operator change still changes the resulting token sequence.
    if char in {"<", ">"} and (
        (start > 0 and value[start - 1] == char)
        or (start + 1 < len(value) and value[start + 1] == char)
    ):
        state.observe_token(char)
        return _LexicalToken(char, start, start + 1)

    compound = _compound_signature_token(value, start)
    if compound is not None:
        restricted_line_terminator_before = compound == "=>" or (
            compound in {"++", "--"} and state.can_end_expression
        )
        state.observe_token(compound)
        return _LexicalToken(
            compound,
            start,
            start + len(compound),
            restricted_line_terminator_before=restricted_line_terminator_before,
        )

    state.observe_token(char)
    return _LexicalToken(char, start, start + 1)


def _non_python_lexical_tokens(signature: str, language: str) -> list[_LexicalToken]:
    """Return source-bounded non-Python tokens with opaque literals preserved."""
    tokens: list[_LexicalToken] = []
    state = _LexicalState()
    index = 0
    while index < len(signature):
        if signature[index].isspace():
            if (
                language in _JAVASCRIPT_LANGUAGES
                and signature[index] in _JAVASCRIPT_LINE_TERMINATORS
            ):
                state.observe_line_terminator()
            index += 1
            continue
        token = _next_non_python_token(signature, index, language, state)
        tokens.append(token)
        index = token.end
    return tokens


def _matching_lexical_delimiter(
    tokens: list[_LexicalToken],
    start: int,
    opening: str,
    closing: str,
) -> int | None:
    """Return the matching delimiter token index for a bounded token sequence."""
    depth = 0
    for index in range(start, len(tokens)):
        text = tokens[index].text
        if text == opening:
            depth += 1
        elif text == closing:
            depth -= 1
            if depth == 0:
                return index
    return None


def _is_identifier_lexical_token(token: _LexicalToken) -> bool:
    """Return whether *token* is one complete identifier spelling."""
    return (
        bool(token.text)
        and _is_identifier_start(token.text[0])
        and (_identifier_token_end(token.text, 0) == len(token.text))
    )


def _arrow_follows_parameters(tokens: list[_LexicalToken], close: int) -> bool:
    """Recognize an arrow after parameters and an optional TypeScript return type."""
    index = close + 1
    if index >= len(tokens):
        return False
    if tokens[index].text == "=>":
        return True
    if tokens[index].text != ":":
        return False

    depths = {"(": 0, "[": 0, "{": 0, "<": 0}
    closing_to_opening = {")": "(", "]": "[", "}": "{", ">": "<"}
    for token in tokens[index + 1 :]:
        text = token.text
        if text in depths:
            depths[text] += 1
            continue
        opening = closing_to_opening.get(text)
        if opening is not None:
            if depths[opening] == 0:
                return False
            depths[opening] -= 1
            continue
        if text == "=>" and not any(depths.values()):
            return True
        if text in {",", ";"} and not any(depths.values()):
            return False
    return False


def _starts_async_arrow(
    tokens: list[_LexicalToken],
    start: int,
    language: str,
) -> bool:
    """Return whether tokens at *start* form the binding portion of an async arrow."""
    token = tokens[start]
    if _is_identifier_lexical_token(token):
        return start + 1 < len(tokens) and tokens[start + 1].text == "=>"
    if token.text == "(":
        close = _matching_lexical_delimiter(tokens, start, "(", ")")
        return close is not None and _arrow_follows_parameters(tokens, close)
    if language != "typescript" or token.text != "<":
        return False
    generic_close = _matching_lexical_delimiter(tokens, start, "<", ">")
    if generic_close is None or generic_close + 1 >= len(tokens):
        return False
    parameters = generic_close + 1
    if tokens[parameters].text != "(":
        return False
    parameter_close = _matching_lexical_delimiter(tokens, parameters, "(", ")")
    return parameter_close is not None and _arrow_follows_parameters(tokens, parameter_close)


def _starts_async_method(tokens: list[_LexicalToken], start: int) -> bool:
    """Return whether tokens at *start* form an object/class method header."""
    index = start
    if tokens[index].text == "*":
        index += 1
        if index >= len(tokens):
            return False
    if tokens[index].text == "[":
        close = _matching_lexical_delimiter(tokens, index, "[", "]")
        if close is None:
            return False
        index = close + 1
    else:
        if tokens[index].text in {"(", ")", "{", "}", ",", ";", ":", "=>"}:
            return False
        index += 1
    if index < len(tokens) and tokens[index].text == "?":
        index += 1
    if index < len(tokens) and tokens[index].text == "<":
        close = _matching_lexical_delimiter(tokens, index, "<", ">")
        if close is None:
            return False
        index = close + 1
    return index < len(tokens) and tokens[index].text == "("


def _javascript_line_terminator_is_significant(
    tokens: list[_LexicalToken],
    current: int,
    language: str,
) -> bool:
    """Return whether the gap before *current* is a restricted-production newline."""
    previous = tokens[current - 1]
    token = tokens[current]
    if previous.restricted_line_terminator_after:
        if previous.text in {"break", "continue"}:
            return _is_identifier_lexical_token(token)
        return token.text not in {";", "}"}
    if token.restricted_line_terminator_before:
        return True
    if not previous.async_line_terminator_candidate:
        return False
    if token.text == "function" or _starts_async_arrow(tokens, current, language):
        return True
    return previous.async_method_candidate and _starts_async_method(tokens, current)


def _non_python_signature_tokens(signature: str, language: str) -> tuple[str, ...]:
    """Tokenize a signature while discarding only inter-token whitespace.

    Strings, templates, and regular expressions remain exact tokens. Comments
    remain opaque to structural scanning but are omitted from equivalence.
    Contiguous compound operators remain distinct from their whitespace-split
    spellings, preventing formatting normalization from erasing syntax changes.
    """
    tokens = [
        token
        for token in _non_python_lexical_tokens(signature, language)
        if not token.normalization_ignored
    ]
    normalized: list[str] = []
    for index, token in enumerate(tokens):
        if (
            index
            and language in _JAVASCRIPT_LANGUAGES
            and _contains_javascript_line_terminator(signature[tokens[index - 1].end : token.start])
            and _javascript_line_terminator_is_significant(tokens, index, language)
        ):
            normalized.append(_RESTRICTED_LINE_TERMINATOR_TOKEN)
        normalized.append(token.text)
    return tuple(normalized)


def _matching_parenthesis_close(value: str, start: int) -> int | None:
    """Return the closing index for the parenthesis opened at ``start``."""
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(start, len(value)):
        char = value[index]
        if quote is not None:
            if char == quote and not escaped:
                quote = None
            escaped = char == "\\" and not escaped
            if char != "\\":
                escaped = False
            continue
        if char in {'"', "'", "`"}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def _top_level_parenthesis(value: str, start: int) -> int:
    """Find a parenthesis outside generic/type-constraint delimiters."""
    angle_depth = 0
    square_depth = 0
    brace_depth = 0
    quote: str | None = None
    escaped = False
    for index in range(start, len(value)):
        char = value[index]
        if quote is not None:
            if char == quote and not escaped:
                quote = None
            escaped = char == "\\" and not escaped
            if char != "\\":
                escaped = False
            continue
        if char in {'"', "'", "`"}:
            quote = char
        elif char == "<" and _matching_angle_close(value, index) is not None:
            angle_depth += 1
        elif char == ">" and angle_depth and (index == 0 or value[index - 1] != "="):
            angle_depth -= 1
        elif char == "[":
            square_depth += 1
        elif char == "]" and square_depth:
            square_depth -= 1
        elif char == "{":
            brace_depth += 1
        elif char == "}" and brace_depth:
            brace_depth -= 1
        elif char == "(" and not (angle_depth or square_depth or brace_depth):
            return index
    return -1


def _parameter_list_start(signature: str) -> int:
    """Locate the callable parameter list, skipping constraints and receivers."""
    definition = signature.rfind("def ")
    if definition != -1:
        return _top_level_parenthesis(signature, definition + len("def "))
    leading_offset = len(signature) - len(signature.lstrip())
    if signature.startswith("func ", leading_offset):
        declaration_start = leading_offset + len("func ")
        first = _top_level_parenthesis(signature, declaration_start)
        if first != -1 and signature[declaration_start:first].strip() == "":
            receiver_close = _matching_parenthesis_close(signature, first)
            if receiver_close is None:
                return -1
            return _top_level_parenthesis(signature, receiver_close + 1)
        return first
    return _top_level_parenthesis(signature, 0)


def _balanced_parameter_close(
    signature: str,
    start: int,
    language: str | None,
) -> int | None:
    """Return the parameter-list close without inspecting opaque literals."""
    if language in _NON_PYTHON_NORMALIZED_LANGUAGES:
        depth = 0
        for token in _non_python_lexical_tokens(signature, language):
            if token.start < start:
                continue
            if token.text == "(":
                depth += 1
            elif token.text == ")":
                depth -= 1
                if depth == 0:
                    return token.start
        return None

    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(start, len(signature)):
        char = signature[index]
        if quote is not None:
            if char == quote and not escaped:
                quote = None
            escaped = char == "\\" and not escaped
            if char != "\\":
                escaped = False
            continue
        if char in {'"', "'", "`"}:
            quote = char
        elif char in ("(", "["):
            depth += 1
        elif char in (")", "]"):
            depth -= 1
            if depth == 0:
                return index
    return None


def _extract_balanced_params(signature: str, language: str | None = None) -> str | None:
    """Extract content between first balanced parentheses in signature."""
    start = _parameter_list_start(signature)
    if start == -1:
        return None
    close = _balanced_parameter_close(signature, start, language)
    return None if close is None else signature[start + 1 : close]


def _matching_angle_close(value: str, start: int) -> int | None:
    """Return the closing index for a balanced generic ``<...>`` group."""
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(start, len(value)):
        char = value[index]
        if quote is not None:
            if char == quote and not escaped:
                quote = None
            escaped = char == "\\" and not escaped
            if char != "\\":
                escaped = False
            continue
        if char in {'"', "'", "`"}:
            quote = char
            continue
        if char == "<":
            depth += 1
        elif char == ">" and (index == 0 or value[index - 1] != "="):
            depth -= 1
            if depth == 0:
                return index
    return None


def _is_assignment_operator(value: str, index: int) -> bool:
    """Return whether ``value[index]`` is a plain assignment operator."""
    previous = value[index - 1] if index else ""
    following = value[index + 1] if index + 1 < len(value) else ""
    return value[index] == "=" and previous not in "<>=!:" and following not in "=>"


def _is_generic_angle_start(value: str, index: int, *, in_default: bool) -> bool:
    """Recognize a TypeScript generic angle group without treating ``<`` as comparison."""
    before = value[index - 1] if index else ""
    after = value[index + 1] if index + 1 < len(value) else ""
    if not after or after in "=<>!+-*/%&|^?:,)}":
        return False
    close = _matching_angle_close(value, index)
    if close is None:
        return False
    prefix = value[:index].rstrip()
    suffix = value[close + 1 :].lstrip()

    # Named generic types/calls keep ``<`` adjacent to their owner.  Preserve
    # the existing whitespace guard so comparison defaults such as
    # ``value = left < right`` are not reinterpreted as type syntax.
    if before and not before.isspace() and before not in "=<>!+-*/%&|^?:,({":
        if not in_default:
            return True
        # TypeScript instantiation expressions do not need to be called.
        # At the outer parameter depth, a completed expression may end at the
        # parameter delimiter or continue through a call/member access.  Do
        # not accept an identifier/operator suffix: that remains comparison
        # syntax (for example ``lower<current, other = current>lower``).
        return not suffix or suffix.startswith((",", "(", ".", "?.", "["))

    # A generic function type starts its type expression with ``<T, U>``.
    # Requiring a preceding annotation colon and a following parameter list
    # distinguishes ``cb: <T, U>(x: T) => U`` from comparison expressions.
    starts_typed_callable = prefix.endswith(":") and not in_default
    starts_defaulted_callable = prefix.endswith("=") and in_default
    return (starts_typed_callable or starts_defaulted_callable) and suffix.startswith("(")


def _split_non_python_params(params_str: str, language: str) -> list[str]:
    """Split parameters on non-opaque, top-level commas."""
    params: list[str] = []
    segment_start = 0
    depth = 0
    angle_depth = 0
    in_default = False
    track_angles = language == "typescript"

    for token in _non_python_lexical_tokens(params_str, language):
        if token.opaque:
            continue
        if token.text in {"(", "[", "{"}:
            depth += 1
        elif (
            track_angles
            and token.text == "<"
            and _is_generic_angle_start(params_str, token.start, in_default=in_default)
        ):
            depth += 1
            angle_depth += 1
        elif token.text in {
            ")",
            "]",
            "}",
        }:
            depth -= 1
        elif token.text == ">" and angle_depth:
            depth -= 1
            angle_depth -= 1

        if depth == 0 and token.text == "=":
            in_default = True
        if depth == 0 and token.text == ",":
            params.append(params_str[segment_start : token.start].strip())
            segment_start = token.end
            in_default = False

    last = params_str[segment_start:].strip()
    if last:
        params.append(last)
    return params


def extract_params(signature: str, language: str | None = None) -> list[str]:
    """Extract parameter list from a signature string.

    Handles signatures like:
        def foo(a: int, b: str = "x") -> bool
        def bar(a: Callable[[int], str], b: int) -> None
        fn foo(a: i32, b: &str) -> bool

    TypeScript generic angle groups are tracked only when the caller supplies
    ``language="typescript"`` so Python comparison defaults are not mistaken
    for type syntax.
    """
    params_str = _extract_balanced_params(signature, language)
    if params_str is None or not params_str.strip():
        return []

    if language in _NON_PYTHON_NORMALIZED_LANGUAGES:
        non_python_params = _split_non_python_params(params_str, language)
        return [param for param in non_python_params if param and param not in ("self", "cls")]

    # Split by comma respecting nesting, quoted strings, and lambda headers.
    params: list[str] = []
    depth = 0
    current: list[str] = []
    quote: str | None = None
    escaped = False
    lambda_header = False
    angle_depth = 0
    track_angles = language == "typescript"
    in_default = False
    index = 0
    while index < len(params_str):
        ch = params_str[index]
        if quote is not None:
            current.append(ch)
            if ch == quote and not escaped:
                quote = None
            escaped = ch == "\\" and not escaped
            if ch != "\\":
                escaped = False
            index += 1
            continue
        if ch in {'"', "'", "`"}:
            quote = ch
            current.append(ch)
            index += 1
            continue
        if params_str.startswith("lambda", index):
            before = params_str[index - 1] if index else " "
            after_index = index + len("lambda")
            after = params_str[after_index] if after_index < len(params_str) else " "
            if not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_"):
                lambda_header = True
        if ch in ("(", "[", "{"):
            depth += 1
        elif (
            track_angles
            and ch == "<"
            and _is_generic_angle_start(params_str, index, in_default=in_default)
        ):
            depth += 1
            angle_depth += 1
        elif ch in (")", "]", "}"):
            depth -= 1
        elif ch == ">" and angle_depth and (index == 0 or params_str[index - 1] != "="):
            depth -= 1
            angle_depth -= 1
        elif ch == ":" and depth == 0 and lambda_header:
            lambda_header = False
        if depth == 0 and ch == "=" and _is_assignment_operator(params_str, index):
            in_default = True
        if ch == "," and depth == 0 and not lambda_header:
            params.append("".join(current).strip())
            current = []
            in_default = False
        else:
            current.append(ch)
        index += 1
    last = "".join(current).strip()
    if last:
        params.append(last)

    return [p for p in params if p and p not in ("self", "cls")]


def _normalized_non_python_params(
    params: list[str],
    language: str,
) -> list[tuple[str, ...]]:
    """Normalize formatting independently inside each non-Python parameter."""
    return [_non_python_signature_tokens(param, language) for param in params]


def _extract_return_type(signature: str) -> str | None:
    """Extract return type annotation from signature."""
    match = re.search(r"\)\s*->\s*(.+)$", signature)
    if match:
        return match.group(1).strip()
    return None


def _param_has_default(param: str) -> bool:
    """Check if a parameter has a default value."""
    return _default_separator(param) is not None


def _strip_default(param: str) -> str:
    """Strip default value from parameter, keeping name and type."""
    separator = _default_separator(param)
    return param[:separator].strip() if separator is not None else param.strip()


def _param_default_value(param: str) -> str | None:
    """Extract default value from a parameter, or None if no default."""
    separator = _default_separator(param)
    return param[separator + 1 :].strip() if separator is not None else None


def _default_separator(param: str) -> int | None:
    """Locate a top-level assignment ``=`` outside strings and nested syntax."""
    depth = 0
    quote: str | None = None
    escaped = False
    for index, char in enumerate(param):
        if quote is not None:
            if char == quote and not escaped:
                quote = None
            escaped = char == "\\" and not escaped
            if char != "\\":
                escaped = False
            continue
        if char in {'"', "'", "`"}:
            quote = char
            continue
        if char in "([{":
            depth += 1
            continue
        if char in ")]}":
            depth -= 1
            continue
        if char != "=" or depth != 0:
            continue
        previous = param[index - 1] if index else ""
        following = param[index + 1] if index + 1 < len(param) else ""
        if previous in "<>=!:" or following == "=":
            continue
        return index
    return None


def is_default_value_change(old_signature: str, new_signature: str) -> bool:
    """Check if the ONLY difference is a default value change on existing params."""
    old_params = extract_params(old_signature)
    new_params = extract_params(new_signature)
    if len(old_params) != len(new_params):
        return False
    for old_p, new_p in zip(old_params, new_params, strict=False):
        if _strip_default(old_p) != _strip_default(new_p):
            return False
        old_def = _param_default_value(old_p)
        new_def = _param_default_value(new_p)
        if old_def != new_def and old_def is not None and new_def is not None:
            return True
    return False


def _param_name(param: str) -> str:
    """Return the declared name from a Python-like parameter string."""
    value = _strip_default(param).strip().lstrip("*")
    value = value.split(":", 1)[0].strip()
    return value.rstrip("?").split(maxsplit=1)[0] if value else value


def _signature_tail(signature: str, language: str | None = None) -> str:
    """Return syntax following the balanced parameter list."""
    start = _parameter_list_start(signature)
    if start == -1:
        return ""
    close = _balanced_parameter_close(signature, start, language)
    return "" if close is None else signature[close + 1 :].strip()


def _normalized_non_python_signature_tail(
    signature: str,
    language: str,
) -> tuple[str, ...]:
    """Return the post-parameter syntax without formatting-only differences."""
    return _non_python_signature_tokens(_signature_tail(signature, language), language)


def _assessment(
    rule_id: str,
    category_id: str,
    category: str,
    breaking: bool | None,
    confidence: Confidence,
    evidence: str,
    *gaps: str,
) -> SignatureAssessment:
    """Build a typed signature assessment."""
    return SignatureAssessment(
        rule_id=rule_id,
        category_id=category_id,
        category=category,
        breaking=breaking,
        confidence=confidence,
        evidence=(evidence,),
        analysis_gaps=tuple(gaps),
    )


def _combine_python_syntax_assessments(
    assessments: list[SignatureAssessment],
) -> SignatureAssessment:
    """Combine every retained Python syntax change into one honest assessment."""
    first = assessments[0]
    same_category = all(
        assessment.rule_id == first.rule_id
        and assessment.category_id == first.category_id
        and assessment.category == first.category
        and assessment.breaking == first.breaking
        for assessment in assessments[1:]
    )
    evidence = tuple(
        dict.fromkeys(item for assessment in assessments for item in assessment.evidence)
    )
    analysis_gaps = tuple(
        dict.fromkeys(item for assessment in assessments for item in assessment.analysis_gaps)
    )
    if same_category:
        return SignatureAssessment(
            rule_id=first.rule_id,
            category_id=first.category_id,
            category=first.category,
            breaking=first.breaking,
            confidence=first.confidence,
            evidence=evidence,
            analysis_gaps=analysis_gaps,
        )

    breaking: bool | None
    if any(assessment.breaking is True for assessment in assessments):
        breaking = True
    elif all(assessment.breaking is False for assessment in assessments):
        breaking = False
    else:
        breaking = None
    compound_gap = (
        "Compound signature compatibility could not be represented by one bounded "
        "rule; review every evidence item and analysis gap"
    )
    return SignatureAssessment(
        rule_id="DG110",
        category_id="signature_changed",
        category="SIGNATURE CHANGED",
        breaking=breaking,
        confidence="high",
        evidence=evidence,
        analysis_gaps=(compound_gap, *analysis_gaps),
    )


def _python_retained_syntax_assessments(
    old: _PythonSignature,
    new: _PythonSignature,
) -> list[SignatureAssessment]:
    """Describe non-call-shape syntax changes that a primary rule can coexist with."""
    old_names = {parameter.name for parameter in old.parameters}
    new_by_name = {parameter.name: parameter for parameter in new.parameters}
    pairs = [
        (old_parameter, new_by_name[old_parameter.name])
        for old_parameter in old.parameters
        if old_parameter.name in new_by_name
    ]
    simple_rename = (
        len(old.parameters) == len(new.parameters)
        and {parameter.name for parameter in old.parameters}
        != {parameter.name for parameter in new.parameters}
        and all(
            old_parameter.kind == new_parameter.kind
            for old_parameter, new_parameter in zip(old.parameters, new.parameters, strict=True)
        )
    )
    if simple_rename:
        pairs.extend(
            (old_parameter, new_parameter)
            for old_parameter, new_parameter in zip(old.parameters, new.parameters, strict=True)
            if old_parameter.name != new_parameter.name
            and old_parameter.name not in new_by_name
            and new_parameter.name not in old_names
        )

    assessments: list[SignatureAssessment] = []
    for old_parameter, new_parameter in pairs:
        parameter_label = (
            f"Python parameter '{old_parameter.name}'"
            if old_parameter.name == new_parameter.name
            else (f"renamed Python parameter '{old_parameter.name}' -> '{new_parameter.name}'")
        )
        if old_parameter.default is None and new_parameter.default is not None:
            assessments.append(
                _assessment(
                    "DG106",
                    "default_added",
                    "DEFAULT ADDED",
                    False,
                    "high",
                    f"Default added to {parameter_label}",
                )
            )
        elif (
            old_parameter.default is not None
            and new_parameter.default is not None
            and old_parameter.default != new_parameter.default
        ):
            assessments.append(
                _assessment(
                    "DG105",
                    "default_changed",
                    "DEFAULT VALUE CHANGED",
                    False,
                    "high",
                    f"Default changed for {parameter_label}",
                    "Runtime behavior can change even though omitted-argument calls remain valid",
                )
            )
        if old_parameter.annotation != new_parameter.annotation:
            assessments.append(
                _assessment(
                    "DG107",
                    "parameter_annotation_changed",
                    "PARAMETER ANNOTATION CHANGED",
                    None,
                    "high",
                    f"Annotation syntax changed for {parameter_label}",
                    "Python annotations alone do not prove runtime or type-checker compatibility",
                )
            )

    if old.return_annotation != new.return_annotation:
        assessments.append(
            _assessment(
                "DG108",
                "return_annotation_changed",
                "RETURN ANNOTATION CHANGED",
                None,
                "high",
                "Python return annotation syntax changed",
                "Annotation syntax does not prove runtime or type-checker compatibility",
            )
        )
    if old.decorators != new.decorators:
        assessments.append(
            _assessment(
                "DG110",
                "decorator_changed",
                "DECORATOR CHANGED",
                None,
                "high",
                "Python decorator syntax changed",
                "Decorator runtime behavior and call compatibility were not evaluated",
            )
        )
    if old.type_parameters != new.type_parameters:
        assessments.append(
            _assessment(
                "DG110",
                "type_parameters_changed",
                "TYPE PARAMETERS CHANGED",
                None,
                "high",
                "Python type-parameter syntax changed",
                "Type-checker compatibility was not evaluated",
            )
        )
    return assessments


def _merge_python_primary_and_syntax(
    primary: SignatureAssessment,
    syntax_assessments: list[SignatureAssessment],
) -> SignatureAssessment:
    """Retain direct call-shape precedence without dropping secondary evidence."""
    if not syntax_assessments:
        return primary

    assessments = [primary, *syntax_assessments]
    evidence = tuple(
        dict.fromkeys(item for assessment in assessments for item in assessment.evidence)
    )
    analysis_gaps = tuple(
        dict.fromkeys(item for assessment in assessments for item in assessment.analysis_gaps)
    )
    same_category = all(
        assessment.rule_id == primary.rule_id
        and assessment.category_id == primary.category_id
        and assessment.category == primary.category
        and assessment.breaking == primary.breaking
        for assessment in assessments[1:]
    )
    preserve_primary = primary.breaking is True or (
        primary.rule_id == "DG110" and primary.breaking is None
    )
    if same_category or preserve_primary:
        return SignatureAssessment(
            rule_id=primary.rule_id,
            category_id=primary.category_id,
            category=primary.category,
            breaking=primary.breaking,
            confidence=primary.confidence,
            evidence=evidence,
            analysis_gaps=analysis_gaps,
        )

    breaking = False if all(assessment.breaking is False for assessment in assessments) else None
    compound_gap = (
        "Compound signature compatibility could not be represented by one bounded "
        "rule; review every evidence item and analysis gap"
    )
    return SignatureAssessment(
        rule_id="DG110",
        category_id="signature_changed",
        category="SIGNATURE CHANGED",
        breaking=breaking,
        confidence="high",
        evidence=evidence,
        analysis_gaps=tuple(dict.fromkeys((compound_gap, *analysis_gaps))),
    )


def _is_class_declaration(signature: str, language: str) -> bool:
    """Return whether the outer extracted declaration is a class."""
    if language == "python":
        declaration = _parse_python_declaration(signature)
        return declaration is not None and isinstance(declaration.node, ast.ClassDef)
    return (
        re.match(
            r"^\s*(?:export\s+(?:default\s+)?)?(?:abstract\s+)?class\s+",
            signature,
        )
        is not None
    )


def _ast_dump(node: ast.AST | None) -> str | None:
    """Return a stable syntax representation for an optional AST node."""
    return ast.dump(node, include_attributes=False) if node is not None else None


_IGNORED_TYPE_PARAMETER_TOKENS = {
    tokenize.COMMENT,
    tokenize.DEDENT,
    tokenize.ENDMARKER,
    tokenize.INDENT,
    tokenize.NEWLINE,
    tokenize.NL,
}


def _source_offset(value: str, position: tuple[int, int]) -> int:
    """Translate a tokenizer line/column position to a string offset."""
    row, column = position
    lines = value.splitlines(keepends=True)
    return sum(len(line) for line in lines[: row - 1]) + column


def _without_python_type_parameters(signature: str) -> tuple[str, tuple[str, ...]]:
    """Remove only a declaration's PEP 695 clause and preserve normalized tokens.

    Python 3.11's tokenizer understands the lexical shape of PEP 695 even
    though its AST parser does not understand the grammar.  Tokenizing first
    therefore lets all supported runtimes feed the remaining declaration to
    :func:`ast.parse` while retaining type-parameter syntax for comparison.
    """
    try:
        tokens = list(tokenize.generate_tokens(StringIO(signature).readline))
    except (IndentationError, tokenize.TokenError):
        return signature, ()

    declaration_index: int | None = None
    for index, token in enumerate(tokens):
        if token.type != tokenize.NAME or token.string not in {"class", "def"}:
            continue
        # The declaration keyword starts its logical line, optionally after
        # ``async``.  This avoids keywords that only occur inside decorators.
        if token.line[: token.start[1]].strip() not in {"", "async"}:
            continue
        declaration_index = index
        break
    if declaration_index is None:
        return signature, ()

    significant = [
        index
        for index in range(declaration_index + 1, len(tokens))
        if tokens[index].type not in _IGNORED_TYPE_PARAMETER_TOKENS
    ]
    if len(significant) < 2:
        return signature, ()
    name_index, open_index = significant[:2]
    if tokens[name_index].type != tokenize.NAME or tokens[open_index].string != "[":
        return signature, ()

    depth = 0
    close_index: int | None = None
    for index in range(open_index, len(tokens)):
        token = tokens[index]
        if token.type != tokenize.OP:
            continue
        if token.string == "[":
            depth += 1
        elif token.string == "]":
            depth -= 1
            if depth == 0:
                close_index = index
                break
    if close_index is None:
        return signature, ()

    type_parameters = tuple(
        token.string
        for token in tokens[open_index : close_index + 1]
        if token.type not in _IGNORED_TYPE_PARAMETER_TOKENS
    )
    start = _source_offset(signature, tokens[open_index].start)
    end = _source_offset(signature, tokens[close_index].end)
    return signature[:start] + signature[end:], type_parameters


def _parse_python_declaration(
    signature: str,
) -> _PythonDeclaration | None:
    """Parse an extracted Python declaration without importing or executing it."""
    normalized_signature, type_parameters = _without_python_type_parameters(signature)
    source = normalized_signature.rstrip()
    if not source.endswith(":"):
        source += ":"
    source += "\n    pass\n"
    try:
        module = ast.parse(source)
    except SyntaxError:
        return None
    if len(module.body) != 1:
        return None
    declaration = module.body[0]
    if not isinstance(declaration, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return None
    return _PythonDeclaration(node=declaration, type_parameters=type_parameters)


def _python_signatures_equivalent(old_signature: str, new_signature: str) -> bool:
    """Return whether Python parses both declarations to identical structure."""
    old = _parse_python_declaration(old_signature)
    new = _parse_python_declaration(new_signature)
    return (
        old is not None
        and new is not None
        and old.type_parameters == new.type_parameters
        and _ast_dump(old.node) == _ast_dump(new.node)
    )


def _parse_python_signature(signature: str) -> _PythonSignature | None:
    """Parse Python callable syntax needed by the compatibility rules."""
    declaration = _parse_python_declaration(signature)
    if declaration is None:
        return None
    function = declaration.node
    if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None

    positional_nodes = [*function.args.posonlyargs, *function.args.args]
    positional_defaults: list[ast.expr | None] = [None] * (
        len(positional_nodes) - len(function.args.defaults)
    ) + list(function.args.defaults)
    parameters: list[_PythonParameter] = []
    posonly_count = len(function.args.posonlyargs)
    for index, (node, default) in enumerate(
        zip(positional_nodes, positional_defaults, strict=True)
    ):
        kind: PythonParameterKind = (
            "positional_only" if index < posonly_count else "positional_or_keyword"
        )
        parameters.append(
            _PythonParameter(node.arg, kind, _ast_dump(default), _ast_dump(node.annotation))
        )
    if function.args.vararg is not None:
        node = function.args.vararg
        parameters.append(
            _PythonParameter(node.arg, "var_positional", None, _ast_dump(node.annotation))
        )
    for node, default in zip(
        function.args.kwonlyargs,
        function.args.kw_defaults,
        strict=True,
    ):
        parameters.append(
            _PythonParameter(
                node.arg, "keyword_only", _ast_dump(default), _ast_dump(node.annotation)
            )
        )
    if function.args.kwarg is not None:
        node = function.args.kwarg
        parameters.append(
            _PythonParameter(node.arg, "var_keyword", None, _ast_dump(node.annotation))
        )

    return _PythonSignature(
        parameters=tuple(parameters),
        return_annotation=_ast_dump(function.returns),
        decorators=tuple(_ast_dump(node) or "" for node in function.decorator_list),
        type_parameters=declaration.type_parameters,
    )


def _parameter_kind_breaks(
    old_kind: PythonParameterKind, new_kind: PythonParameterKind
) -> bool | None:
    """Assess whether a Python parameter-kind transition restricts existing calls."""
    if old_kind == new_kind:
        return False
    # Replacing an unbounded capture with any other kind loses calls even when
    # the replacement accepts the same argument channel: one finite parameter
    # cannot accept every call previously captured by *args/**kwargs.
    if old_kind in {"var_positional", "var_keyword"}:
        return True
    # These are the two provable fixed-to-variadic supersets. Other transitions
    # to a variadic kind lose either positional or keyword callability.
    if new_kind == "var_positional":
        return old_kind != "positional_only"
    if new_kind == "var_keyword":
        return old_kind != "keyword_only"
    if (old_kind, new_kind) in {
        ("positional_only", "positional_or_keyword"),
        ("keyword_only", "positional_or_keyword"),
    }:
        return False
    if (old_kind, new_kind) in {
        ("positional_or_keyword", "positional_only"),
        ("positional_or_keyword", "keyword_only"),
        ("positional_only", "keyword_only"),
        ("keyword_only", "positional_only"),
    }:
        return True
    return None


def _has_parameter_kind(signature: _PythonSignature, kind: PythonParameterKind) -> bool:
    """Return whether *signature* contains a parameter of *kind*."""
    return any(parameter.kind == kind for parameter in signature.parameters)


def _variadic_capture_lost(old: _PythonSignature, new: _PythonSignature) -> bool:
    """Return whether an accepted unbounded argument channel disappeared."""
    return (
        _has_parameter_kind(old, "var_positional")
        and not _has_parameter_kind(new, "var_positional")
    ) or (_has_parameter_kind(old, "var_keyword") and not _has_parameter_kind(new, "var_keyword"))


def _complementary_variadic_preserves_parameter_calls(
    new: _PythonSignature,
    old_kind: PythonParameterKind,
    new_kind: PythonParameterKind,
) -> bool:
    """Return whether variadics cover every channel accepted by the old kind."""
    if new_kind == "var_positional":
        return old_kind in {"positional_or_keyword", "keyword_only"} and _has_parameter_kind(
            new, "var_keyword"
        )
    if new_kind == "var_keyword":
        return old_kind in {"positional_only", "positional_or_keyword"} and _has_parameter_kind(
            new, "var_positional"
        )
    return False


def _parameter_kind_change_outcome(
    old: _PythonSignature,
    new: _PythonSignature,
    name: str,
    old_kind: PythonParameterKind,
    new_kind: PythonParameterKind,
) -> bool | None:
    """Apply the kind matrix without ignoring compensating variadic capture."""
    outcome = _parameter_kind_breaks(old_kind, new_kind)
    if outcome is not True:
        return outcome

    new_by_name = {parameter.name: parameter for parameter in new.parameters}
    new_parameter = new_by_name[name]
    if old_kind == "var_positional" and _has_parameter_kind(new, "var_positional"):
        return True if new_parameter.required else None
    if old_kind == "var_keyword" and _has_parameter_kind(new, "var_keyword"):
        return True if new_parameter.required else None

    if _complementary_variadic_preserves_parameter_calls(new, old_kind, new_kind):
        return False

    # A retained optional fixed parameter plus a variadic can keep accepting
    # the old channel, but positional values may bind to a different target.
    # Preserve that uncertainty rather than manufacturing a breaking witness.
    if old_kind == "positional_or_keyword" and not new_parameter.required:
        if new_kind == "keyword_only" and _has_parameter_kind(new, "var_positional"):
            return None
        if new_kind == "positional_only" and _has_parameter_kind(new, "var_keyword"):
            return None
    return True


def _removed_parameter_breaks_call_shape(
    old: _PythonSignature,
    new: _PythonSignature,
    removed: list[_PythonParameter],
) -> bool | None:
    """Assess pure removals against variadic capture in the new signature.

    ``False`` is limited to cases where every argument channel accepted by a
    removed parameter remains accepted and removed positional slots form a
    trailing suffix. Non-trailing capture can rebind retained positional
    parameters, so that compound shape remains unknown rather than receiving a
    false breaking or nonbreaking claim.
    """
    captures_positional = _has_parameter_kind(new, "var_positional")
    captures_keyword = _has_parameter_kind(new, "var_keyword")
    for parameter in removed:
        if parameter.kind in {"positional_only", "var_positional"}:
            if not captures_positional:
                return True
        elif parameter.kind == "positional_or_keyword":
            if not captures_positional or not captures_keyword:
                return True
        elif parameter.kind in {"keyword_only", "var_keyword"}:
            if not captures_keyword:
                return True

    positional_kinds = {"positional_only", "positional_or_keyword"}
    removed_names = {parameter.name for parameter in removed}
    old_positional = [
        parameter.name for parameter in old.parameters if parameter.kind in positional_kinds
    ]
    retained_positional = [name for name in old_positional if name not in removed_names]
    new_positional = [
        parameter.name for parameter in new.parameters if parameter.kind in positional_kinds
    ]
    if new_positional != retained_positional:
        return None
    if old_positional[: len(retained_positional)] != retained_positional:
        return None
    return False


def _retained_positional_index_changes(
    old: _PythonSignature,
    new: _PythonSignature,
) -> list[tuple[str, int, int]]:
    """Return retained fixed positional names whose positional slot changed."""
    positional_kinds = {"positional_only", "positional_or_keyword"}
    old_names = [
        parameter.name for parameter in old.parameters if parameter.kind in positional_kinds
    ]
    new_names = [
        parameter.name for parameter in new.parameters if parameter.kind in positional_kinds
    ]
    old_indices = {name: index for index, name in enumerate(old_names)}
    new_indices = {name: index for index, name in enumerate(new_names)}
    return [
        (name, old_indices[name], new_indices[name])
        for name in old_names
        if name in new_indices and old_indices[name] != new_indices[name]
    ]


def _required_additions_with_call_demand(
    old: _PythonSignature,
    new: _PythonSignature,
    required: list[str],
) -> list[str]:
    """Return added required names omitted by one valid old call.

    A canonical old call supplies only required positional-only parameters by
    position and supplies other required fixed parameters by keyword.  Added
    names are absent from that call.  An added required fixed parameter is
    therefore a new demand unless the unavoidable old positional-only prefix
    fills its new positional slot.  This is more precise than comparing total
    required counts: retained parameters can become variadic or defaulted
    while an added parameter still rejects an old all-keyword call.
    """
    old_required_positional_only = sum(
        parameter.required and parameter.kind == "positional_only" for parameter in old.parameters
    )
    new_positional_indices = {
        parameter.name: index
        for index, parameter in enumerate(
            parameter
            for parameter in new.parameters
            if parameter.kind in {"positional_only", "positional_or_keyword"}
        )
    }
    new_by_name = {parameter.name: parameter for parameter in new.parameters}
    return [
        name
        for name in required
        if new_by_name[name].kind == "keyword_only"
        or new_positional_indices[name] >= old_required_positional_only
    ]


def _added_keyword_parameter_claims_old_kwargs(
    old: _PythonSignature,
    new: _PythonSignature,
    added: list[str],
) -> bool:
    """Return whether an addition creates a duplicate-binding witness.

    ``**kwargs`` accepts every previously unknown keyword.  If an added
    positional-or-keyword parameter can also be filled by a positional call
    accepted by the old signature, that old call may repeat the added name in
    ``**kwargs``.  The new fixed parameter then rejects it as a duplicate.
    """
    if not _has_parameter_kind(old, "var_keyword"):
        return False

    old_has_varargs = _has_parameter_kind(old, "var_positional")
    old_fixed_positional_capacity = sum(
        parameter.kind in {"positional_only", "positional_or_keyword"}
        for parameter in old.parameters
    )
    new_positional = [
        parameter
        for parameter in new.parameters
        if parameter.kind in {"positional_only", "positional_or_keyword"}
    ]
    added_names = set(added)
    return any(
        parameter.name in added_names
        and parameter.kind == "positional_or_keyword"
        and (old_has_varargs or index < old_fixed_positional_capacity)
        for index, parameter in enumerate(new_positional)
    )


def _compound_parameter_change_assessment(
    old: _PythonSignature,
    new: _PythonSignature,
    removed: list[str],
    added: list[str],
) -> SignatureAssessment | None:
    """Return a direct call-shape witness for a non-simple mixed name change."""
    old_by_name = {parameter.name: parameter for parameter in old.parameters}
    new_by_name = {parameter.name: parameter for parameter in new.parameters}
    added_required = [name for name in added if new_by_name[name].required]
    old_required_count = sum(parameter.required for parameter in old.parameters)
    new_required_count = sum(parameter.required for parameter in new.parameters)
    added_required_keyword_only = any(
        new_by_name[name].kind == "keyword_only" for name in added_required
    )
    if new_required_count > old_required_count or added_required_keyword_only:
        return _assessment(
            "DG102",
            "required_parameter_added",
            "PARAMETER ADDED (REQUIRED)",
            True,
            "high",
            "Compound Python parameter change added required call demand: "
            f"{', '.join(added_required)}",
        )

    positional_kinds = {"positional_only", "positional_or_keyword"}
    old_positional_capacity = sum(
        parameter.kind in positional_kinds for parameter in old.parameters
    )
    new_positional_capacity = sum(
        parameter.kind in positional_kinds for parameter in new.parameters
    )
    positional_capacity_reduced = (
        not _has_parameter_kind(new, "var_positional")
        and old_positional_capacity > new_positional_capacity
    )
    removed_keyword_modes = [
        name
        for name in removed
        if old_by_name[name].kind in {"positional_or_keyword", "keyword_only"}
    ]
    keyword_mode_lost = bool(removed_keyword_modes) and not _has_parameter_kind(new, "var_keyword")
    if positional_capacity_reduced or keyword_mode_lost:
        evidence_parts: list[str] = []
        if positional_capacity_reduced:
            evidence_parts.append(
                "fixed positional capacity decreased from "
                f"{old_positional_capacity} to {new_positional_capacity}"
            )
        if keyword_mode_lost:
            evidence_parts.append(
                f"removed keyword-call name(s) are not captured: {', '.join(removed_keyword_modes)}"
            )
        return _assessment(
            "DG101",
            "parameter_removed",
            "PARAMETER REMOVED",
            True,
            "high",
            "Compound Python parameter change rejects an old call because "
            + "; ".join(evidence_parts),
        )
    return None


def _parameter_kind_transition_breaks_call_shape(
    old: _PythonSignature,
    new: _PythonSignature,
) -> bool:
    """Return whether kind changes provably alter calls accepted by ``old``.

    A nominally permissive transition can still break existing calls when it
    changes a fixed positional binding or claims values previously captured by
    ``*args``/``**kwargs``.
    """
    if _variadic_capture_lost(old, new):
        return True

    positional_kinds = {"positional_only", "positional_or_keyword"}
    old_by_name = {parameter.name: parameter for parameter in old.parameters}
    new_by_name = {parameter.name: parameter for parameter in new.parameters}
    positional_channels_preserved_by_varargs = {
        name
        for name in old_by_name.keys() & new_by_name.keys()
        if old_by_name[name].kind in positional_kinds
        and new_by_name[name].kind not in positional_kinds
        and (
            new_by_name[name].kind == "var_positional"
            or (_has_parameter_kind(new, "var_positional") and not new_by_name[name].required)
        )
    }
    old_positional = [
        parameter.name
        for parameter in old.parameters
        if parameter.kind in positional_kinds
        and parameter.name not in positional_channels_preserved_by_varargs
    ]
    new_positional = [
        parameter.name for parameter in new.parameters if parameter.kind in positional_kinds
    ]

    # Every positional argument accepted by the old signature must keep the
    # same named binding. Appending a new positional slot is safe only when the
    # old signature did not capture arbitrary trailing positional arguments.
    if new_positional[: len(old_positional)] != old_positional:
        return True
    if any(parameter.kind == "var_positional" for parameter in old.parameters) and len(
        new_positional
    ) > len(old_positional):
        return True

    old_fixed_positional_capacity = sum(
        parameter.kind in positional_kinds for parameter in old.parameters
    )
    new_positional_indices = {name: index for index, name in enumerate(new_positional)}
    if any(
        old_by_name[name].kind == "keyword_only"
        and new_by_name[name].kind == "positional_or_keyword"
        and new_positional_indices[name] < old_fixed_positional_capacity
        for name in old_by_name.keys() & new_by_name.keys()
    ):
        return True

    # A positional-only name can also be present in **kwargs. Making that name
    # keyword-bindable turns a previously valid call into a duplicate binding.
    return any(parameter.kind == "var_keyword" for parameter in old.parameters) and any(
        old_by_name[name].kind == "positional_only"
        and new_by_name[name].kind == "positional_or_keyword"
        for name in old_by_name.keys() & new_by_name.keys()
    )


def _parameter_kind_change_assessment(
    old: _PythonSignature,
    new: _PythonSignature,
    kind_changes: list[tuple[str, PythonParameterKind, PythonParameterKind]],
) -> SignatureAssessment:
    """Build the bounded assessment for retained parameter-kind changes."""
    call_shape_break = _parameter_kind_transition_breaks_call_shape(old, new)
    outcomes = [
        _parameter_kind_change_outcome(old, new, name, old_kind, new_kind)
        for name, old_kind, new_kind in kind_changes
    ]
    complementary_capture = any(
        _complementary_variadic_preserves_parameter_calls(new, old_kind, new_kind)
        for _, old_kind, new_kind in kind_changes
    )
    breaking: bool | None
    if call_shape_break or any(outcome is True for outcome in outcomes):
        breaking = True
    elif all(outcome is False for outcome in outcomes):
        breaking = False
    else:
        breaking = None
    gaps: tuple[str, ...]
    if breaking is None:
        gaps = ("Compatibility was not proven for every parameter-kind transition",)
    elif complementary_capture and breaking is False:
        gaps = ("Call acceptance is preserved, but argument binding and runtime behavior changed",)
    else:
        gaps = ()
    return _assessment(
        "DG110",
        "parameter_kind_changed",
        "PARAMETER KIND CHANGED",
        breaking,
        "high" if breaking is not None else "medium",
        (
            "Python parameter-kind transition changed an existing positional binding "
            "or variadic capture"
            if call_shape_break
            else "Python positional-only/keyword-only/variadic parameter kind changed"
        ),
        *gaps,
    )


def _python_signature_assessment(old_signature: str, new_signature: str) -> SignatureAssessment:
    """Assess Python call-shape compatibility from parsed signature syntax."""
    old = _parse_python_signature(old_signature)
    new = _parse_python_signature(new_signature)
    if old is None or new is None:
        return _assessment(
            "DG110",
            "signature_changed",
            "SIGNATURE CHANGED",
            None,
            "low",
            "Python signature syntax changed",
            "The running Python parser could not model this signature; compatibility is unknown",
        )

    old_params = list(old.parameters)
    new_params = list(new.parameters)
    old_by_name = {parameter.name: parameter for parameter in old_params}
    new_by_name = {parameter.name: parameter for parameter in new_params}
    old_names = [parameter.name for parameter in old_params]
    new_names = [parameter.name for parameter in new_params]

    removed = [name for name in old_names if name not in new_by_name]
    added = [name for name in new_names if name not in old_by_name]
    retained_defaults_removed = [
        name
        for name in old_names
        if name in new_by_name
        and old_by_name[name].default is not None
        and new_by_name[name].default is None
        and new_by_name[name].required
    ]
    kind_changes = [
        (name, old_by_name[name].kind, new_by_name[name].kind)
        for name in old_names
        if name in new_by_name and old_by_name[name].kind != new_by_name[name].kind
    ]

    # A compound name change must not hide a retained parameter becoming
    # required. This is a direct lost-call witness regardless of whether added
    # or remaining variadics capture removed peer parameters.
    if retained_defaults_removed:
        return _assessment(
            "DG104",
            "default_removed",
            "DEFAULT REMOVED",
            True,
            "high",
            "Default removed from retained Python parameter(s): "
            f"{', '.join(retained_defaults_removed)}",
        )

    if removed and not added:
        removed_parameters = [old_by_name[name] for name in removed]
        removal_break = _removed_parameter_breaks_call_shape(old, new, removed_parameters)
        kind_outcomes = [
            _parameter_kind_change_outcome(old, new, name, old_kind, new_kind)
            for name, old_kind, new_kind in kind_changes
        ]
        kind_call_shape_break = bool(kind_changes) and (
            _parameter_kind_transition_breaks_call_shape(old, new)
        )
        breaking: bool | None
        if (
            removal_break is True
            or kind_call_shape_break
            or any(outcome is True for outcome in kind_outcomes)
        ):
            breaking = True
        elif removal_break is False and all(outcome is False for outcome in kind_outcomes):
            breaking = False
        else:
            breaking = None
        if breaking is True and removal_break is True:
            evidence = (
                "Python parameter(s) removed without complete variadic capture: "
                f"{', '.join(removed)}"
            )
            gaps: tuple[str, ...] = ()
        elif breaking is True:
            evidence = (
                "Python parameter removal coincided with a retained parameter-kind restriction"
            )
            gaps = ()
        elif breaking is False:
            evidence = (
                "Removed Python parameter channel(s) remain accepted by trailing "
                f"variadic capture: {', '.join(removed)}"
            )
            gaps = ("Argument binding and runtime behavior can still change after capture",)
        else:
            evidence = (
                "Variadic capture accepts the removed Python parameter channel(s), but "
                "retained positional binding also changed"
            )
            gaps = ("Compatibility was not proven for the compound positional shape",)
        return _assessment(
            "DG101",
            "parameter_removed",
            "PARAMETER REMOVED",
            breaking,
            "high" if breaking is not None else "medium",
            evidence,
            *gaps,
        )
    if added and not removed:
        required = [name for name in added if new_by_name[name].required]
        required_call_demand = _required_additions_with_call_demand(old, new, required)
        if required_call_demand:
            return _assessment(
                "DG102",
                "required_parameter_added",
                "PARAMETER ADDED (REQUIRED)",
                True,
                "high",
                f"Required Python parameter(s) added: {', '.join(required_call_demand)}",
            )

        # A required addition can be offset by a retained parameter becoming
        # optional or more permissive. In that compound shape there is no new
        # required-demand witness, but inserting a positional slot can still
        # shift existing bindings under the project's reorder semantics.
        keyword_capture_claim = _added_keyword_parameter_claims_old_kwargs(old, new, added)
        call_shape_break = (
            _parameter_kind_transition_breaks_call_shape(old, new) or keyword_capture_claim
        )
        old_positions = {name: index for index, name in enumerate(old_names)}
        existing_in_new = [name for name in new_names if name in old_positions]
        inserted_before_existing = any(
            new_by_name[name].kind in {"positional_only", "positional_or_keyword"}
            and any(
                candidate in old_positions
                and new_by_name[candidate].kind in {"positional_only", "positional_or_keyword"}
                for candidate in new_names[index + 1 :]
            )
            for index, name in enumerate(new_names)
            if name in added
        )
        positional_index_changes = _retained_positional_index_changes(old, new)
        if positional_index_changes:
            shifted = ", ".join(
                f"{name} ({old_index} -> {new_index})"
                for name, old_index, new_index in positional_index_changes
            )
            return _assessment(
                "DG103",
                "parameter_reordered",
                "PARAMETERS REORDERED",
                True,
                "high",
                f"Python parameter addition shifted retained positional binding(s): {shifted}",
            )

        added_fixed_positional = any(
            new_by_name[name].kind in {"positional_only", "positional_or_keyword"} for name in added
        )
        if added_fixed_positional and (
            existing_in_new != old_names or inserted_before_existing or call_shape_break
        ):
            if _has_parameter_kind(old, "var_positional") and call_shape_break:
                evidence = (
                    "An added Python positional parameter claims values previously "
                    "captured by *args"
                )
            elif keyword_capture_claim:
                evidence = (
                    "An added Python positional-or-keyword parameter creates a duplicate "
                    "binding for a name previously accepted by **kwargs"
                )
            else:
                evidence = (
                    "A Python positional parameter was inserted before an existing "
                    "parameter, shifting positional argument binding"
                )
            return _assessment(
                "DG103",
                "parameter_reordered",
                "PARAMETERS REORDERED",
                True,
                "high",
                evidence,
            )

        # An optional/variadic addition must not hide a retained parameter's
        # call-shape transition. For example, adding ``*args`` can turn an
        # existing positional-or-keyword parameter into keyword-only syntax.
        if kind_changes:
            return _parameter_kind_change_assessment(old, new, kind_changes)
        variadic = [
            name for name in added if new_by_name[name].kind in {"var_positional", "var_keyword"}
        ]
        evidence = (
            f"Variadic Python parameter(s) added: {', '.join(variadic)}"
            if variadic
            else f"Defaulted Python parameter(s) added: {', '.join(added)}"
        )
        return _assessment(
            "DG106",
            "optional_parameter_added",
            "OPTIONAL PARAMETER ADDED",
            False,
            "high",
            evidence,
        )
    if removed and added:
        if _variadic_capture_lost(old, new):
            return _assessment(
                "DG110",
                "parameters_changed",
                "PARAMETERS CHANGED",
                True,
                "high",
                "Python parameter changes removed an unbounded *args or **kwargs capture",
            )
        positional_index_changes = _retained_positional_index_changes(old, new)
        simple_rename = len(old_params) == len(new_params) and all(
            old_parameter.kind == new_parameter.kind
            for old_parameter, new_parameter in zip(old_params, new_params, strict=True)
        )
        if not simple_rename:
            if positional_index_changes:
                shifted = ", ".join(
                    f"{name} ({old_index} -> {new_index})"
                    for name, old_index, new_index in positional_index_changes
                )
                return _assessment(
                    "DG103",
                    "parameter_reordered",
                    "PARAMETERS REORDERED",
                    True,
                    "high",
                    f"Retained Python positional parameter index changed: {shifted}",
                )
            compound_assessment = _compound_parameter_change_assessment(
                old,
                new,
                removed,
                added,
            )
            if compound_assessment is not None:
                return compound_assessment
            return _assessment(
                "DG110",
                "parameters_changed",
                "PARAMETERS CHANGED",
                None,
                "medium",
                "Python parameters were added and removed in one signature change",
                "The bounded rules could not prove whether this is a rename or a shape change",
            )
        paired_parameters = list(zip(old_params, new_params, strict=True))
        renamed_pairs = [
            (old_parameter, new_parameter)
            for old_parameter, new_parameter in paired_parameters
            if old_parameter.name != new_parameter.name
        ]
        defaults_removed = [
            (old_parameter.name, new_parameter.name)
            for old_parameter, new_parameter in paired_parameters
            if old_parameter.default is not None and new_parameter.default is None
        ]
        if defaults_removed:
            renamed = ", ".join(
                f"{old_name} -> {new_name}" for old_name, new_name in defaults_removed
            )
            return _assessment(
                "DG104",
                "default_removed",
                "DEFAULT REMOVED",
                True,
                "high",
                f"Default removed during compound Python parameter change: {renamed}",
            )

        if positional_index_changes:
            shifted = ", ".join(
                f"{name} ({old_index} -> {new_index})"
                for name, old_index, new_index in positional_index_changes
            )
            return _assessment(
                "DG103",
                "parameter_reordered",
                "PARAMETERS REORDERED",
                True,
                "high",
                f"Retained Python positional parameter index changed: {shifted}",
            )

        breaks_keyword_calls = any(
            old_parameter.kind in {"positional_or_keyword", "keyword_only"}
            for old_parameter, _ in renamed_pairs
        )
        compound_syntax_changed = any(
            old_parameter.default != new_parameter.default
            or old_parameter.annotation != new_parameter.annotation
            for old_parameter, new_parameter in paired_parameters
        ) or any(
            (
                old.return_annotation != new.return_annotation,
                old.decorators != new.decorators,
                old.type_parameters != new.type_parameters,
            )
        )
        if compound_syntax_changed and not breaks_keyword_calls:
            return _assessment(
                "DG110",
                "parameters_changed",
                "PARAMETERS CHANGED",
                None,
                "medium",
                "Python parameter names and additional signature syntax changed together",
                "The bounded rules could not isolate the compound change's compatibility",
            )
        return _assessment(
            "DG109",
            "parameter_renamed",
            "PARAMETER RENAMED",
            breaks_keyword_calls,
            "high",
            f"Python parameter names changed: {', '.join(removed)} -> {', '.join(added)}",
            *(
                ("Positional calls may remain compatible; keyword-call compatibility breaks",)
                if breaks_keyword_calls
                else ()
            ),
        )

    if kind_changes:
        return _parameter_kind_change_assessment(old, new, kind_changes)

    if old_names != new_names:
        old_positional = [
            parameter.name
            for parameter in old_params
            if parameter.kind in {"positional_only", "positional_or_keyword"}
        ]
        new_positional = [
            parameter.name
            for parameter in new_params
            if parameter.kind in {"positional_only", "positional_or_keyword"}
        ]
        positional_break = old_positional != new_positional
        return _assessment(
            "DG103",
            "parameter_reordered",
            "PARAMETERS REORDERED",
            positional_break,
            "high",
            (
                "Python positional parameter order changed"
                if positional_break
                else "Python keyword-only parameter order changed"
            ),
        )

    syntax_assessments: list[SignatureAssessment] = []
    for name in old_names:
        old_param = old_by_name[name]
        new_param = new_by_name[name]
        old_default = old_param.default
        new_default = new_param.default
        if old_default is None and new_default is not None:
            syntax_assessments.append(
                _assessment(
                    "DG106",
                    "default_added",
                    "DEFAULT ADDED",
                    False,
                    "high",
                    f"Default added to Python parameter '{name}'",
                )
            )
        elif old_default != new_default:
            syntax_assessments.append(
                _assessment(
                    "DG105",
                    "default_changed",
                    "DEFAULT VALUE CHANGED",
                    False,
                    "high",
                    f"Default changed for Python parameter '{name}'",
                    "Runtime behavior can change even though omitted-argument calls remain valid",
                )
            )
        if old_param.annotation != new_param.annotation:
            syntax_assessments.append(
                _assessment(
                    "DG107",
                    "parameter_annotation_changed",
                    "PARAMETER ANNOTATION CHANGED",
                    None,
                    "high",
                    f"Annotation syntax changed for Python parameter '{name}'",
                    "Python annotations alone do not prove runtime or type-checker compatibility",
                )
            )

    if old.return_annotation != new.return_annotation:
        syntax_assessments.append(
            _assessment(
                "DG108",
                "return_annotation_changed",
                "RETURN ANNOTATION CHANGED",
                None,
                "high",
                "Python return annotation syntax changed",
                "Annotation syntax does not prove runtime or type-checker compatibility",
            )
        )
    if old.decorators != new.decorators:
        syntax_assessments.append(
            _assessment(
                "DG110",
                "decorator_changed",
                "DECORATOR CHANGED",
                None,
                "high",
                "Python decorator syntax changed",
                "Decorator runtime behavior and call compatibility were not evaluated",
            )
        )
    if old.type_parameters != new.type_parameters:
        syntax_assessments.append(
            _assessment(
                "DG110",
                "type_parameters_changed",
                "TYPE PARAMETERS CHANGED",
                None,
                "high",
                "Python type-parameter syntax changed",
                "Type-checker compatibility was not evaluated",
            )
        )
    if syntax_assessments:
        return _combine_python_syntax_assessments(syntax_assessments)
    return _assessment(
        "DG110",
        "signature_changed",
        "SIGNATURE CHANGED",
        None,
        "medium",
        "Python signature syntax changed outside the bounded call-shape rules",
        "Compatibility was not proven for this syntax",
    )


def assess_signature_change(
    old_signature: str,
    new_signature: str,
    language: str,
) -> SignatureAssessment:
    """Classify a signature diff without claiming compiler-grade semantics.

    Python receives bounded call-shape rules. TypeScript, JavaScript, and Go
    report syntactic parameter/return evidence with unknown compatibility;
    DiffGuard does not run a compiler or resolve overloads, interfaces, or
    dynamic dispatch.
    """
    # Class heritage may itself contain calls, for example
    # ``class Service extends mixin(Base, Trait)``. Classify the declaration
    # before callable parameter extraction so commas inside heritage syntax
    # cannot become fabricated parameter additions or removals.
    if _is_class_declaration(old_signature, language) or _is_class_declaration(
        new_signature, language
    ):
        is_python = language == "python"
        evidence = (
            "Python class base/decorator syntax changed"
            if is_python
            else f"{language} class declaration/heritage syntax changed"
        )
        gap = (
            "Class compatibility requires inheritance, metaclass, and runtime analysis"
            if is_python
            else f"{language} class compatibility requires inheritance, type, and runtime analysis"
        )
        return _assessment(
            "DG110",
            "class_signature_changed",
            "CLASS SIGNATURE CHANGED",
            None,
            "high",
            evidence,
            gap,
        )
    if language == "python":
        primary = _python_signature_assessment(old_signature, new_signature)
        old_python = _parse_python_signature(old_signature)
        new_python = _parse_python_signature(new_signature)
        if old_python is None or new_python is None:
            return primary
        return _merge_python_primary_and_syntax(
            primary,
            _python_retained_syntax_assessments(old_python, new_python),
        )

    old_params = extract_params(old_signature, language)
    new_params = extract_params(new_signature, language)
    old_normalized_params = _normalized_non_python_params(old_params, language)
    new_normalized_params = _normalized_non_python_params(new_params, language)
    gap = f"{language} compatibility requires compiler/type resolution not performed here"
    if len(new_params) < len(old_params):
        return _assessment(
            "DG101",
            "parameter_removed",
            "PARAMETER REMOVED",
            None,
            "medium",
            f"{language} parameter-list arity decreased syntactically",
            gap,
        )
    if len(new_params) > len(old_params):
        return _assessment(
            "DG102",
            "parameter_added",
            "PARAMETER ADDED",
            None,
            "medium",
            f"{language} parameter-list arity increased syntactically",
            gap,
        )
    if old_normalized_params != new_normalized_params:
        old_names = [_param_name(p) for p in old_params]
        new_names = [_param_name(p) for p in new_params]
        category_id = (
            "parameter_reordered"
            if old_names != new_names and sorted(old_names) == sorted(new_names)
            else "parameters_changed"
        )
        category = (
            "PARAMETERS REORDERED" if category_id == "parameter_reordered" else "PARAMETERS CHANGED"
        )
        rule_id = "DG103" if category_id == "parameter_reordered" else "DG110"
        return _assessment(
            rule_id,
            category_id,
            category,
            None,
            "medium",
            f"{language} parameter syntax changed",
            gap,
        )
    if _normalized_non_python_signature_tail(
        old_signature, language
    ) != _normalized_non_python_signature_tail(new_signature, language):
        return _assessment(
            "DG108",
            "return_type_changed",
            "RETURN TYPE CHANGED",
            None,
            "medium",
            f"{language} return-type syntax changed",
            gap,
        )
    return _assessment(
        "DG110",
        "signature_changed",
        "SIGNATURE CHANGED",
        None,
        "low",
        f"{language} signature syntax changed",
        gap,
    )


def compare_signatures(
    old_signature: str,
    new_signature: str,
    language: str,
) -> SignatureComparison:
    """Compare signatures, separating equivalence from assessed changes.

    Python declarations are parsed before comparison. TypeScript, JavaScript,
    and Go use conservative lexical tokens so inter-token whitespace does not
    become a finding while literals, templates, operators, and syntax changes
    remain visible.
    """
    if old_signature == new_signature:
        return SignatureComparison()
    if language == "python" and _python_signatures_equivalent(old_signature, new_signature):
        return SignatureComparison()
    if language in _NON_PYTHON_NORMALIZED_LANGUAGES and _non_python_signature_tokens(
        old_signature, language
    ) == _non_python_signature_tokens(new_signature, language):
        return SignatureComparison()
    return SignatureComparison(
        assessment=assess_signature_change(old_signature, new_signature, language)
    )


def classify_signature_change(old_signature: str, new_signature: str) -> str:
    """Return the bounded Python category label for a signature change."""
    return assess_signature_change(old_signature, new_signature, "python").category


def is_breaking_change(old_signature: str, new_signature: str) -> bool:
    """Return whether a bounded Python call-shape rule proves breakage.

    Annotation and default-value changes can be important findings without
    proving that calls become invalid. Unknown compatibility returns ``False``
    through this legacy boolean helper; use :func:`assess_signature_change` for
    the tri-state result and evidence.
    """
    return assess_signature_change(old_signature, new_signature, "python").breaking is True
