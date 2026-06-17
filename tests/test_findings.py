"""Tests for the findings domain layer.

These exercise the high-signal trigger and finding extraction directly,
without going through the CLI — proving the domain is decoupled from
presentation and orchestration.
"""

from __future__ import annotations

from diffguard.engine.deps import Reference
from diffguard.engine.findings import (
    Finding,
    categorize_change,
    extract_findings,
    has_high_signal,
    is_high_signal,
)
from diffguard.schema import DiffStats, FileChange, DiffGuardOutput, Meta, SymbolChange


def _sc(kind: str, name: str = "f", **kwargs: object) -> SymbolChange:
    return SymbolChange(kind=kind, name=name, **kwargs)  # type: ignore[arg-type]


def _output(*changes: SymbolChange, path: str = "src/mod.py") -> DiffGuardOutput:
    fc = FileChange(path=path, change_type="modified", language="python", changes=list(changes))
    return DiffGuardOutput(
        meta=Meta(ref_range="a..b", stats=DiffStats(files=1, additions=0, deletions=0)),
        files=[fc],
    )


# ---------------------------------------------------------------------------
# is_high_signal — the single source of truth for the trigger
# ---------------------------------------------------------------------------


class TestIsHighSignal:
    def test_signature_change_is_high_signal(self) -> None:
        sc = _sc("signature_changed", before_signature="def f(a)", after_signature="def f(a, b)")
        assert is_high_signal(sc)

    def test_breaking_is_high_signal(self) -> None:
        assert is_high_signal(_sc("function_modified", breaking=True))

    def test_removed_is_high_signal(self) -> None:
        assert is_high_signal(_sc("function_removed"))
        assert is_high_signal(_sc("class_removed"))

    def test_moved_is_high_signal(self) -> None:
        assert is_high_signal(_sc("moved"))

    def test_body_only_modification_is_silence(self) -> None:
        assert not is_high_signal(_sc("function_modified"))

    def test_plain_addition_is_silence(self) -> None:
        assert not is_high_signal(_sc("function_added"))

    def test_one_sided_signature_is_silence(self) -> None:
        # Only one side present → not a signature *change*
        assert not is_high_signal(_sc("function_added", after_signature="def f(a)"))


# ---------------------------------------------------------------------------
# categorize_change
# ---------------------------------------------------------------------------


class TestCategorize:
    def test_removed(self) -> None:
        assert categorize_change(_sc("function_removed")) == "SYMBOL REMOVED"

    def test_moved(self) -> None:
        assert categorize_change(_sc("moved")) == "SYMBOL MOVED"

    def test_signature_delegates(self) -> None:
        sc = _sc("signature_changed", before_signature="def f(a)", after_signature="def f()")
        assert categorize_change(sc) == "PARAMETER REMOVED"

    def test_fallback(self) -> None:
        assert categorize_change(_sc("function_modified")) == "CHANGED"


# ---------------------------------------------------------------------------
# has_high_signal — output-level
# ---------------------------------------------------------------------------


class TestHasHighSignal:
    def test_true_when_any_high_signal(self) -> None:
        out = _output(_sc("function_modified"), _sc("function_removed"))
        assert has_high_signal(out)

    def test_false_when_all_silence(self) -> None:
        out = _output(_sc("function_modified"), _sc("function_added"))
        assert not has_high_signal(out)

    def test_dep_refs_do_not_trigger(self) -> None:
        # A body-only change with callers is still silence.
        out = _output(_sc("function_modified", name="f"))
        assert not has_high_signal(out)


# ---------------------------------------------------------------------------
# extract_findings — caller annotation
# ---------------------------------------------------------------------------


class TestExtractFindings:
    def test_only_high_signal_become_findings(self) -> None:
        out = _output(
            _sc("function_modified", name="internal"),
            _sc("function_removed", name="gone"),
        )
        findings = extract_findings(out)
        assert [f.change.name for f in findings] == ["gone"]
        assert findings[0].category == "SYMBOL REMOVED"

    def test_splits_prod_and_test_callers(self) -> None:
        out = _output(_sc("function_removed", name="gone"))
        refs = [
            Reference("src/app.py", 10, "gone", "call", "gone()"),
            Reference("tests/test_app.py", 5, "gone", "call", "gone()"),
        ]
        (finding,) = extract_findings(out, refs)
        assert [r.file_path for r in finding.prod_callers] == ["src/app.py"]
        assert [r.file_path for r in finding.test_callers] == ["tests/test_app.py"]

    def test_import_refs_excluded(self) -> None:
        out = _output(_sc("function_removed", name="gone"))
        refs = [Reference("src/app.py", 1, "gone", "import", "from mod import gone")]
        (finding,) = extract_findings(out, refs)
        assert finding.prod_callers == []
        assert finding.test_callers == []

    def test_finding_path_property(self) -> None:
        out = _output(_sc("function_removed"), path="pkg/thing.py")
        (finding,) = extract_findings(out)
        assert isinstance(finding, Finding)
        assert finding.path == "pkg/thing.py"
