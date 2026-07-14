"""Tests for change classification."""

from diffguard.engine._types import MatchedSymbol, Symbol
from diffguard.engine.classifier import classify_changes as _classify_changes
from diffguard.engine.signatures import compare_signatures
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


def classify_changes(matches: list[MatchedSymbol]) -> list[SymbolChange]:
    """Classify test matches with the Python signature policy."""
    return _classify_changes(
        matches,
        lambda old, new: compare_signatures(old, new, "python"),
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

    def test_required_positional_demand_survives_keyword_only_default_addition(self) -> None:
        matches = [
            MatchedSymbol(
                old=_sym(signature="def foo(*, option)", body_hash="same"),
                new=_sym(signature="def foo(required, *, option=1)", body_hash="same"),
            )
        ]

        result = classify_changes(matches)

        assert len(result) == 1
        assert result[0].kind == "signature_changed"
        assert result[0].rule_id == "DG102"
        assert result[0].category_id == "required_parameter_added"
        assert result[0].breaking is True

    def test_default_value_change_is_behavioral_not_call_shape_breaking(self) -> None:
        """Changing a default remains high-signal without inventing a call break."""
        matches = [
            MatchedSymbol(
                old=_sym(signature="def redirect(location, code=302)", body_hash="old"),
                new=_sym(signature="def redirect(location, code=303)", body_hash="new"),
            )
        ]
        result = classify_changes(matches)
        assert len(result) == 1
        assert result[0].kind == "signature_changed"
        assert result[0].breaking is False
        assert result[0].category_id == "default_changed"

    def test_pure_default_removal_is_detected_before_equal_body_hash(self) -> None:
        matches = [
            MatchedSymbol(
                old=_sym(signature="def foo(a=1)", body_hash="same"),
                new=_sym(signature="def foo(a)", body_hash="same"),
            )
        ]
        result = classify_changes(matches)
        assert len(result) == 1
        assert result[0].category_id == "default_removed"
        assert result[0].breaking is True

    def test_pep695_default_removal_is_detected_before_equal_body_hash(self) -> None:
        matches = [
            MatchedSymbol(
                old=_sym(signature="def foo[T: str](value: T = 1)", body_hash="same"),
                new=_sym(signature="def foo[T: str](value: T)", body_hash="same"),
            )
        ]

        result = classify_changes(matches)

        assert len(result) == 1
        assert result[0].category_id == "default_removed"
        assert result[0].breaking is True

    def test_pep695_formatting_only_signature_falls_through_to_body_change(self) -> None:
        matches = [
            MatchedSymbol(
                old=_sym(
                    signature="def foo[T: (str, bytes)](value:T=1)",
                    body_hash="old",
                ),
                new=_sym(
                    signature="def foo[ T : ( str , bytes ) ]( value : T = 1 )",
                    body_hash="new",
                ),
            )
        ]

        result = classify_changes(matches)

        assert len(result) == 1
        assert result[0].kind == "function_modified"

    def test_default_removal_precedes_optional_parameter_addition(self) -> None:
        matches = [
            MatchedSymbol(
                old=_sym(signature="def foo(value=1)", body_hash="same"),
                new=_sym(signature="def foo(value, optional=2)", body_hash="same"),
            )
        ]

        result = classify_changes(matches)

        assert len(result) == 1
        assert result[0].rule_id == "DG104"
        assert result[0].category_id == "default_removed"
        assert result[0].breaking is True

    def test_parameter_kind_change_does_not_steal_variadic_capture(self) -> None:
        matches = [
            MatchedSymbol(
                old=_sym(signature="def foo(*args, value)", body_hash="same"),
                new=_sym(signature="def foo(value, *args)", body_hash="same"),
            )
        ]

        result = classify_changes(matches)

        assert len(result) == 1
        assert result[0].category_id == "parameter_kind_changed"
        assert result[0].breaking is True

    def test_variadic_addition_does_not_hide_parameter_kind_change(self) -> None:
        matches = [
            MatchedSymbol(
                old=_sym(signature="def foo(value)", body_hash="same"),
                new=_sym(signature="def foo(*args, value)", body_hash="same"),
            )
        ]

        result = classify_changes(matches)

        assert len(result) == 1
        assert result[0].category_id == "parameter_kind_changed"
        assert result[0].breaking is True

    def test_formatting_only_signature_edit_is_excluded(self) -> None:
        matches = [
            MatchedSymbol(
                old=_sym(signature="def foo(value:int=1)", body_hash="same"),
                new=_sym(signature="def foo( value: int = 1 )", body_hash="same"),
            )
        ]
        assert classify_changes(matches) == []

    def test_formatting_only_signature_edit_falls_through_to_body_change(self) -> None:
        matches = [
            MatchedSymbol(
                old=_sym(signature="def foo(value:int=1)", body_hash="old"),
                new=_sym(signature="def foo( value: int = 1 )", body_hash="new"),
            )
        ]
        result = classify_changes(matches)
        assert len(result) == 1
        assert result[0].kind == "function_modified"

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
