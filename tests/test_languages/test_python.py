"""Tests for Python language extraction."""

from diffguard.engine.parser import parse_file


def test_standalone_function() -> None:
    source = """\
def greet(name: str) -> str:
    return f"Hello, {name}"
"""
    result = parse_file(source, "python")
    assert len(result.symbols) == 1
    sym = result.symbols[0]
    assert sym.name == "greet"
    assert sym.kind == "function"
    assert sym.parent is None
    assert "def greet(name: str) -> str" in sym.signature


def test_class_with_methods() -> None:
    source = """\
class Animal:
    def __init__(self, name: str) -> None:
        self.name = name

    def speak(self) -> str:
        return "..."
"""
    result = parse_file(source, "python")
    cls = [s for s in result.symbols if s.kind == "class"]
    assert len(cls) == 1
    assert cls[0].name == "Animal"

    methods = [s for s in result.symbols if s.kind == "method"]
    assert len(methods) == 2
    names = {m.name for m in methods}
    assert names == {"__init__", "speak"}
    for m in methods:
        assert m.parent == "Animal"


def test_decorated_function() -> None:
    source = """\
@app.route("/")
def index() -> str:
    return "hello"
"""
    result = parse_file(source, "python")
    assert len(result.symbols) == 1
    sym = result.symbols[0]
    assert sym.name == "index"
    assert "@app.route" in sym.signature
    assert "def index()" in sym.signature


def test_async_modifier_is_part_of_function_and_method_signatures() -> None:
    source = """\
async def fetch() -> str:
    return "value"

class Client:
    async def fetch(self) -> str:
        return "value"
"""
    result = parse_file(source, "python")

    function = next(symbol for symbol in result.symbols if symbol.kind == "function")
    method = next(symbol for symbol in result.symbols if symbol.kind == "method")
    assert function.signature == "async def fetch() -> str"
    assert method.signature == "async def fetch(self) -> str"


def test_type_parameters_are_part_of_function_and_class_signatures() -> None:
    source = """\
def identity[T: str](value: T) -> T:
    return value

class Box[T: str](Base):
    pass
"""
    result = parse_file(source, "python")

    function = next(symbol for symbol in result.symbols if symbol.kind == "function")
    cls = next(symbol for symbol in result.symbols if symbol.kind == "class")
    assert function.signature == "def identity[T: str](value: T) -> T"
    assert cls.signature == "class Box[T: str](Base)"


def test_nested_functions() -> None:
    source = """\
def outer() -> None:
    def inner() -> None:
        pass
    inner()
"""
    result = parse_file(source, "python")
    names = [s.name for s in result.symbols]
    assert "outer" in names
    assert "inner" in names


def test_signature_accuracy() -> None:
    source = """\
def complex_func(a: int, b: str = "hello", *args: float, **kwargs: bool) -> list[int]:
    return []
"""
    result = parse_file(source, "python")
    sym = result.symbols[0]
    assert "a: int" in sym.signature
    assert 'b: str = "hello"' in sym.signature
    assert "*args: float" in sym.signature
    assert "**kwargs: bool" in sym.signature
    assert "-> list[int]" in sym.signature
