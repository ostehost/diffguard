"""Tests for TypeScript/JavaScript language extraction."""

import pytest

from diffguard.engine.parser import parse_file


def test_function_declaration() -> None:
    source = """\
function greet(name) {
    return "Hello " + name;
}
"""
    result = parse_file(source, "javascript")
    assert len(result.symbols) == 1
    sym = result.symbols[0]
    assert sym.name == "greet"
    assert sym.kind == "function"
    assert "function greet(name)" in sym.signature


def test_arrow_function_const() -> None:
    source = """\
const add = (a, b) => {
    return a + b;
};
"""
    result = parse_file(source, "javascript")
    assert len(result.symbols) == 1
    sym = result.symbols[0]
    assert sym.name == "add"
    assert sym.kind == "function"
    assert "const add = (a, b) =>" in sym.signature


def test_class_with_methods() -> None:
    source = """\
class Calculator {
    add(a, b) {
        return a + b;
    }

    subtract(a, b) {
        return a - b;
    }
}
"""
    result = parse_file(source, "javascript")
    cls = [s for s in result.symbols if s.kind == "class"]
    assert len(cls) == 1
    assert cls[0].name == "Calculator"

    methods = [s for s in result.symbols if s.kind == "method"]
    assert len(methods) == 2
    for m in methods:
        assert m.parent == "Calculator"


def test_exported_function() -> None:
    source = """\
export function handle(req) {
    return req;
}
"""
    result = parse_file(source, "javascript")
    assert len(result.symbols) == 1
    sym = result.symbols[0]
    assert sym.name == "handle"
    assert sym.kind == "function"
    assert sym.signature == "export function handle(req)"


@pytest.mark.parametrize("language", ["javascript", "typescript"])
@pytest.mark.parametrize(
    ("source", "expected_signature"),
    [
        ("export function contract(value) {}", "export function contract(value)"),
        (
            "export default function contract(value) {}",
            "export default function contract(value)",
        ),
        ("export class Contract {}", "export class Contract"),
        ("export default class Contract {}", "export default class Contract"),
        (
            "export const contract = (value) => value;",
            "export const contract = (value) =>",
        ),
        (
            "export default async function* contract(value) { yield value; }",
            "export default async function* contract(value)",
        ),
    ],
)
def test_export_modifiers_are_part_of_declaration_signatures(
    language: str,
    source: str,
    expected_signature: str,
) -> None:
    result = parse_file(source, language)

    assert result.parse_error is False
    assert len(result.symbols) == 1
    assert result.symbols[0].signature == expected_signature


def test_typescript_export_wrapper_precedes_abstract_class_modifier() -> None:
    result = parse_file("export abstract class Contract {}", "typescript")

    assert result.parse_error is False
    assert len(result.symbols) == 1
    assert result.symbols[0].signature == "export abstract class Contract"


def test_typescript_return_types_are_part_of_signatures() -> None:
    source = """\
function convert(value: number): string { return String(value); }
const arrow = (value: number): string => String(value);
class Converter { convert(value: number): string { return String(value); } }
"""
    result = parse_file(source, "typescript")
    signatures = {symbol.name: symbol.signature for symbol in result.symbols}
    assert signatures["convert"].endswith(": string")
    assert signatures["arrow"].endswith(": string =>")
    method = next(symbol for symbol in result.symbols if symbol.kind == "method")
    assert method.signature.endswith(": string")


def test_callable_modifiers_are_part_of_signatures() -> None:
    source = """\
async function fetchValue(): Promise<number> { return 1; }
const fetchArrow = async (): Promise<number> => 1;
abstract class Service {
    protected abstract load(): Promise<number>;
    private static async refresh(): Promise<number> { return 1; }
    get value(): number { return 1; }
    set value(next: number) {}
}
"""
    result = parse_file(source, "typescript")
    signatures = [symbol.signature for symbol in result.symbols]

    assert "async function fetchValue(): Promise<number>" in signatures
    assert "const fetchArrow = async (): Promise<number> =>" in signatures
    assert "protected abstract load(): Promise<number>" in signatures
    assert "private static async refresh(): Promise<number>" in signatures
    assert "get value(): number" in signatures
    assert "set value(next: number)" in signatures


def test_type_parameters_and_method_markers_are_part_of_signatures() -> None:
    source = """\
function identity<T extends string>(value: T): T { return value; }
const arrow = <T extends number>(value: T): T => value;
class Box<T extends string> {
    override map<U>(value: U): U { return value; }
}
abstract class Service<T> {
    abstract load?<U>(): U;
}
"""
    result = parse_file(source, "typescript")
    signatures = {symbol.name: symbol.signature for symbol in result.symbols}

    assert signatures["identity"] == "function identity<T extends string>(value: T): T"
    assert signatures["arrow"] == "const arrow = <T extends number>(value: T): T =>"
    assert signatures["Box"] == "class Box<T extends string>"
    assert signatures["map"] == "override map<U>(value: U): U"
    assert signatures["Service"] == "abstract class Service<T>"
    assert signatures["load"] == "abstract load?<U>(): U"


def test_class_heritage_call_syntax_is_preserved() -> None:
    source = """\
class Single extends mixin(Base) {}
class Multiple extends mixin(Base, Trait) {}
class Plain extends Base {}
"""

    for language in ("typescript", "javascript"):
        result = parse_file(source, language)
        signatures = {symbol.name: symbol.signature for symbol in result.symbols}

        assert signatures["Single"] == "class Single extends mixin(Base)"
        assert signatures["Multiple"] == "class Multiple extends mixin(Base, Trait)"
        assert signatures["Plain"] == "class Plain extends Base"
