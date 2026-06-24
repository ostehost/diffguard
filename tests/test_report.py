"""Tests for the report presentation layer.

report.py is pure rendering (no git, no trigger logic), so it can be unit-tested
directly with hand-built Finding objects — no temp git repos required. Before
this, render_text/render_json/render_empty_json were only exercised through the
CI-skipped end-to-end CLI tests.
"""

from __future__ import annotations

import json

from diffguard.engine._types import Reference
from diffguard.engine.findings import Finding
from diffguard.report import (
    render_empty_json,
    render_json,
    render_text,
    review_hint,
    signature_display,
)
from diffguard.schema import DiffStats, FileChange, DiffGuardOutput, Meta, SymbolChange


def _sc(kind: str, name: str = "f", **kw: object) -> SymbolChange:
    return SymbolChange(kind=kind, name=name, **kw)  # type: ignore[arg-type]


def _fc(
    path: str = "src/mod.py", changes: list[SymbolChange] | None = None, **kw: object
) -> FileChange:
    return FileChange(
        path=path,
        change_type="modified",
        language="python",
        changes=changes or [],
        **kw,  # type: ignore[arg-type]
    )


def _finding(
    change: SymbolChange,
    category: str,
    *,
    path: str = "src/mod.py",
    prod: tuple[Reference, ...] = (),
    test: tuple[Reference, ...] = (),
) -> Finding:
    return Finding(
        file=_fc(path),
        change=change,
        category=category,
        prod_callers=list(prod),
        test_callers=list(test),
    )


def _ref(
    path: str, line: int = 1, name: str = "f", ctx: str = "call", src: str = "f()"
) -> Reference:
    return Reference(path, line, name, ctx, src)


def _output(*fcs: FileChange) -> DiffGuardOutput:
    return DiffGuardOutput(
        meta=Meta(ref_range="a..b", stats=DiffStats(files=len(fcs), additions=0, deletions=0)),
        files=list(fcs),
    )


# ---------------------------------------------------------------------------
# review_hint
# ---------------------------------------------------------------------------


class TestReviewHint:
    def test_known_category(self) -> None:
        assert review_hint("SYMBOL REMOVED") == "Ensure no remaining callers depend on this symbol"

    def test_unknown_category_falls_back(self) -> None:
        assert review_hint("WHATEVER") == "Review this change"


# ---------------------------------------------------------------------------
# signature_display
# ---------------------------------------------------------------------------


class TestSignatureDisplay:
    def test_before_after(self) -> None:
        sc = _sc("signature_changed", before_signature="def f(a)", after_signature="def f(a, b)")
        assert signature_display(sc) == "f(a) → f(a, b)"

    def test_single_signature_strips_keyword_and_return(self) -> None:
        sc = _sc("function_added", signature="def f(a) -> int")
        assert signature_display(sc) == "f(a)"

    def test_name_only_fallback(self) -> None:
        sc = _sc("moved", name="thing")
        assert signature_display(sc) == "`thing`"

    def test_multiline_signature_is_collapsed(self) -> None:
        sc = _sc("function_added", signature="def f(\n    a,\n    b,\n) -> int")
        out = signature_display(sc)
        assert "\n" not in out
        assert out.startswith("f(")
        assert "-> int" not in out  # return type stripped for compactness


# ---------------------------------------------------------------------------
# render_text
# ---------------------------------------------------------------------------


