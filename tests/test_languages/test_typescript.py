"""Tests for TypeScript/JavaScript language extraction."""

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
