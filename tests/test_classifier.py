"""Tests for change classification."""

from diffguard.engine._types import Symbol
from diffguard.engine.classifier import classify_changes
from diffguard.engine.matcher import MatchedSymbol
from diffguard.schema import SymbolChange


def _sym(
    name: str = "foo",
    kind: str = "function",
    signature: str = "def foo()",
    start_line: int = 1,
    end_line: int = 5,
    body_hash: str = "abc123",
    parent: str | None = None,
) -> Symbol:
    return Symbol(
        name=name,
        kind=kind,
        signature=signature,
        start_line=start_line,
        end_line=end_line,
        body_hash=body_hash,
        parent=parent,
    )


class TestClassifyChanges:
    def test_function_added(self) -> None:
        matches = [MatchedSymbol(old=None, new=_sym())]
        result = classify_changes(matches)
        assert len(result) == 1
        assert result[0].kind == "function_added"
        assert result[0].name == "foo"

    def test_function_removed(self) -> None:
        matches = [MatchedSymbol(old=_sym(), new=None)]
        result = classify_changes(matches)
        assert len(result) == 1
        assert result[0].kind == "function_removed"

    def test_class_added(self) -> None:
        matches = [MatchedSymbol(old=None, new=_sym(kind="class", name="Foo"))]
        result = classify_changes(matches)
        assert result[0].kind == "class_added"

    def test_class_removed(self) -> None:
        matches = [MatchedSymbol(old=_sym(kind="class", name="Foo"), new=None)]
        result = classify_changes(matches)
        assert result[0].kind == "class_removed"

    def test_function_modified(self) -> None:
        matches = [
            MatchedSymbol(
                old=_sym(body_hash="old"),
                new=_sym(body_hash="new"),
            )
        ]
        result = classify_changes(matches)
        assert len(result) == 1
        assert result[0].kind == "function_modified"

    def test_signature_changed(self) -> None:
        matches = [
            MatchedSymbol(
                old=_sym(signature="def foo(a: int)", body_hash="old"),
                new=_sym(signature="def foo(a: int, b: str)", body_hash="new"),
            )
        ]
        result = classify_changes(matches)
        assert len(result) == 1
        assert result[0].kind == "signature_changed"
        assert result[0].breaking is True

    def test_unchanged_excluded(self) -> None:
        matches = [
            MatchedSymbol(
                old=_sym(body_hash="same"),
                new=_sym(body_hash="same"),
            )
        ]
        result = classify_changes(matches)
        assert len(result) == 0

    def test_moved_symbol(self) -> None:
        matches = [
            MatchedSymbol(
                old=_sym(body_hash="h1"),
                new=_sym(body_hash="h2"),
                file_from="old.py",
            )
        ]
        result = classify_changes(matches)
        assert len(result) == 1
        assert result[0].kind == "moved"
        assert result[0].file_from == "old.py"

    def test_new_kwarg_with_default_not_breaking(self) -> None:
        """Adding a keyword-only arg with a default is NOT breaking."""
        matches = [
            MatchedSymbol(
                old=_sym(signature="def foo(a, b)", body_hash="old"),
                new=_sym(signature="def foo(a, b, *, new_kwarg=None)", body_hash="new"),
            )
        ]
        result = classify_changes(matches)
        assert len(result) == 1
        assert result[0].kind == "signature_changed"
        assert result[0].breaking is False

    def test_default_value_change_is_breaking(self) -> None:
        """Changing a default value IS breaking."""
        matches = [
            MatchedSymbol(
                old=_sym(signature="def redirect(location, code=302)", body_hash="old"),
                new=_sym(signature="def redirect(location, code=303)", body_hash="new"),
            )
        ]
        result = classify_changes(matches)
        assert len(result) == 1
        assert result[0].kind == "signature_changed"
        assert result[0].breaking is True

    def test_new_positional_without_default_is_breaking(self) -> None:
        """Adding a new positional arg without a default IS breaking."""
        matches = [
            MatchedSymbol(
                old=_sym(signature="def foo(a, b)", body_hash="old"),
                new=_sym(signature="def foo(a, b, c)", body_hash="new"),
            )
        ]
        result = classify_changes(matches)
        assert len(result) == 1
        assert result[0].breaking is True

    def test_new_positional_with_default_not_breaking(self) -> None:
        """Adding a new positional arg with a default is NOT breaking."""
        matches = [
            MatchedSymbol(
                old=_sym(signature="def foo(a)", body_hash="old"),
                new=_sym(signature="def foo(a, b=None)", body_hash="new"),
            )
        ]
        result = classify_changes(matches)
        assert len(result) == 1
        assert result[0].breaking is False

    def test_output_is_symbol_change(self) -> None:
        matches = [MatchedSymbol(old=None, new=_sym())]
        result = classify_changes(matches)
        assert all(isinstance(c, SymbolChange) for c in result)
