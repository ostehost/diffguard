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
    render_summary_text,
    render_summary_json,
    render_text,
    review_hint,
    signature_display,
    terminal_safe_text,
)
from diffguard.schema import (
    DiffStats,
    FileChange,
    DiffGuardOutput,
    Meta,
    ReviewEnvelope,
    SymbolChange,
    TieredSummary,
)


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
        prod_references=list(prod),
        test_references=list(test),
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
        assert (
            review_hint("SYMBOL REMOVED")
            == "Check unresolved references and public API expectations"
        )

    def test_unknown_category_falls_back(self) -> None:
        assert review_hint("WHATEVER") == "Review this change"


# ---------------------------------------------------------------------------
# signature_display
# ---------------------------------------------------------------------------


class TestSignatureDisplay:
    def test_before_after(self) -> None:
        sc = _sc("signature_changed", before_signature="def f(a)", after_signature="def f(a, b)")
        assert signature_display(sc) == "f(a) → f(a, b)"

    def test_before_after_preserves_changed_return_annotations(self) -> None:
        sc = _sc(
            "signature_changed",
            before_signature="def f() -> int",
            after_signature="def f() -> str",
        )
        assert signature_display(sc) == "f() -> int → f() -> str"

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

    def test_terminal_controls_in_signature_are_visible_escapes(self) -> None:
        sc = _sc("function_added", signature="def f(a='\x1b[2J\t')")
        assert signature_display(sc) == r"f(a='\x1b[2J\t')"


