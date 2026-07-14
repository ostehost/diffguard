"""CLI tests."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from diffguard import __version__
from diffguard.cli import (
    EXIT_ERROR,
    EXIT_FINDINGS,
    EXIT_NO_CHANGES,
    EXIT_PARTIAL,
    EXIT_SUCCESS,
    main,
)
from diffguard.engine._types import Reference, ReferenceScan
from diffguard.schema import (
    DiffStats,
    FileChange,
    DiffGuardOutput,
    Meta,
    Summary,
    SymbolChange,
    TieredSummary,
)

runner = CliRunner()

SAMPLE_DIFF = """\
diff --git a/hello.py b/hello.py
new file mode 100644
--- /dev/null
+++ b/hello.py
@@ -0,0 +1,3 @@
+def hello():
+    print("hello")
+    return 42
"""


def _make_output(parse_error: bool = False, generated: bool = False) -> DiffGuardOutput:
    return DiffGuardOutput(
        meta=Meta(
            ref_range="stdin",
            stats=DiffStats(files=1, additions=3, deletions=0),
        ),
        files=[
            FileChange(
                path="hello.py",
                language="python",
                change_type="added",
                parse_error=parse_error,
                generated=generated,
                changes=[
                    SymbolChange(
                        kind="function_added",
                        name="hello",
                        signature="def hello()",
                    )
                ],
            ),
        ],
        summary=Summary(change_types={"function_added": 1}),
        tiered=TieredSummary(
            oneliner="Added hello function",
            short="Added hello() to hello.py",
            detailed="## hello.py (added)\n- Added function `hello()`",
        ),
    )


def _make_review_output(*changes: SymbolChange) -> DiffGuardOutput:
    return DiffGuardOutput(
        meta=Meta(ref_range="base..head", stats=DiffStats(files=1, additions=1, deletions=1)),
        files=[
            FileChange(
                path="mod.py",
                language="python",
                change_type="modified",
                changes=list(changes),
            )
        ],
    )


def test_version() -> None:
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


@patch("diffguard.cli.run_pipeline")
def test_stdin_json(mock_pipeline: MagicMock) -> None:
    mock_pipeline.return_value = _make_output()
    result = runner.invoke(
        main,
        ["summarize", "--diff", "-", "--repo", "/does/not/need/to/exist"],
        input=SAMPLE_DIFF,
    )
    assert result.exit_code == EXIT_SUCCESS
    data = json.loads(result.output)
    assert data["schema_version"] == "2.0"
    assert data["meta"]["ref_range"] == "stdin"


@patch("diffguard.cli.run_pipeline")
def test_stdin_json_renders_surrogateescaped_paths_safely(mock_pipeline: MagicMock) -> None:
    path = b"pkg/\xff.py".decode("utf-8", "surrogateescape")
    output = _make_output()
    output.files[0].path = path
    output.meta.warnings = [f"{path}: parse gap — symbol analysis skipped"]
    output.tiered.detailed = f"Changed {path}"
    mock_pipeline.return_value = output

    result = runner.invoke(
        main,
        ["summarize", "--diff", "-", "--repo", "/does/not/need/to/exist"],
        input=SAMPLE_DIFF,
    )

    assert result.exit_code == EXIT_SUCCESS
    data = json.loads(result.output)
    assert data["files"][0]["path"] == r"pkg/\xff.py"
    assert data["meta"]["warnings"] == [r"pkg/\xff.py: parse gap — symbol analysis skipped"]
    assert data["tiered"]["detailed"] == r"Changed pkg/\xff.py"
    assert output.files[0].path == path
    assert output.files[0].path.encode("utf-8", "surrogateescape") == b"pkg/\xff.py"


def test_stdin_no_changes() -> None:
    result = runner.invoke(main, ["summarize", "--diff", "-"], input="")
    assert result.exit_code == EXIT_NO_CHANGES


def test_stdin_no_changes_json_is_schema_valid() -> None:
    result = runner.invoke(
        main,
        ["summarize", "--diff", "-", "--format", "json"],
        input="",
    )

    assert result.exit_code == EXIT_NO_CHANGES
    output = DiffGuardOutput.model_validate_json(result.output)
    assert output.meta.ref_range == "stdin"
    assert output.meta.stats == DiffStats(files=0, additions=0, deletions=0)
    assert output.files == []
    assert output.summary.change_types == {}
    assert output.tiered.oneliner == ""


@patch("diffguard.cli.run_pipeline")
def test_format_oneliner(mock_pipeline: MagicMock) -> None:
    mock_pipeline.return_value = _make_output()
    result = runner.invoke(
        main, ["summarize", "--diff", "-", "--format", "oneliner"], input=SAMPLE_DIFF
    )
    assert result.exit_code == EXIT_SUCCESS
    assert result.output.strip() == "Add `hello`"


@patch("diffguard.cli.run_pipeline")
def test_format_short(mock_pipeline: MagicMock) -> None:
    mock_pipeline.return_value = _make_output()
    result = runner.invoke(
        main, ["summarize", "--diff", "-", "--format", "short"], input=SAMPLE_DIFF
    )
    assert result.exit_code == EXIT_SUCCESS
    assert result.output.strip() == "`hello` (added)"


@patch("diffguard.cli.report.render_summary_text", return_value="safe summary")
@patch("diffguard.cli.run_pipeline")
def test_human_format_passes_summary_visibility_flags(
    mock_pipeline: MagicMock,
    mock_render: MagicMock,
) -> None:
    output = _make_output()
    mock_pipeline.return_value = output

    result = runner.invoke(
        main,
        [
            "summarize",
            "--diff",
            "-",
            "--format",
            "short",
            "--include-tests",
            "--show-skipped",
        ],
        input=SAMPLE_DIFF,
    )

    assert result.exit_code == EXIT_SUCCESS
    assert result.output.strip() == "safe summary"
    mock_render.assert_called_once_with(
        output,
        "short",
        include_tests=True,
        show_skipped=True,
    )


@patch("diffguard.cli.run_pipeline")
def test_human_summary_escapes_repository_control_characters(mock_pipeline: MagicMock) -> None:
    output = _make_output()
    output.files[0].path = "src/unsafe\npath\x1b[2J.py"
    output.files[0].changes[0].name = "hello\tjob\u202e"
    output.files[0].changes[0].signature = "def hello\tjob\u202e()"
    mock_pipeline.return_value = output

    result = runner.invoke(
        main,
        ["summarize", "--diff", "-", "--format", "detailed"],
        input=SAMPLE_DIFF,
    )

    assert result.exit_code == EXIT_SUCCESS
    assert "\x1b" not in result.output
    assert "\t" not in result.output
    assert "\u202e" not in result.output
    assert r"`hello\tjob\u202e` (src/unsafe\npath\x1b[2J.py)" in result.output


@patch("diffguard.cli.run_pipeline")
def test_summary_json_preserves_raw_repository_controls(mock_pipeline: MagicMock) -> None:
    output = _make_output()
    path = "src/unsafe\npath\x1b[2J.py"
    name = "hello\tjob\u202e"
    output.files[0].path = path
    output.files[0].changes[0].name = name
    mock_pipeline.return_value = output

    result = runner.invoke(
        main,
        ["summarize", "--diff", "-", "--format", "json"],
        input=SAMPLE_DIFF,
    )

    assert result.exit_code == EXIT_SUCCESS
    payload = json.loads(result.output)
    assert payload["files"][0]["path"] == path
    assert payload["files"][0]["changes"][0]["name"] == name


@patch("diffguard.cli.run_pipeline")
def test_format_detailed(mock_pipeline: MagicMock) -> None:
    mock_pipeline.return_value = _make_output()
    result = runner.invoke(
        main, ["summarize", "--diff", "-", "--format", "detailed"], input=SAMPLE_DIFF
    )
    assert result.exit_code == EXIT_SUCCESS
    assert "hello.py" in result.output


@patch("diffguard.cli.run_pipeline")
def test_format_json(mock_pipeline: MagicMock) -> None:
    mock_pipeline.return_value = _make_output()
    result = runner.invoke(
        main, ["summarize", "--diff", "-", "--format", "json"], input=SAMPLE_DIFF
    )
    assert result.exit_code == EXIT_SUCCESS
    data = json.loads(result.output)
    assert "files" in data


@patch("diffguard.cli.run_pipeline")
def test_partial_success(mock_pipeline: MagicMock) -> None:
    mock_pipeline.return_value = _make_output(parse_error=True)
    result = runner.invoke(main, ["summarize", "--diff", "-"], input=SAMPLE_DIFF)
    assert result.exit_code == EXIT_PARTIAL


@patch("diffguard.cli.run_pipeline")
def test_no_generated(mock_pipeline: MagicMock) -> None:
    mock_pipeline.return_value = _make_output(generated=False)
    result = runner.invoke(main, ["summarize", "--diff", "-", "--no-generated"], input=SAMPLE_DIFF)
    assert result.exit_code == EXIT_SUCCESS
    # Verify skip_generated=True was passed through to run_pipeline
    mock_pipeline.assert_called_once()
    _, kwargs = mock_pipeline.call_args
    assert kwargs["skip_generated"] is True
    data = json.loads(result.output)
    assert data["files"][0]["generated"] is False


@patch("diffguard.cli.run_pipeline")
def test_summarize_text_error_escapes_control_characters(mock_pipeline: MagicMock) -> None:
    mock_pipeline.side_effect = RuntimeError("something\nforged\x1b[2J broke")
    result = runner.invoke(main, ["summarize", "--diff", "-"], input=SAMPLE_DIFF)
    assert result.exit_code == EXIT_ERROR
    assert "\x1b" not in result.output
    assert "Error: something\\nforged\\x1b[2J broke" in result.output


@patch("diffguard.cli.get_diff")
@patch("diffguard.cli.get_repository_root", return_value="/repo")
@patch("diffguard.cli.run_pipeline")
def test_ref_range(
    mock_pipeline: MagicMock,
    _mock_repository_root: MagicMock,
    mock_get_diff: MagicMock,
) -> None:
    mock_get_diff.return_value = SAMPLE_DIFF
    mock_pipeline.return_value = _make_output()
    result = runner.invoke(main, ["summarize", "HEAD~1..HEAD"])
    assert result.exit_code == EXIT_SUCCESS
    mock_get_diff.assert_called_once_with("HEAD~1..HEAD", repo_path="/repo")
    data = json.loads(result.output)
    assert "files" in data


def test_review_does_not_scan_silent_addition_or_body_change() -> None:
    output = _make_review_output(
        SymbolChange(kind="function_added", name="added", after_signature="def added()"),
        SymbolChange(kind="function_modified", name="body_only"),
    )
    with (
        patch("diffguard.cli.get_repository_root", return_value="/repo"),
        patch("diffguard.cli.get_diff", return_value=SAMPLE_DIFF),
        patch("diffguard.cli.run_pipeline", return_value=output),
        patch(
            "diffguard.cli.scan_references",
            return_value=ReferenceScan(warnings=["broken.py: reference scan has a parse gap"]),
        ) as mock_scan,
    ):
        result = runner.invoke(
            main,
            ["review", "base..head", "--repo", "/repo", "--format", "json"],
        )

    assert result.exit_code == EXIT_SUCCESS
    mock_scan.assert_not_called()
    payload = json.loads(result.output)
    assert payload["findings"] == []
    assert payload["warnings"] == []
    assert payload["stats"]["parse_errors"] == 0


def test_review_scans_only_symbols_that_surface_as_findings() -> None:
    output = _make_review_output(
        SymbolChange(kind="function_added", name="added", after_signature="def added()"),
        SymbolChange(kind="function_modified", name="body_only"),
        SymbolChange(
            kind="signature_changed",
            name="signature",
            before_signature="def signature(a)",
            after_signature="def signature(a, b)",
        ),
        SymbolChange(kind="function_removed", name="removed"),
        SymbolChange(kind="moved", name="moved", file_from="old.py"),
    )
    scan = ReferenceScan(
        references=[Reference("use.py", 4, "removed", "call", "removed()")],
        warnings=["broken.py: reference scan has a parse gap"],
    )
    with (
        patch("diffguard.cli.get_repository_root", return_value="/repo"),
        patch("diffguard.cli.get_diff", return_value=SAMPLE_DIFF),
        patch("diffguard.cli.run_pipeline", return_value=output),
        patch("diffguard.cli.scan_references", return_value=scan) as mock_scan,
    ):
        result = runner.invoke(
            main,
            ["review", "base..head", "--repo", "/repo", "--format", "json"],
        )

    assert result.exit_code == EXIT_FINDINGS
    mock_scan.assert_called_once_with("/repo", ["signature", "removed", "moved"], "head")
    payload = json.loads(result.output)
    assert [finding["symbol"] for finding in payload["findings"]] == [
        "signature",
        "removed",
        "moved",
    ]
    removed = next(finding for finding in payload["findings"] if finding["symbol"] == "removed")
    assert [reference["file"] for reference in removed["references"]] == ["use.py"]
    assert payload["warnings"] == [
        {
            "code": "parse_gap",
            "message": "broken.py: reference scan has a parse gap",
            "file": "broken.py",
        }
    ]


def test_review_json_surfaces_unreadable_reference_candidate_as_analysis_gap() -> None:
    output = _make_review_output(
        SymbolChange(
            kind="signature_changed",
            name="contract",
            before_signature="def contract(a)",
            after_signature="def contract(a, b)",
        )
    )
    path = "src/a:b\nc: reference candidate at snapshot d.py"
    warning = (
        f"{path}: reference candidate at snapshot head is unreadable"
        " — reference analysis incomplete"
    )
    with (
        patch("diffguard.cli.get_repository_root", return_value="/repo"),
        patch("diffguard.cli.get_diff", return_value=SAMPLE_DIFF),
        patch("diffguard.cli.run_pipeline", return_value=output),
        patch(
            "diffguard.cli.scan_references",
            return_value=ReferenceScan(warnings=[warning]),
        ),
    ):
        result = runner.invoke(
            main,
            ["review", "base..head", "--repo", "/repo", "--format", "json"],
        )

    assert result.exit_code == EXIT_FINDINGS
    payload = json.loads(result.output)
    assert payload["findings"][0]["references"] == []
    assert payload["warnings"] == [
        {
            "code": "analysis_gap",
            "message": warning,
            "file": path,
        }
    ]


def test_review_text_escapes_repository_control_characters_in_warnings() -> None:
    output = _make_review_output(SymbolChange(kind="function_removed", name="removed"))
    output.meta.warnings = ["unsafe\npath.py: parse gap\x1b]8;;https://example.invalid\x07"]
    with (
        patch("diffguard.cli.get_repository_root", return_value="/repo"),
        patch("diffguard.cli.get_diff", return_value=SAMPLE_DIFF),
        patch("diffguard.cli.run_pipeline", return_value=output),
    ):
        result = runner.invoke(
            main,
            ["review", "base..head", "--repo", "/repo", "--no-deps", "--format", "text"],
        )

    assert result.exit_code == EXIT_FINDINGS
    assert "\x1b" not in result.output
    assert "\x07" not in result.output
    assert r"- unsafe\npath.py: parse gap\x1b]8;;https://example.invalid\x07" in result.output


def test_review_json_renders_surrogateescaped_paths_safely() -> None:
    path = b"pkg/\xff.py".decode("utf-8", "surrogateescape")
    output = _make_review_output(SymbolChange(kind="function_removed", name="removed"))
    output.files[0].path = path
    with (
        patch("diffguard.cli.get_repository_root", return_value="/repo"),
        patch("diffguard.cli.get_diff", return_value=SAMPLE_DIFF),
        patch("diffguard.cli.run_pipeline", return_value=output),
    ):
        result = runner.invoke(
            main,
            ["review", "base..head", "--repo", "/repo", "--no-deps", "--format", "json"],
        )

    assert result.exit_code == EXIT_FINDINGS
    payload = json.loads(result.output)
    assert payload["findings"][0]["file"] == r"pkg/\xff.py"
    assert output.files[0].path == path
    assert output.files[0].path.encode("utf-8", "surrogateescape") == b"pkg/\xff.py"


def test_worktree_review_reports_untracked_diff_failure_as_json_error() -> None:
    message = "git diff failed for added.py: fatal: added.py disappeared"
    with (
        patch("diffguard.cli.get_repository_root", return_value="/repo"),
        patch("diffguard.cli.resolve_commit", return_value="head-sha"),
        patch("diffguard.cli.get_merge_base", return_value="base-sha"),
        patch("diffguard.cli.get_worktree_diff", side_effect=RuntimeError(message)),
    ):
        result = runner.invoke(
            main,
            [
                "review",
                "--worktree",
                "--against",
                "HEAD",
                "--repo",
                "/repo",
                "--format",
                "json",
            ],
        )

    assert result.exit_code == EXIT_ERROR
    payload = json.loads(result.output)
    assert payload["status"] == "error"
    assert payload["mode"] == "worktree"
    assert payload["findings"] == []
    assert payload["warnings"] == []
    assert payload["error"] == {"code": "tool_error", "message": message}


def test_against_without_worktree_reports_worktree_mode_in_json_error() -> None:
    result = runner.invoke(
        main,
        ["review", "--against", "main", "--format", "json"],
    )

    assert result.exit_code == EXIT_ERROR
    payload = json.loads(result.output)
    assert payload["status"] == "error"
    assert payload["mode"] == "worktree"
    assert payload["error"] == {
        "code": "tool_error",
        "message": "--against requires --worktree",
    }


def test_install_hook_escapes_control_characters_in_reported_path() -> None:
    hook_path = "/repo/.git/hooks/pre-push\nFORGED\x1b[2J"
    with (
        patch("diffguard.cli.get_repository_root", return_value="/repo"),
        patch("diffguard.cli.hooks.install_hook", return_value=hook_path),
    ):
        result = runner.invoke(main, ["install-hook", "--repo", "/repo"])

    assert result.exit_code == EXIT_SUCCESS
    assert "\x1b" not in result.output
    assert r"Installed pre-push hook: /repo/.git/hooks/pre-push\nFORGED\x1b[2J" in result.output
