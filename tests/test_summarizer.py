"""Tests for the summarizer module."""

from __future__ import annotations

from diffguard.engine.summarizer import (
    _DETAILED_CAP,
    build_summary,
    build_tiered_summary,
    is_test_file,
)
from diffguard.schema import FileChange, SymbolChange


def _fc(path: str, changes: list[SymbolChange], **kwargs: object) -> FileChange:
    return FileChange(
        path=path, change_type="modified", language="python", changes=changes, **kwargs  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# is_test_file
# ---------------------------------------------------------------------------


class TestIsTestFile:
    def test_tests_dir(self) -> None:
        assert is_test_file("tests/test_foo.py")

    def test_test_prefix(self) -> None:
        assert is_test_file("test_foo.py")

    def test_test_suffix(self) -> None:
        assert is_test_file("foo_test.py")

    def test_spec_dir(self) -> None:
        assert is_test_file("spec/models/user_spec.ts")

    def test_js_test(self) -> None:
        assert is_test_file("src/utils.test.ts")

    def test_js_spec(self) -> None:
        assert is_test_file("src/utils.spec.jsx")

    def test_normal_file(self) -> None:
        assert not is_test_file("src/diffguard/engine/parser.py")

    def test_nested_tests(self) -> None:
        assert is_test_file("project/tests/unit/test_thing.py")

    def test_not_test(self) -> None:
        assert not is_test_file("src/testing_utils.py")


# ---------------------------------------------------------------------------
# build_summary (unchanged behaviour)
# ---------------------------------------------------------------------------


def test_build_summary_counts() -> None:
    files = [
        _fc("a.py", [
            SymbolChange(kind="function_added", name="foo"),
            SymbolChange(kind="function_added", name="bar"),
            SymbolChange(kind="function_modified", name="baz"),
        ]),
    ]
    s = build_summary(files)
    assert s.change_types["function_added"] == 2
    assert s.change_types["function_modified"] == 1
    assert len(s.breaking_changes) == 0


def test_build_summary_breaking() -> None:
    files = [
        _fc("a.py", [
            SymbolChange(
                kind="signature_changed", name="run",
                before_signature="def run(x: int) -> None",
                after_signature="def run(x: int, y: int) -> None",
                breaking=True,
            ),
        ]),
    ]
    s = build_summary(files)
    assert len(s.breaking_changes) == 1
    assert s.breaking_changes[0].name == "run"


def test_focus_max_five() -> None:
    changes = [SymbolChange(kind="function_added", name=f"fn{i}") for i in range(10)]
    files = [_fc("a.py", changes)]
    s = build_summary(files)
    assert len(s.focus) <= 5


def test_focus_breaking_first() -> None:
    files = [
        _fc("a.py", [
            SymbolChange(kind="function_added", name="foo"),
            SymbolChange(kind="signature_changed", name="bar", breaking=True,
                         before_signature="def bar()", after_signature="def bar(x: int)"),
        ]),
    ]
    s = build_summary(files)
    assert "BREAKING" in s.focus[0]


# ---------------------------------------------------------------------------
# Tiered summary — basic
# ---------------------------------------------------------------------------


def test_tiered_empty() -> None:
    t = build_tiered_summary([], build_summary([]))
    assert "No substantive" in t.oneliner


def test_tiered_oneliner_breaking() -> None:
    files = [
        _fc("a.py", [
            SymbolChange(kind="signature_changed", name="run", breaking=True,
                         before_signature="def run()", after_signature="def run(x: int)"),
        ]),
    ]
    s = build_summary(files)
    t = build_tiered_summary(files, s)
    assert "BREAKING" in t.oneliner


def test_tiered_short_refactor_only() -> None:
    files = [
        _fc("a.py", [
            SymbolChange(kind="function_modified", name="helper"),
        ]),
    ]
    s = build_summary(files)
    t = build_tiered_summary(files, s)
    assert "Refactor" in t.short or "modified" in t.short.lower()


def test_tiered_detailed_sections() -> None:
    files = [
        _fc("a.py", [
            SymbolChange(kind="function_added", name="new_fn"),
            SymbolChange(kind="function_removed", name="old_fn"),
        ]),
    ]
    s = build_summary(files)
    t = build_tiered_summary(files, s)
    assert "Added" in t.detailed
    assert "Removed" in t.detailed


# ---------------------------------------------------------------------------
# Test file filtering
# ---------------------------------------------------------------------------


def test_test_files_excluded_by_default() -> None:
    """Test files should not appear in short/detailed by default."""
    prod = _fc("src/core.py", [SymbolChange(kind="function_added", name="run")])
    test = _fc("tests/test_core.py", [SymbolChange(kind="function_added", name="test_run")])
    files = [prod, test]
    s = build_summary(files)
    t = build_tiered_summary(files, s, include_tests=False)
    assert "test_run" not in t.detailed
    assert "run" in t.detailed


def test_test_files_included_with_flag() -> None:
    """--include-tests shows test changes in a separate section."""
    prod = _fc("src/core.py", [SymbolChange(kind="function_added", name="run")])
    test = _fc("tests/test_core.py", [SymbolChange(kind="function_added", name="test_run")])
    files = [prod, test]
    s = build_summary(files)
    t = build_tiered_summary(files, s, include_tests=True)
    assert "Test Changes" in t.detailed
    assert "test_run" in t.detailed


def test_test_only_changes() -> None:
    """When only test files changed, oneliner/short should say so."""
    test = _fc("tests/test_core.py", [SymbolChange(kind="function_added", name="test_run")])
    files = [test]
    s = build_summary(files)
    t = build_tiered_summary(files, s, include_tests=False)
    assert "Test-only" in t.oneliner


# ---------------------------------------------------------------------------
# Detailed cap
# ---------------------------------------------------------------------------


def test_detailed_capped() -> None:
    """Detailed output should be capped at ~15 and show '(and N more)'."""
    changes = [SymbolChange(kind="function_added", name=f"fn{i}") for i in range(25)]
    files = [_fc("src/big.py", changes)]
    s = build_summary(files)
    t = build_tiered_summary(files, s)
    assert f"(and {25 - _DETAILED_CAP} more)" in t.detailed


def test_detailed_not_capped_when_under_limit() -> None:
    changes = [SymbolChange(kind="function_added", name=f"fn{i}") for i in range(5)]
    files = [_fc("src/small.py", changes)]
    s = build_summary(files)
    t = build_tiered_summary(files, s)
    assert "(and" not in t.detailed


# ---------------------------------------------------------------------------
# Skipped files
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Unsupported file warning
# ---------------------------------------------------------------------------


def test_unsupported_warning_in_short() -> None:
    """Short output should include ⚠ warning for unsupported files."""
    files = [
        _fc("src/core.py", [SymbolChange(kind="function_added", name="run")]),
        FileChange(path="lib.rs", change_type="modified", unsupported_language=True),
        FileChange(path="config.toml", change_type="modified", unsupported_language=True),
    ]
    s = build_summary(files)
    t = build_tiered_summary(files, s, show_skipped=False)
    assert "⚠ 2 files skipped" in t.short
    assert ".rs" in t.short
    assert ".toml" in t.short
    assert "review manually" in t.short


def test_unsupported_warning_in_detailed() -> None:
    """Detailed output should include ⚠ warning for unsupported files."""
    files = [
        _fc("src/core.py", [SymbolChange(kind="function_added", name="run")]),
        FileChange(path="lib.rs", change_type="modified", unsupported_language=True),
    ]
    s = build_summary(files)
    t = build_tiered_summary(files, s, show_skipped=False)
    assert "⚠ 1 file skipped" in t.detailed
    assert ".rs" in t.detailed


def test_unsupported_warning_not_in_oneliner() -> None:
    """Oneliner should NOT include the unsupported warning."""
    files = [
        _fc("src/core.py", [SymbolChange(kind="function_added", name="run")]),
        FileChange(path="lib.rs", change_type="modified", unsupported_language=True),
    ]
    s = build_summary(files)
    t = build_tiered_summary(files, s, show_skipped=False)
    assert "⚠" not in t.oneliner


def test_unsupported_warning_hidden_when_show_skipped() -> None:
    """When --show-skipped is set, no ⚠ warning (full list shown instead)."""
    files = [
        _fc("src/core.py", [SymbolChange(kind="function_added", name="run")]),
        FileChange(path="lib.rs", change_type="modified", unsupported_language=True),
    ]
    s = build_summary(files)
    t = build_tiered_summary(files, s, show_skipped=True)
    assert "⚠" not in t.short


def test_unsupported_warning_absent_when_no_unsupported() -> None:
    """No warning when all files are supported."""
    files = [_fc("src/core.py", [SymbolChange(kind="function_added", name="run")])]
    s = build_summary(files)
    t = build_tiered_summary(files, s, show_skipped=False)
    assert "⚠" not in t.short
    assert "⚠" not in t.detailed


# ---------------------------------------------------------------------------
# Skipped files
# ---------------------------------------------------------------------------


def test_skipped_hidden_by_default() -> None:
    files = [
        _fc("src/core.py", [SymbolChange(kind="function_added", name="run")]),
        FileChange(path="data.bin", change_type="modified", binary=True),
        FileChange(path="lib.rs", change_type="modified", unsupported_language=True),
    ]
    s = build_summary(files)
    t = build_tiered_summary(files, s, show_skipped=False)
    assert "Skipped" not in t.detailed


def test_skipped_shown_with_flag() -> None:
    files = [
        _fc("src/core.py", [SymbolChange(kind="function_added", name="run")]),
        FileChange(path="data.bin", change_type="modified", binary=True),
    ]
    s = build_summary(files)
    t = build_tiered_summary(files, s, show_skipped=True)
    assert "Skipped" in t.detailed
    assert "data.bin" in t.detailed


# ---------------------------------------------------------------------------
# Separate prod vs test in detailed
# ---------------------------------------------------------------------------


def test_detailed_prod_before_test() -> None:
    prod = _fc("src/core.py", [SymbolChange(kind="function_added", name="run")])
    test = _fc("tests/test_core.py", [SymbolChange(kind="function_added", name="test_run")])
    files = [prod, test]
    s = build_summary(files)
    t = build_tiered_summary(files, s, include_tests=True)
    # Prod section should come before Test Changes section
    added_pos = t.detailed.index("Added")
    test_pos = t.detailed.index("Test Changes")
    assert added_pos < test_pos
