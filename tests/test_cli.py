"""CLI tests."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from diffguard import __version__
from diffguard.cli import EXIT_ERROR, EXIT_NO_CHANGES, EXIT_PARTIAL, EXIT_SUCCESS, main
from diffguard.schema import (
    DiffStats,
    FileChange,
    DiffGuardOutput,
    Meta,
    Summary,
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
            ),
        ],
        summary=Summary(change_types={"function_added": 1}),
        tiered=TieredSummary(
            oneliner="Added hello function",
            short="Added hello() to hello.py",
            detailed="## hello.py (added)\n- Added function `hello()`",
        ),
    )


def test_version() -> None:
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


@patch("diffguard.cli.run_pipeline")
def test_stdin_json(mock_pipeline: MagicMock) -> None:
    mock_pipeline.return_value = _make_output()
    result = runner.invoke(main, ["summarize", "--diff", "-"], input=SAMPLE_DIFF)
    assert result.exit_code == EXIT_SUCCESS
    data = json.loads(result.output)
    assert data["schema_version"] == "1.1"
    assert data["meta"]["ref_range"] == "stdin"


def test_stdin_no_changes() -> None:
    result = runner.invoke(main, ["summarize", "--diff", "-"], input="")
    assert result.exit_code == EXIT_NO_CHANGES


@patch("diffguard.cli.run_pipeline")
def test_format_oneliner(mock_pipeline: MagicMock) -> None:
    mock_pipeline.return_value = _make_output()
    result = runner.invoke(
        main, ["summarize", "--diff", "-", "--format", "oneliner"], input=SAMPLE_DIFF
    )
    assert result.exit_code == EXIT_SUCCESS
    assert result.output.strip() == "Added hello function"


@patch("diffguard.cli.run_pipeline")
def test_format_short(mock_pipeline: MagicMock) -> None:
    mock_pipeline.return_value = _make_output()
    result = runner.invoke(
        main, ["summarize", "--diff", "-", "--format", "short"], input=SAMPLE_DIFF
    )
    assert result.exit_code == EXIT_SUCCESS
    assert result.output.strip() == "Added hello() to hello.py"


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
    result = runner.invoke(
        main, ["summarize", "--diff", "-", "--no-generated"], input=SAMPLE_DIFF
    )
    assert result.exit_code == EXIT_SUCCESS
    # Verify skip_generated=True was passed through to run_pipeline
    mock_pipeline.assert_called_once()
    _, kwargs = mock_pipeline.call_args
    assert kwargs["skip_generated"] is True
    data = json.loads(result.output)
    assert data["files"][0]["generated"] is False


@patch("diffguard.cli.run_pipeline")
def test_error_exit_code(mock_pipeline: MagicMock) -> None:
    mock_pipeline.side_effect = RuntimeError("something broke")
    result = runner.invoke(main, ["summarize", "--diff", "-"], input=SAMPLE_DIFF)
    assert result.exit_code == EXIT_ERROR
    assert "Error: something broke" in result.output


@patch("diffguard.cli.get_diff")
@patch("diffguard.cli.run_pipeline")
def test_ref_range(mock_pipeline: MagicMock, mock_get_diff: MagicMock) -> None:
    mock_get_diff.return_value = SAMPLE_DIFF
    mock_pipeline.return_value = _make_output()
    result = runner.invoke(main, ["summarize", "HEAD~1..HEAD"])
    assert result.exit_code == EXIT_SUCCESS
    mock_get_diff.assert_called_once_with("HEAD~1..HEAD", repo_path=".")
    data = json.loads(result.output)
    assert "files" in data
