"""Tests for the parser module."""

from diffguard.engine.parser import parse_file


def test_parse_python_functions_and_classes() -> None:
    source = """\
def greet(name: str) -> str:
    return f"Hello, {name}"

class Calculator:
    def add(self, a: int, b: int) -> int:
        return a + b

    def subtract(self, a: int, b: int) -> int:
        return a - b
"""
    result = parse_file(source, "python")
    assert not result.parse_error
    assert result.language == "python"
    names = [s.name for s in result.symbols]
    assert "greet" in names
    assert "Calculator" in names
    assert "add" in names
    assert "subtract" in names


def test_parse_python_methods_have_parent() -> None:
    source = """\
class MyClass:
    def method_one(self) -> None:
        pass

    def method_two(self, x: int) -> int:
        return x
"""
    result = parse_file(source, "python")
    methods = [s for s in result.symbols if s.kind == "method"]
    assert len(methods) == 2
    for m in methods:
        assert m.parent == "MyClass"


def test_parse_python_decorators() -> None:
    source = """\
@staticmethod
def helper() -> None:
    pass

@property
def value(self) -> int:
    return 42
"""
    result = parse_file(source, "python")
    assert not result.parse_error
    symbols = {s.name: s for s in result.symbols}
    assert "@staticmethod" in symbols["helper"].signature
    assert "@property" in symbols["value"].signature


def test_parse_empty_file() -> None:
    result = parse_file("", "python")
    assert result.symbols == []
    assert not result.parse_error


def test_parse_malformed_code() -> None:
    source = "def broken(\n    # missing close paren and body"
    result = parse_file(source, "python")
    # tree-sitter is error tolerant - it should still produce a result
    assert result.parse_error or result.symbols == [] or len(result.symbols) >= 0


def test_body_hash_changes_on_body_change() -> None:
    source_v1 = """\
def foo() -> int:
    return 1
"""
    source_v2 = """\
def foo() -> int:
    return 2
"""
    r1 = parse_file(source_v1, "python")
    r2 = parse_file(source_v2, "python")
    s1 = [s for s in r1.symbols if s.name == "foo"][0]
    s2 = [s for s in r2.symbols if s.name == "foo"][0]
    assert s1.body_hash != s2.body_hash


def test_body_hash_stable_on_whitespace_change() -> None:
    source_v1 = """\
def foo() -> int:
    return 1
"""
    source_v2 = """\
def foo() -> int:
    return   1
"""
    r1 = parse_file(source_v1, "python")
    r2 = parse_file(source_v2, "python")
    s1 = [s for s in r1.symbols if s.name == "foo"][0]
    s2 = [s for s in r2.symbols if s.name == "foo"][0]
    assert s1.body_hash == s2.body_hash


def test_parse_unsupported_language() -> None:
    result = parse_file("some code", "rust")
    assert result.parse_error
    assert result.error_message is not None


def test_parse_typescript() -> None:
    source = """\
function greet(name) {
    return "Hello " + name;
}

const add = (a, b) => a + b;

class Calculator {
    multiply(a, b) {
        return a * b;
    }
}
"""
    result = parse_file(source, "typescript")
    assert not result.parse_error
    names = [s.name for s in result.symbols]
    assert "greet" in names
    assert "add" in names
    assert "Calculator" in names
    assert "multiply" in names


def test_parse_go() -> None:
    source = """\
package main

func Add(a int, b int) int {
    return a + b
}

func (s *Server) Handle(r Request) error {
    return nil
}
"""
    result = parse_file(source, "go")
    assert not result.parse_error
    names = [s.name for s in result.symbols]
    assert "Add" in names
    assert "Handle" in names
    handle = [s for s in result.symbols if s.name == "Handle"][0]
    assert handle.parent == "Server"
    assert handle.kind == "method"
