"""Tests for Go language extraction."""

from diffguard.engine.parser import parse_file


def test_standalone_function() -> None:
    source = """\
package main

func Add(a int, b int) int {
    return a + b
}
"""
    result = parse_file(source, "go")
    assert len(result.symbols) == 1
    sym = result.symbols[0]
    assert sym.name == "Add"
    assert sym.kind == "function"
    assert sym.parent is None
    assert "func Add(a int, b int) int" in sym.signature


def test_method_with_receiver() -> None:
    source = """\
package main

func (s *Server) Handle(r Request) error {
    return nil
}
"""
    result = parse_file(source, "go")
    assert len(result.symbols) == 1
    sym = result.symbols[0]
    assert sym.name == "Handle"
    assert sym.kind == "method"
    assert sym.parent == "Server"
    assert "(s *Server)" in sym.signature


def test_multiple_return_types() -> None:
    source = """\
package main

func Divide(a int, b int) (int, error) {
    if b == 0 {
        return 0, fmt.Errorf("division by zero")
    }
    return a / b, nil
}
"""
    result = parse_file(source, "go")
    assert len(result.symbols) == 1
    sym = result.symbols[0]
    assert sym.name == "Divide"
    assert "(int, error)" in sym.signature


def test_type_parameters_are_part_of_function_signatures() -> None:
    source = """\
package main

func Identity[T ~string](value T) T {
    return value
}
"""
    result = parse_file(source, "go")

    assert len(result.symbols) == 1
    assert result.symbols[0].signature == "func Identity[T ~string](value T) T"


def test_generic_receiver_syntax_and_parent_are_preserved() -> None:
    source = """\
package main

type Box[T any] struct { value T }

func (box *Box[T]) Get() T {
    return box.value
}
"""
    result = parse_file(source, "go")

    assert len(result.symbols) == 1
    assert result.symbols[0].signature == "func (box *Box[T]) Get() T"
    assert result.symbols[0].parent == "Box"
