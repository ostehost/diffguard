"""Tests for the `diffguard review` (and `context` alias) CLI command."""

from __future__ import annotations

import json
import os
import subprocess

import pytest
from click.testing import CliRunner

from diffguard.cli import EXIT_ERROR, EXIT_FINDINGS, EXIT_SUCCESS, main

# Tests that run against the live repo may fail in CI (shallow clone, no code changes in HEAD~1)
_skip_in_ci = pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason="Requires real repo history with code changes",
)


def _init_repo(tmp_path):
    """Initialize a git repo in tmp_path."""
    repo = str(tmp_path)
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True, check=True
    )
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, capture_output=True, check=True)
    return repo


class TestReviewCommand:
    """End-to-end tests for the review command."""

    @_skip_in_ci
    def test_review_on_own_repo(self):
        """Run review on the diffguard repo itself."""
        runner = CliRunner()
        result = runner.invoke(main, ["review", "HEAD~1..HEAD", "--repo", "."])
        assert result.exit_code in (EXIT_SUCCESS, EXIT_FINDINGS)

    @_skip_in_ci
    def test_review_default_ref_range(self):
        """review with no ref_range defaults to HEAD~1..HEAD."""
        runner = CliRunner()
        result = runner.invoke(main, ["review", "--repo", "."])
        assert result.exit_code in (EXIT_SUCCESS, EXIT_FINDINGS)

    @_skip_in_ci
    def test_review_verbose_on_own_repo(self):
        """With --verbose, always shows output even if no high-signal changes."""
        runner = CliRunner()
        result = runner.invoke(main, ["review", "HEAD~1..HEAD", "--repo", ".", "--verbose"])
        assert result.exit_code in (EXIT_SUCCESS, EXIT_FINDINGS)

    @_skip_in_ci
    def test_review_no_deps(self):
        """Run review with --no-deps flag."""
        runner = CliRunner()
        result = runner.invoke(main, ["review", "HEAD~1..HEAD", "--repo", ".", "--no-deps"])
        assert result.exit_code in (EXIT_SUCCESS, EXIT_FINDINGS)

    def test_body_only_changes_silent(self, tmp_path):
        """PR with only body modifications (no signature changes) → silent exit 0."""
        repo = _init_repo(tmp_path)

        (tmp_path / "lib.py").write_text("def helper():\n    return 1\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)

        (tmp_path / "lib.py").write_text("def helper():\n    return 42\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "change body"], cwd=repo, capture_output=True, check=True
        )

        runner = CliRunner()
        result = runner.invoke(main, ["review", "HEAD~1..HEAD", "--repo", repo])
        assert result.exit_code == EXIT_SUCCESS
        assert result.output.strip() == ""

    def test_signature_change_produces_output(self, tmp_path):
        """PR with signature change → exit 1 with output."""
        repo = _init_repo(tmp_path)

        (tmp_path / "lib.py").write_text("def helper(a):\n    return a\n")
        (tmp_path / "main.py").write_text("from lib import helper\nx = helper(1)\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)

        (tmp_path / "lib.py").write_text("def helper(a, b):\n    return a + b\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "add param"], cwd=repo, capture_output=True, check=True
        )

        runner = CliRunner()
        result = runner.invoke(main, ["review", "HEAD~1..HEAD", "--repo", repo])
        assert result.exit_code == EXIT_FINDINGS
        assert "⚠ DiffGuard:" in result.output
        assert "PARAMETER ADDED (BREAKING)" in result.output
        assert "helper" in result.output

    def test_signature_change_json_format(self, tmp_path):
        """--format json produces valid JSON with findings."""
        repo = _init_repo(tmp_path)

        (tmp_path / "lib.py").write_text("def helper(a):\n    return a\n")
        (tmp_path / "main.py").write_text("from lib import helper\nx = helper(1)\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)

        (tmp_path / "lib.py").write_text("def helper(a, b):\n    return a + b\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "add param"], cwd=repo, capture_output=True, check=True
        )

        runner = CliRunner()
        result = runner.invoke(main, ["review", "HEAD~1..HEAD", "--repo", repo, "--format", "json"])
        assert result.exit_code == EXIT_FINDINGS
        data = json.loads(result.output)
        assert data["version"] == "0.1.0"
        assert data["ref_range"] == "HEAD~1..HEAD"
        assert len(data["findings"]) > 0
        finding = data["findings"][0]
        assert finding["category"] == "PARAMETER_ADDED_(BREAKING)"
        assert finding["symbol"] == "helper"
        assert "impact" in finding
        assert "review_hint" in finding
        assert data["stats"]["silence_reason"] is None

    def test_body_only_json_silent(self, tmp_path):
        """Body-only changes in JSON format → exit 0 with empty findings."""
        repo = _init_repo(tmp_path)

        (tmp_path / "lib.py").write_text("def helper():\n    return 1\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)

        (tmp_path / "lib.py").write_text("def helper():\n    return 42\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "change body"], cwd=repo, capture_output=True, check=True
        )

        runner = CliRunner()
        result = runner.invoke(main, ["review", "HEAD~1..HEAD", "--repo", repo, "--format", "json"])
        assert result.exit_code == EXIT_SUCCESS
        data = json.loads(result.output)
        assert data["findings"] == []
        assert data["stats"]["silence_reason"] == "no high-signal changes"

    def test_removed_symbol_produces_output(self, tmp_path):
        """Removed symbol should produce output with exit 1."""
        repo = _init_repo(tmp_path)

        (tmp_path / "lib.py").write_text(
            "def helper():\n    return 1\n\ndef old_func():\n    pass\n"
        )
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)

        (tmp_path / "lib.py").write_text("def helper():\n    return 1\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "remove old_func"], cwd=repo, capture_output=True, check=True
        )

        runner = CliRunner()
        result = runner.invoke(main, ["review", "HEAD~1..HEAD", "--repo", repo])
        assert result.exit_code == EXIT_FINDINGS
        assert "⚠ DiffGuard:" in result.output
        assert "SYMBOL REMOVED" in result.output

    def test_error_exit_code(self, tmp_path):
        """Invalid repo should exit with code 2."""
        runner = CliRunner()
        result = runner.invoke(
            main, ["review", "HEAD~1..HEAD", "--repo", str(tmp_path / "nonexistent")]
        )
        assert result.exit_code == EXIT_ERROR

    def test_verbose_shows_output_for_body_changes(self, tmp_path):
        """--verbose shows output even for body-only changes."""
        repo = _init_repo(tmp_path)

        (tmp_path / "lib.py").write_text("def helper():\n    return 1\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)

        (tmp_path / "lib.py").write_text("def helper():\n    return 42\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "change body"], cwd=repo, capture_output=True, check=True
        )

        runner = CliRunner()
        result = runner.invoke(main, ["review", "HEAD~1..HEAD", "--repo", repo, "--verbose"])
        assert result.exit_code in (EXIT_SUCCESS, EXIT_FINDINGS)


class TestContextAlias:
    """Ensure the `context` command still works as a hidden alias."""

    @_skip_in_ci
    def test_context_still_works(self):
        """context command is still accessible."""
        runner = CliRunner()
        result = runner.invoke(main, ["context", "HEAD~1..HEAD", "--repo", "."])
        assert result.exit_code in (EXIT_SUCCESS, EXIT_FINDINGS)

    @_skip_in_ci
    def test_context_with_json(self):
        """context alias supports --format json."""
        runner = CliRunner()
        result = runner.invoke(main, ["context", "HEAD~1..HEAD", "--repo", ".", "--format", "json"])
        assert result.exit_code in (EXIT_SUCCESS, EXIT_FINDINGS)
        data = json.loads(result.output)
        assert "version" in data

    def test_context_hidden_in_help(self):
        """context should not appear in --help output."""
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "review" in result.output
        assert "context" not in result.output

    @_skip_in_ci
    def test_context_default_ref_range(self):
        """context alias also defaults to HEAD~1..HEAD."""
        runner = CliRunner()
        result = runner.invoke(main, ["context", "--repo", "."])
        assert result.exit_code in (EXIT_SUCCESS, EXIT_FINDINGS)


class TestHelpText:
    """Test that help text is clear and self-documenting."""

    def test_main_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "structural breaks" in result.output
        assert "review" in result.output

    def test_review_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["review", "--help"])
        assert "high-signal" in result.output
        assert "Exit codes" in result.output
        assert "--format" in result.output
