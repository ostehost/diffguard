"""Tests for symbol matching."""

from diffguard.engine._types import Symbol
from diffguard.engine.matcher import match_cross_file, match_symbols


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


class TestMatchSymbols:
    def test_identical_lists(self) -> None:
        syms = [_sym(name="a"), _sym(name="b", signature="def b()")]
        result = match_symbols(syms, syms)
        assert len(result) == 2
        assert all(m.old is not None and m.new is not None for m in result)

    def test_added_symbols(self) -> None:
        old = [_sym(name="a")]
        new = [_sym(name="a"), _sym(name="b", signature="def b()")]
        result = match_symbols(old, new)
        added = [m for m in result if m.old is None]
        assert len(added) == 1
        assert added[0].new is not None
        assert added[0].new.name == "b"

    def test_removed_symbols(self) -> None:
        old = [_sym(name="a"), _sym(name="b", signature="def b()")]
        new = [_sym(name="a")]
        result = match_symbols(old, new)
        removed = [m for m in result if m.new is None]
        assert len(removed) == 1
        assert removed[0].old is not None
        assert removed[0].old.name == "b"

    def test_modified_symbols(self) -> None:
        old = [_sym(name="a", body_hash="old")]
        new = [_sym(name="a", body_hash="new")]
        result = match_symbols(old, new)
        assert len(result) == 1
        assert result[0].old is not None and result[0].new is not None
        assert result[0].old.body_hash != result[0].new.body_hash

    def test_duplicate_names_different_classes(self) -> None:
        old = [
            _sym(name="run", parent="Dog", signature="def run(self)"),
            _sym(name="run", parent="Cat", signature="def run(self)"),
        ]
        new = [
            _sym(name="run", parent="Dog", signature="def run(self)"),
            _sym(name="run", parent="Cat", signature="def run(self)"),
        ]
        result = match_symbols(old, new)
        assert len(result) == 2
        assert all(m.old is not None and m.new is not None for m in result)

    def test_duplicate_same_key_signature_match(self) -> None:
        """Two overloads with same (name, kind, parent) but different sigs."""
        old = [
            _sym(name="foo", signature="def foo(a: int)", body_hash="h1"),
            _sym(name="foo", signature="def foo(a: str)", body_hash="h2"),
        ]
        new = [
            _sym(name="foo", signature="def foo(a: str)", body_hash="h2"),
            _sym(name="foo", signature="def foo(a: int)", body_hash="h1"),
        ]
        result = match_symbols(old, new)
        assert len(result) == 2
        for m in result:
            assert m.old is not None and m.new is not None
            assert m.old.signature == m.new.signature


class TestMatchCrossFile:
    def test_cross_file_move(self) -> None:
        sym_old = _sym(name="helper", body_hash="h1")
        sym_new = _sym(name="helper", body_hash="h2")
        result = match_cross_file(
            {"old.py": [sym_old]},
            {"new.py": [sym_new]},
        )
        assert len(result) == 1
        assert result[0].file_from == "old.py"
        assert result[0].old == sym_old
        assert result[0].new == sym_new

    def test_cross_file_no_false_positive_same_name(self) -> None:
        """Regression: unrelated functions with the same name should NOT match as moves."""
        old_sym = _sym(name="helper", signature="def helper()", body_hash="aaa")
        new_sym = _sym(name="helper", signature="def helper(x: int)", body_hash="bbb")
        result = match_cross_file(
            {"utils.py": [old_sym]},
            {"api.py": [new_sym]},
        )
        assert len(result) == 0, "Different signature AND body_hash should not match as move"

    def test_cross_file_move_same_signature(self) -> None:
        """Move detected when signature matches even if body_hash differs."""
        old_sym = _sym(name="helper", signature="def helper(x: int)", body_hash="aaa")
        new_sym = _sym(name="helper", signature="def helper(x: int)", body_hash="bbb")
        result = match_cross_file(
            {"utils.py": [old_sym]},
            {"api.py": [new_sym]},
        )
        assert len(result) == 1

    def test_cross_file_move_same_body_hash(self) -> None:
        """Move detected when body_hash matches even if signature differs."""
        old_sym = _sym(name="helper", signature="def helper()", body_hash="same")
        new_sym = _sym(name="helper", signature="def helper() -> None", body_hash="same")
        result = match_cross_file(
            {"utils.py": [old_sym]},
            {"api.py": [new_sym]},
        )
        assert len(result) == 1

    def test_no_cross_file_same_file(self) -> None:
        sym = _sym(name="helper")
        result = match_cross_file(
            {"a.py": [sym]},
            {"a.py": [sym]},
        )
        assert len(result) == 0