class TestTerminalSafeText:
    def test_escapes_line_terminal_unicode_and_surrogate_controls(self) -> None:
        value = "line\ncol\tcr\ransi\x1bdel\x7fc1\x85bidi\u202e" + b"\xff".decode(
            "utf-8", "surrogateescape"
        )

        assert terminal_safe_text(value) == (r"line\ncol\tcr\ransi\x1bdel\x7fc1\x85bidi\u202e\xff")


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

    def test_removed_with_prod_references(self) -> None:
        f = _finding(
            _sc("function_removed", name="gone", line=12),
            "SYMBOL REMOVED",
            prod=(_ref("src/app.py", 3, "gone", "call", "gone()"),),
        )
        out = render_text([f])
        assert "SYMBOL REMOVED: `gone`" in out
        assert "File: src/mod.py:12" in out
        assert "Syntactic references: 1 name match (ownership unresolved):" in out
        assert "app.py:3 [call] `gone()`" in out
        assert "Review: Check unresolved references and public API expectations" in out

    def test_removed_without_references(self) -> None:
        f = _finding(_sc("function_removed", name="gone"), "SYMBOL REMOVED")
        assert "Compatibility: unknown" in render_text([f])

    def test_breaking_with_references(self) -> None:
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
        assert "breaking call shape" in out
        assert "2 name matches" in out

    def test_non_breaking_signature(self) -> None:
        f = _finding(
            _sc(
                "signature_changed",
                name="f",
                breaking=False,
                before_signature="def f(a)",
                after_signature="def f(a, b=1)",
            ),
            "SIGNATURE CHANGED",
            prod=(_ref("src/a.py", 1, "f", "call", "f(1)"),),
        )
        out = render_text([f])
        assert "no breaking call shape detected" in out
        assert "Syntactic references: 1 name match" in out

    def test_text_report_renders_every_analysis_gap(self) -> None:
        f = _finding(
            _sc(
                "signature_changed",
                analysis_gaps=["runtime compatibility unknown", "type compatibility unknown"],
            ),
            "SIGNATURE CHANGED",
        )

        rendered = render_text([f])

        assert "Gap: runtime compatibility unknown" in rendered
        assert "Gap: type compatibility unknown" in rendered

    def test_test_references_grouped(self) -> None:
        f = _finding(
            _sc("function_removed", name="gone"),
            "SYMBOL REMOVED",
            test=(_ref("tests/test_a.py", 1), _ref("tests/test_a.py", 2)),
        )
        assert "Test evidence: test_a.py (2 references)" in render_text([f])

    def test_repository_controlled_fields_cannot_inject_terminal_or_report_lines(self) -> None:
        f = _finding(
            _sc(
                "function_removed",
                name="gone\x1b[2J",
                analysis_gaps=["gap\rforged\nline\u202e"],
            ),
            "SYMBOL\x1b[31m REMOVED",
            path="src/unsafe\npath\x1b[0m.py",
            prod=(
                _ref(
                    "src/ref\tname.py",
                    3,
                    "gone",
                    "call\x85",
                    "gone()\nFORGED\x1b]8;;https://example.invalid\x07",
                ),
            ),
            test=(_ref("tests/test\runsafe.py", 4),),
        )

        rendered = render_text([f])

        assert "\x1b" not in rendered
        assert "\r" not in rendered
        assert "\t" not in rendered
        assert "\x85" not in rendered
        assert "\u202e" not in rendered
        assert r"SYMBOL\x1b[31m REMOVED" in rendered
        assert r"src/unsafe\npath\x1b[0m.py" in rendered
        assert r"ref\tname.py:3 [call\x85]" in rendered
        assert r"gone()\nFORGED\x1b]8;;https://example.invalid\x07" in rendered
        assert r"Gap: gap\rforged\nline\u202e" in rendered
        assert r"test\runsafe.py (1 reference)" in rendered

    def test_summary_text_is_rebuilt_from_safe_fields_without_mutating_json(self) -> None:
        path = "src/unsafe\npath\x1b[2J.py"
        name = "run\tjob\u202e"
        change = _sc("function_added", name=name, signature=f"def {name}()")
        output = _output(_fc(path, [change]))

        rendered = render_summary_text(output, "detailed")
        structured = json.loads(render_summary_json(output))

        assert "\x1b" not in rendered
        assert "\t" not in rendered
        assert "\u202e" not in rendered
        assert r"`run\tjob\u202e` (src/unsafe\npath\x1b[2J.py)" in rendered
        assert structured["files"][0]["path"] == path
        assert structured["files"][0]["changes"][0]["name"] == name


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
        data = json.loads(render_json(out, "a..b", "committed", [finding]))

        assert data["version"] == "1.1.0"
        assert data["status"] == "ok"
        assert data["mode"] == "committed"
        assert data["ref_range"] == "a..b"
        item = data["findings"][0]
        assert item["category"] == "SIGNATURE CHANGED"
        assert item["symbol"] == "f"
        assert item["source_file"] is None
        assert item["before_signature"] == "def f(a)"  # stripped
        assert item["after_signature"] == "def f(a, b)"
        assert len(item["references"]) == 2
        assert all(ref["resolution"] == "unresolved" for ref in item["references"])
        assert item["review_hint"] == "Review compatibility; bounded syntax rules were inconclusive"

    def test_move_preserves_source_and_destination_paths(self) -> None:
        sc = _sc(
            "moved",
            name="helper",
            file_from="src/old_module.py",
            line=7,
            rule_id="DG202",
            category_id="possible_symbol_move",
            category="POSSIBLE SYMBOL MOVE",
            confidence="medium",
        )
        destination = _fc("src/new_module.py", [sc])
        data = json.loads(
            render_json(
                _output(destination),
                "a..b",
                "committed",
                [_finding(sc, "POSSIBLE SYMBOL MOVE", path=destination.path)],
            )
        )

        item = data["findings"][0]
        assert item["file"] == "src/new_module.py"
        assert item["source_file"] == "src/old_module.py"

    def test_stats_and_parse_errors(self) -> None:
        clean = _fc("a.py", [_sc("function_removed", name="x")])
        broken = _fc("b.py", parse_error=True)
        out = _output(clean, broken)
        data = json.loads(render_json(out, "a..b", "committed", []))
        stats = data["stats"]
        assert stats["files_analyzed"] == 2
        assert stats["symbols_changed"] == 1
        assert stats["parse_errors"] == 1
        assert stats["reference_count"] == 0
        assert stats["silence_reason"] == "no high-signal changes"

    def test_silence_reason_none_when_findings(self) -> None:
        sc = _sc("function_removed", name="x")
        out = _output(_fc("a.py", [sc]))
        data = json.loads(render_json(out, "a..b", "committed", [_finding(sc, "SYMBOL REMOVED")]))
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
        data = json.loads(render_json(out, "a..b", "committed", []))
        assert data["warnings"][0]["code"] == "analysis_gap"
        assert data["warnings"][0]["file"] == "mod.py"

    def test_warning_paths_preserve_colons_and_use_exact_suffixes(self) -> None:
        path = "src/a:b.py"
        marker_path = "src/a:b\nc: reference candidate at snapshot d.py"
        warnings = [
            f"{path}: parse gap — symbol analysis skipped",
            f"{path}: reference scan has a parse gap",
            f"{path}: content unavailable at ref — symbol analysis skipped",
            f"{marker_path}: reference candidate at snapshot head is unreadable"
            " — reference analysis incomplete",
            f"{path}: unrelated diagnostic mentions parse gap",
        ]
        out = DiffGuardOutput(
            meta=Meta(
                ref_range="a..b",
                stats=DiffStats(files=1, additions=0, deletions=0),
                warnings=warnings,
            ),
            files=[_fc(path)],
        )

        data = json.loads(render_json(out, "a..b", "committed", []))

        assert [warning["code"] for warning in data["warnings"]] == [
            "parse_gap",
            "parse_gap",
            "analysis_gap",
            "analysis_gap",
            "analysis_gap",
        ]
        assert [warning["file"] for warning in data["warnings"]] == [
            path,
            path,
            path,
            marker_path,
            None,
        ]
        assert [warning["message"] for warning in data["warnings"]] == warnings

    def test_json_escapes_display_controls_without_changing_parsed_values(self) -> None:
        message = "warning with c1:\x85 bidi:\u202e isolate:\u2066"
        out = DiffGuardOutput(
            meta=Meta(
                ref_range="a..b",
                stats=DiffStats(files=1, additions=0, deletions=0),
                warnings=[message],
            ),
            files=[_fc("mod.py")],
        )

        rendered = render_json(out, "a..b", "committed", [])

        for control in ("\x85", "\u202e", "\u2066"):
            assert control not in rendered
        assert r"\u0085" in rendered
        assert r"\u202e" in rendered
        assert r"\u2066" in rendered
        assert json.loads(rendered)["warnings"][0]["message"] == message

    def test_surrogateescaped_paths_are_json_safe_display_values(self) -> None:
        destination = b"pkg/\xff.py".decode("utf-8", "surrogateescape")
        source = b"old/\xfe.py".decode("utf-8", "surrogateescape")
        reference = b"uses/\xfd.py".decode("utf-8", "surrogateescape")
        sc = _sc("moved", name="helper", file_from=source)
        out = DiffGuardOutput(
            meta=Meta(
                ref_range="a..b",
                stats=DiffStats(files=1, additions=0, deletions=0),
                warnings=[f"{destination}: parse gap — symbol analysis skipped"],
            ),
            files=[_fc(destination, [sc])],
        )
        finding = _finding(
            sc,
            "POSSIBLE SYMBOL MOVE",
            path=destination,
            prod=(_ref(reference),),
        )

        rendered = render_json(out, "a..b", "committed", [finding])
        envelope = ReviewEnvelope.model_validate_json(rendered)

        assert envelope.findings[0].file == r"pkg/\xff.py"
        assert envelope.findings[0].source_file == r"old/\xfe.py"
        assert envelope.findings[0].references[0].file == r"uses/\xfd.py"
        assert envelope.warnings[0].file == r"pkg/\xff.py"
        assert envelope.warnings[0].message == (r"pkg/\xff.py: parse gap — symbol analysis skipped")
        assert out.files[0].path == destination
        assert out.files[0].path.encode("utf-8", "surrogateescape") == b"pkg/\xff.py"

    def test_warnings_empty_by_default(self) -> None:
        out = _output(_fc("a.py", [_sc("function_removed", name="x")]))
        data = json.loads(render_json(out, "a..b", "committed", []))
        assert data["warnings"] == []


