"""Tests for signature comparison."""

from diffguard.engine.signatures import extract_params, is_breaking_change


class TestExtractParams:
    def test_simple(self) -> None:
        assert extract_params("def foo(a, b)") == ["a", "b"]

    def test_empty(self) -> None:
        assert extract_params("def foo()") == []

    def test_self_filtered(self) -> None:
        assert extract_params("def foo(self, a)") == ["a"]

    def test_typed(self) -> None:
        assert extract_params("def foo(a: int, b: str)") == ["a: int", "b: str"]

    def test_defaults(self) -> None:
        params = extract_params('def foo(a: int, b: str = "x")')
        assert len(params) == 2

    def test_nested_parens_callable(self) -> None:
        """Regression: nested brackets/parens in Callable types."""
        params = extract_params("def foo(a: Callable[[int], str], b: int)")
        assert params == ["a: Callable[[int], str]", "b: int"]

    def test_nested_parens_complex(self) -> None:
        params = extract_params("def foo(a: dict[str, list[int]], b: tuple[int, ...])")
        assert params == ["a: dict[str, list[int]]", "b: tuple[int, ...]"]

    def test_return_type_complex(self) -> None:
        """Regression: complex return types should not be truncated."""
        from diffguard.engine.signatures import _extract_return_type
        assert _extract_return_type("def foo() -> dict[str, int]") == "dict[str, int]"


    def test_dict_literal_default(self) -> None:
        params = extract_params('def foo(x: dict = {"a": 1, "b": 2}, y: int = 0)')
        assert params == ['x: dict = {"a": 1, "b": 2}', "y: int = 0"]


class TestIsBreakingChange:
    def test_identical(self) -> None:
        assert is_breaking_change("def foo(a: int)", "def foo(a: int)") is False

    def test_param_added_no_default(self) -> None:
        assert is_breaking_change("def foo(a: int)", "def foo(a: int, b: str)") is True

    def test_param_removed(self) -> None:
        assert is_breaking_change("def foo(a: int, b: str)", "def foo(a: int)") is True

    def test_param_type_changed(self) -> None:
        assert is_breaking_change("def foo(a: int)", "def foo(a: str)") is True

    def test_param_added_with_default(self) -> None:
        assert is_breaking_change("def foo(a: int)", 'def foo(a: int, b: str = "x")') is False

    def test_return_type_changed(self) -> None:
        assert is_breaking_change("def foo(a: int) -> int", "def foo(a: int) -> str") is True

    def test_return_type_added(self) -> None:
        # Adding a return type when there was none — conservative, not breaking
        assert is_breaking_change("def foo(a: int)", "def foo(a: int) -> int") is False