class TestRenderText:
    def test_empty_is_blank(self) -> None:
        assert render_text([]) == ""

    def test_header_pluralization_single(self) -> None:
        f = _finding(_sc("function_removed", name="gone"), "SYMBOL REMOVED")
        out = render_text([f])
        assert out.splitlines()[0] == "⚠ DiffGuard: 1 change needs review"

    def test_header_pluralization_multiple(self) -> None:
        fs = [
            _finding(_sc("function_removed", name="a"), "SYMBOL REMOVED"),
            _finding(_sc("moved", name="b"), "SYMBOL MOVED"),
        ]
        assert render_text(fs).splitlines()[0] == "⚠ DiffGuard: 2 changes need review"

    def test_removed_with_prod_callers(self) -> None:
        f = _finding(
            _sc("function_removed", name="gone", line=12),
            "SYMBOL REMOVED",
            prod=(_ref("src/app.py", 3, "gone", "call", "gone()"),),
        )
        out = render_text([f])
        assert "SYMBOL REMOVED: `gone`" in out
        assert "File: src/mod.py:12" in out
        assert "Impact: 1 caller will break:" in out
        assert "app.py:3  `gone()`" in out
        assert "Review: Ensure no remaining callers depend on this symbol" in out

    def test_removed_without_callers(self) -> None:
        f = _finding(_sc("function_removed", name="gone"), "SYMBOL REMOVED")
        assert "Impact: Symbol removed" in render_text([f])

    def test_breaking_with_callers(self) -> None:
        f = _finding(
            _sc(
                "signature_changed",
                name="f",
                breaking=True,
                before_signature="def f(a)",
                after_signature="def f()",
            ),
            "PARAMETER REMOVED",
            prod=(_ref("src/a.py", 1), _ref("src/a.py", 2)),
        )
        out = render_text([f])
        assert "Impact: 2 callers rely on the default:" in out

    def test_backward_compatible_signature(self) -> None:
        f = _finding(
            _sc(
                "signature_changed",
                name="f",
                before_signature="def f(a)",
                after_signature="def f(a, b=1)",
            ),
            "SIGNATURE CHANGED",
            prod=(_ref("src/a.py", 1, "f", "call", "f(1)"),),
        )
        out = render_text([f])
        assert "Impact: Backward-compatible (new kwarg has default)" in out
        assert "Callers: a.py (1 call)" in out

    def test_test_callers_grouped(self) -> None:
        f = _finding(
            _sc("function_removed", name="gone"),
            "SYMBOL REMOVED",
            test=(_ref("tests/test_a.py", 1), _ref("tests/test_a.py", 2)),
        )
        assert "Callers: test_a.py (2 calls)" in render_text([f])


# ---------------------------------------------------------------------------
# render_json
# ---------------------------------------------------------------------------


class TestRenderJson:
    def test_finding_shape_and_impact(self) -> None:
        sc = _sc(
            "signature_changed",
            name="f",
            before_signature="def f(a)\n",
            after_signature="def f(a, b)\n",
            line=5,
        )
        fc = _fc("src/mod.py", [sc])
        out = _output(fc)
        finding = _finding(
            sc,
            "SIGNATURE CHANGED",
            prod=(_ref("src/a.py", 1, "f"),),
            test=(_ref("tests/t.py", 2, "f"),),
        )
        data = json.loads(render_json(out, "a..b", [finding]))

        assert data["version"] == "0.2.0"
        assert data["ref_range"] == "a..b"
        item = data["findings"][0]
        assert item["category"] == "SIGNATURE_CHANGED"  # spaces -> underscores
        assert item["symbol"] == "f"
        assert item["before_signature"] == "def f(a)"  # stripped
        assert item["after_signature"] == "def f(a, b)"
        assert item["impact"]["production_callers"] == 1
        assert item["impact"]["test_callers"] == 1
        assert item["review_hint"] == "Review the signature change for compatibility"

    def test_stats_and_parse_errors(self) -> None:
        clean = _fc("a.py", [_sc("function_removed", name="x")])
        broken = _fc("b.py", parse_error=True)
        out = _output(clean, broken)
        data = json.loads(render_json(out, "a..b", []))
        stats = data["stats"]
        assert stats["files_analyzed"] == 2
        assert stats["symbols_changed"] == 1
        assert stats["parse_errors"] == 1
        assert stats["silence_reason"] == "no high-signal changes"

    def test_silence_reason_none_when_findings(self) -> None:
        sc = _sc("function_removed", name="x")
        out = _output(_fc("a.py", [sc]))
        data = json.loads(render_json(out, "a..b", [_finding(sc, "SYMBOL REMOVED")]))
        assert data["stats"]["silence_reason"] is None

    def test_warnings_surfaced(self) -> None:
        out = DiffGuardOutput(
            meta=Meta(
                ref_range="a..b",
                stats=DiffStats(files=1, additions=0, deletions=0),
                warnings=["mod.py: content unavailable at ref — symbol analysis skipped"],
            ),
            files=[_fc("mod.py")],
        )
        data = json.loads(render_json(out, "a..b", []))
        assert data["warnings"] == ["mod.py: content unavailable at ref — symbol analysis skipped"]

    def test_warnings_empty_by_default(self) -> None:
        out = _output(_fc("a.py", [_sc("function_removed", name="x")]))
        data = json.loads(render_json(out, "a..b", []))
        assert data["warnings"] == []


# ---------------------------------------------------------------------------
# render_empty_json
# ---------------------------------------------------------------------------


class TestRenderEmptyJson:
    def test_shape(self) -> None:
        data = json.loads(render_empty_json("a..b", "no changes in diff"))
        assert data["version"] == "0.2.0"
        assert data["ref_range"] == "a..b"
        assert data["findings"] == []
        assert data["stats"] == {
            "files_analyzed": 0,
            "symbols_changed": 0,
            "parse_errors": 0,
            "silence_reason": "no changes in diff",
        }
