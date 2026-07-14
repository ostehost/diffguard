"""Tests for diffguard.schema models."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from diffguard.schema import (
    FileChange,
    DiffGuardOutput,
    Meta,
    ReviewEnvelope,
    SymbolChange,
)


def _minimal_meta() -> dict[str, Any]:
    return {
        "ref_range": "abc123..def456",
        "stats": {"files": 1, "additions": 10, "deletions": 2},
    }


def _minimal_output(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {"meta": _minimal_meta()}
    data.update(overrides)
    return data


class TestDefaults:
    def test_minimal_output(self) -> None:
        out = DiffGuardOutput.model_validate(_minimal_output())
        assert out.schema_version == "2.0"
        assert out.files == []
        assert out.summary.change_types == {}
        assert out.summary.breaking_changes == []
        assert out.tiered.oneliner == ""

    def test_meta_defaults(self) -> None:
        meta = Meta.model_validate(_minimal_meta())
        # schema_version is on DiffGuardOutput, not Meta
        assert meta.warnings == []
        assert meta.timing_ms is None

    @pytest.mark.parametrize("schema_version", ["1.1", "2.0.0", "3.0", ""])
    def test_rejects_unknown_schema_version(self, schema_version: str) -> None:
        with pytest.raises(ValidationError, match="schema_version"):
            DiffGuardOutput.model_validate(_minimal_output(schema_version=schema_version))


class TestFullOutput:
    def test_all_fields(self) -> None:
        data = _minimal_output(
            files=[
                {
                    "path": "foo.py",
                    "language": "python",
                    "change_type": "modified",
                    "changes": [
                        {
                            "kind": "function_added",
                            "name": "bar",
                            "signature": "def bar() -> None",
                            "line": 10,
                        }
                    ],
                }
            ],
            summary={"change_types": {"feature": 1}, "breaking_changes": []},
            tiered={
                "oneliner": "Added bar",
                "short": "Added function bar to foo.py",
                "detailed": "...",
            },
        )
        out = DiffGuardOutput.model_validate(data)
        assert len(out.files) == 1
        assert out.files[0].changes[0].name == "bar"
        assert out.tiered.oneliner == "Added bar"


class TestSymbolChangeKinds:
    @pytest.mark.parametrize(
        "kind",
        [
            "function_added",
            "function_removed",
            "function_modified",
            "class_added",
            "class_removed",
            "class_modified",
            "signature_changed",
            "moved",
        ],
    )
    def test_each_kind(self, kind: str) -> None:
        sc = SymbolChange(kind=kind, name="x")  # type: ignore[arg-type]
        assert sc.kind == kind

    def test_signature_changed_fields(self) -> None:
        sc = SymbolChange(
            kind="signature_changed",
            name="f",
            before_signature="def f(a: int) -> None",
            after_signature="def f(a: int, b: str) -> None",
            breaking=True,
        )
        assert sc.before_signature is not None
        assert sc.breaking is True

    def test_moved_fields(self) -> None:
        sc = SymbolChange(kind="moved", name="g", file_from="old.py")
        assert sc.file_from == "old.py"

    def test_detail_escape_hatch(self) -> None:
        sc = SymbolChange(
            kind="function_added", name="h", detail={"decorator": "@cache", "async": True}
        )
        assert sc.detail is not None
        assert sc.detail["async"] is True


class TestFileChangeFlags:
    def test_parse_error(self) -> None:
        fc = FileChange(path="bad.rs", change_type="modified", parse_error=True)
        assert fc.parse_error is True
        assert fc.changes == []

    def test_binary(self) -> None:
        fc = FileChange(path="img.png", change_type="added", binary=True)
        assert fc.binary is True

    def test_unsupported_language(self) -> None:
        fc = FileChange(path="x.obscure", change_type="modified", unsupported_language=True)
        assert fc.unsupported_language is True


class TestMetaWarnings:
    def test_warnings_list(self) -> None:
        meta = Meta.model_validate({**_minimal_meta(), "warnings": ["truncated", "slow"]})
        assert len(meta.warnings) == 2


class TestJsonSchema:
    def test_model_json_schema(self) -> None:
        schema = DiffGuardOutput.model_json_schema()
        assert isinstance(schema, dict)


class TestRoundTrip:
    def test_round_trip(self) -> None:
        data = _minimal_output(
            files=[
                {
                    "path": "a.py",
                    "language": "python",
                    "change_type": "added",
                    "changes": [{"kind": "function_added", "name": "f", "line": 1}],
                }
            ],
        )
        original = DiffGuardOutput.model_validate(data)
        json_str = original.model_dump_json()
        restored = DiffGuardOutput.model_validate_json(json_str)
        assert original == restored


class TestReviewEnvelope:
    @staticmethod
    def _minimal(**overrides: Any) -> dict[str, Any]:
        data: dict[str, Any] = {
            "mode": "committed",
            "ref_range": "a..b",
            "stats": {
                "files_analyzed": 0,
                "symbols_changed": 0,
                "parse_errors": 0,
                "reference_count": 0,
            },
        }
        data.update(overrides)
        return data

    def test_populated(self) -> None:
        envelope = ReviewEnvelope.model_validate(
            {
                "mode": "worktree",
                "ref_range": "abc..:worktree",
                "findings": [
                    {
                        "rule_id": "DG104",
                        "category_id": "default_removed",
                        "category": "DEFAULT REMOVED",
                        "symbol": "f",
                        "file": "lib.py",
                        "breaking": True,
                        "confidence": "high",
                        "evidence": [{"kind": "syntax", "message": "default removed"}],
                        "references": [
                            {
                                "file": "main.py",
                                "line": 2,
                                "symbol": "f",
                                "kind": "call",
                                "source": "f()",
                            }
                        ],
                        "review_hint": "Update calls",
                    }
                ],
                "stats": {
                    "files_analyzed": 1,
                    "symbols_changed": 1,
                    "parse_errors": 0,
                    "reference_count": 1,
                },
            }
        )
        assert envelope.findings[0].references[0].resolution == "unresolved"
        assert envelope.findings[0].source_file is None

    def test_move_source_file_is_optional_structured_evidence(self) -> None:
        envelope = ReviewEnvelope.model_validate(
            {
                "mode": "committed",
                "ref_range": "a..b",
                "findings": [
                    {
                        "rule_id": "DG202",
                        "category_id": "possible_symbol_move",
                        "category": "POSSIBLE SYMBOL MOVE",
                        "symbol": "helper",
                        "file": "src/new_module.py",
                        "source_file": "src/old_module.py",
                        "confidence": "medium",
                        "evidence": [{"kind": "syntax", "message": "cross-file candidate"}],
                        "review_hint": "Confirm identity",
                    }
                ],
                "stats": {
                    "files_analyzed": 2,
                    "symbols_changed": 1,
                    "parse_errors": 0,
                    "reference_count": 0,
                },
            }
        )

        finding = envelope.findings[0]
        assert finding.file == "src/new_module.py"
        assert finding.source_file == "src/old_module.py"

    @pytest.mark.parametrize(
        "data",
        [
            {
                "mode": "committed",
                "ref_range": "a..b",
                "stats": {
                    "files_analyzed": 0,
                    "symbols_changed": 0,
                    "parse_errors": 0,
                    "reference_count": 0,
                    "silence_reason": "no changes in diff",
                },
            },
            {
                "mode": "worktree",
                "ref_range": "a..:worktree",
                "warnings": [
                    {"code": "parse_gap", "message": "bad.py: parse gap", "file": "bad.py"}
                ],
                "stats": {
                    "files_analyzed": 1,
                    "symbols_changed": 0,
                    "parse_errors": 1,
                    "reference_count": 0,
                    "silence_reason": "no high-signal changes",
                },
            },
            {
                "status": "error",
                "mode": "committed",
                "ref_range": "bad",
                "stats": {
                    "files_analyzed": 0,
                    "symbols_changed": 0,
                    "parse_errors": 0,
                    "reference_count": 0,
                    "silence_reason": "tool error",
                },
                "error": {"code": "tool_error", "message": "invalid ref"},
            },
        ],
    )
    def test_empty_partial_and_error_paths(self, data: dict[str, Any]) -> None:
        assert ReviewEnvelope.model_validate(data)

    def test_rejects_unknown_version(self) -> None:
        with pytest.raises(ValidationError, match="version"):
            ReviewEnvelope.model_validate(self._minimal(version="1.0.0"))

    @pytest.mark.parametrize(
        "overrides",
        [
            {"status": "error"},
            {"error": {"code": "tool_error", "message": "invalid ref"}},
        ],
    )
    def test_rejects_inconsistent_status_and_error(self, overrides: dict[str, Any]) -> None:
        with pytest.raises(ValidationError, match="if and only if"):
            ReviewEnvelope.model_validate(self._minimal(**overrides))


class TestFixtures:
    def test_load_simple_function_add(self, load_fixture: Any) -> None:
        data = load_fixture("synthetic", "simple_function_add.json")
        out = DiffGuardOutput.model_validate(data)
        assert len(out.files) == 1
        assert out.files[0].changes[0].kind == "function_added"

    def test_load_signature_change(self, load_fixture: Any) -> None:
        data = load_fixture("synthetic", "signature_change.json")
        out = DiffGuardOutput.model_validate(data)
        assert any(c.breaking for f in out.files for c in f.changes)

    def test_load_multi_file_refactor(self, load_fixture: Any) -> None:
        data = load_fixture("synthetic", "multi_file_refactor.json")
        out = DiffGuardOutput.model_validate(data)
        assert len(out.files) == 3
        assert any(f.parse_error for f in out.files)
