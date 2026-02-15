"""Tests for the end-to-end pipeline."""

from __future__ import annotations

from diffguard.engine._types import Symbol, compute_body_hash
from diffguard.engine.matcher import MatchedSymbol
from diffguard.engine.pipeline import _apply_moves, run_pipeline
from diffguard.schema import FileChange, DiffGuardOutput, SymbolChange


SIMPLE_DIFF = """\
diff --git a/utils.py b/utils.py
--- a/utils.py
+++ b/utils.py
@@ -1,5 +1,8 @@
 def greet(name: str) -> str:
     return f"Hello {name}"
+
+def farewell(name: str) -> str:
+    return f"Goodbye {name}"
"""

OLD_UTILS = '''\
def greet(name: str) -> str:
    return f"Hello {name}"
'''

NEW_UTILS = '''\
def greet(name: str) -> str:
    return f"Hello {name}"

def farewell(name: str) -> str:
    return f"Goodbye {name}"
'''


def _content_provider(
    old_files: dict[str, str],
    new_files: dict[str, str],
    old_ref: str = "abc",
    new_ref: str = "def",
) -> object:
    def _get(ref: str, path: str) -> str | None:
        if ref == old_ref:
            return old_files.get(path)
        if ref == new_ref:
            return new_files.get(path)
        return None
    return _get


def test_simple_function_add() -> None:
    get = _content_provider({"utils.py": OLD_UTILS}, {"utils.py": NEW_UTILS})
    result = run_pipeline(SIMPLE_DIFF, "abc..def", get)  # type: ignore[arg-type]
    assert isinstance(result, DiffGuardOutput)
    assert result.meta.ref_range == "abc..def"
    assert result.meta.stats.files == 1
    assert len(result.files) == 1
    fc = result.files[0]
    assert fc.path == "utils.py"
    assert fc.language == "python"
    added = [c for c in fc.changes if c.kind == "function_added"]
    assert len(added) == 1
    assert added[0].name == "farewell"


def test_generated_file_skipped() -> None:
    diff = """\
diff --git a/package-lock.json b/package-lock.json
--- a/package-lock.json
+++ b/package-lock.json
@@ -1,3 +1,3 @@
-  "version": "1.0.0"
+  "version": "1.1.0"
"""
    result = run_pipeline(diff, "a..b")
    assert result.files[0].generated is True
    assert result.files[0].changes == []


def test_unsupported_language() -> None:
    diff = """\
diff --git a/readme.md b/readme.md
--- a/readme.md
+++ b/readme.md
@@ -1,2 +1,3 @@
 # Title
+Some text
"""
    result = run_pipeline(diff, "a..b")
    assert result.files[0].unsupported_language is True


def test_no_content_provider() -> None:
    result = run_pipeline(SIMPLE_DIFF, "a..b", get_content=None)
    assert len(result.files) == 1
    assert result.files[0].language == "python"
    assert result.files[0].changes == []


def test_summary_has_focus() -> None:
    get = _content_provider({"utils.py": OLD_UTILS}, {"utils.py": NEW_UTILS})
    result = run_pipeline(SIMPLE_DIFF, "abc..def", get)  # type: ignore[arg-type]
    assert len(result.summary.focus) >= 1
    assert "farewell" in result.summary.focus[0]


def test_tiered_oneliner_not_empty() -> None:
    get = _content_provider({"utils.py": OLD_UTILS}, {"utils.py": NEW_UTILS})
    result = run_pipeline(SIMPLE_DIFF, "abc..def", get)  # type: ignore[arg-type]
    assert len(result.tiered.oneliner) > 0


def test_breaking_change_in_summary() -> None:
    old_src = "def process(x: int) -> str:\n    return str(x)\n"
    new_src = "def process(x: int, y: int) -> str:\n    return str(x + y)\n"
    diff = """\
diff --git a/lib.py b/lib.py
--- a/lib.py
+++ b/lib.py
@@ -1,2 +1,2 @@
-def process(x: int) -> str:
-    return str(x)
+def process(x: int, y: int) -> str:
+    return str(x + y)
"""
    get = _content_provider({"lib.py": old_src}, {"lib.py": new_src})
    result = run_pipeline(diff, "abc..def", get)  # type: ignore[arg-type]
    assert len(result.summary.breaking_changes) == 1
    assert result.summary.breaking_changes[0].name == "process"
    assert "BREAKING" in result.tiered.oneliner


def test_empty_diff() -> None:
    result = run_pipeline("", "a..b")
    assert result.files == []
    assert result.tiered.oneliner != ""


# ---------------------------------------------------------------------------
# Regression tests for _apply_moves
# ---------------------------------------------------------------------------

def _make_symbol(name: str, sig: str = "def f()") -> Symbol:
    body = f"body of {name}"
    return Symbol(
        name=name, kind="function", signature=sig,
        start_line=1, end_line=2, body_hash=compute_body_hash(body),
    )


def test_apply_moves_same_named_symbols_not_stripped() -> None:
    """Regression: if two files have a symbol with the same name, only the
    source file's added/removed entry should be stripped — not the other file's."""
    # Setup: file_a.py and file_c.py both have a symbol called "helper".
    # "helper" is moved from file_a.py -> file_b.py.
    # file_c.py's "helper" (function_added) must NOT be touched.
    fc_a = FileChange(
        path="file_a.py", language="python", change_type="modified",
        changes=[SymbolChange(kind="function_removed", name="helper")],
    )
    fc_b = FileChange(
        path="file_b.py", language="python", change_type="modified",
        changes=[SymbolChange(kind="function_added", name="helper")],
    )
    fc_c = FileChange(
        path="file_c.py", language="python", change_type="modified",
        changes=[SymbolChange(kind="function_added", name="helper")],
    )

    old_sym = _make_symbol("helper")
    new_sym = _make_symbol("helper")
    moves = [MatchedSymbol(old=old_sym, new=new_sym, file_from="file_a.py", file_to="file_b.py")]

    _apply_moves(moves, [fc_a, fc_b, fc_c])

    # file_c's "helper" must still be present
    assert any(c.name == "helper" and c.kind == "function_added" for c in fc_c.changes), \
        "Same-named symbol in unrelated file was incorrectly stripped"
    # file_b should have the moved change, not the original function_added
    assert any(c.kind == "moved" and c.name == "helper" for c in fc_b.changes)
    assert not any(c.kind == "function_added" for c in fc_b.changes)


def test_apply_moves_destination_attribution() -> None:
    """Regression: move change must be added to the correct destination file,
    not the first file_change that happens to match."""
    fc_src = FileChange(
        path="old_module.py", language="python", change_type="modified",
        changes=[SymbolChange(kind="function_removed", name="do_work")],
    )
    fc_dst = FileChange(
        path="new_module.py", language="python", change_type="modified",
        changes=[SymbolChange(kind="function_added", name="do_work")],
    )
    fc_other = FileChange(
        path="other.py", language="python", change_type="modified",
        changes=[],
    )

    old_sym = _make_symbol("do_work")
    new_sym = _make_symbol("do_work")
    moves = [MatchedSymbol(old=old_sym, new=new_sym, file_from="old_module.py", file_to="new_module.py")]

    _apply_moves(moves, [fc_other, fc_src, fc_dst])

    # Move should be on fc_dst, not fc_other or fc_src
    assert any(c.kind == "moved" for c in fc_dst.changes), \
        "Move change not attributed to destination file"
    assert not any(c.kind == "moved" for c in fc_src.changes), \
        "Move change incorrectly on source file"
    assert not any(c.kind == "moved" for c in fc_other.changes), \
        "Move change incorrectly on unrelated file"