class TestRenderSummaryJson:
    def test_recursively_sanitizes_surrogates_without_mutating_model(self) -> None:
        path = b"pkg/\xff.py".decode("utf-8", "surrogateescape")
        output = DiffGuardOutput(
            meta=Meta(
                ref_range="stdin",
                stats=DiffStats(files=1, additions=0, deletions=0),
                warnings=[f"{path}: parse gap — symbol analysis skipped"],
            ),
            files=[_fc(path)],
            tiered=TieredSummary(detailed=f"Changed {path}"),
        )

        data = json.loads(render_summary_json(output))

        assert data["files"][0]["path"] == r"pkg/\xff.py"
        assert data["meta"]["warnings"] == [r"pkg/\xff.py: parse gap — symbol analysis skipped"]
        assert data["tiered"]["detailed"] == r"Changed pkg/\xff.py"
        assert output.files[0].path == path
        assert output.files[0].path.encode("utf-8", "surrogateescape") == b"pkg/\xff.py"

    def test_preserves_pydantic_nonfinite_float_serialization(self) -> None:
        output = _output()
        output.meta.timing_ms = float("nan")

        data = json.loads(render_summary_json(output))

        assert data["meta"]["timing_ms"] is None


# ---------------------------------------------------------------------------
# render_empty_json
# ---------------------------------------------------------------------------


class TestRenderEmptyJson:
    def test_shape(self) -> None:
        data = json.loads(render_empty_json("a..b", "committed", "no changes in diff"))
        assert data["version"] == "1.1.0"
        assert data["ref_range"] == "a..b"
        assert data["findings"] == []
        assert data["stats"] == {
            "files_analyzed": 0,
            "symbols_changed": 0,
            "parse_errors": 0,
            "reference_count": 0,
            "silence_reason": "no changes in diff",
        }

    def test_error_shape_validates(self) -> None:
        from diffguard.report import render_error_json
        from diffguard.schema import ReviewEnvelope

        rendered = render_error_json("bad..ref", "committed", "Invalid ref")
        envelope = ReviewEnvelope.model_validate_json(rendered)
        assert envelope.status == "error"
        assert envelope.error is not None
