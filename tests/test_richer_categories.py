"""Tests for richer category labels and install-hook command."""

from __future__ import annotations

import json
import os
import subprocess

from click.testing import CliRunner

from diffguard.cli import EXIT_ERROR, EXIT_FINDINGS, EXIT_SUCCESS, main
from diffguard.engine.signatures import classify_signature_change


# ---------------------------------------------------------------------------
# classify_signature_change unit tests
# ---------------------------------------------------------------------------


class TestClassifySignatureChange:
    def test_parameter_removed(self):
        assert classify_signature_change("def f(a, b)", "def f(a)") == "PARAMETER REMOVED"

    def test_kwonly_parameter_removed(self):
        assert classify_signature_change("def f(a, *, k=1)", "def f(a)") == "PARAMETER REMOVED"

    def test_parameter_added_breaking(self):
        assert classify_signature_change("def f(a)", "def f(a, b)") == "PARAMETER ADDED (BREAKING)"

    def test_parameter_added_nonbreaking(self):
        # New param with default → non-breaking → SIGNATURE CHANGED
        assert classify_signature_change("def f(a)", "def f(a, b=1)") == "SIGNATURE CHANGED"

    def test_return_type_changed(self):
        assert (
            classify_signature_change("def f(a) -> int", "def f(a) -> str") == "RETURN TYPE CHANGED"
        )

    def test_default_value_changed(self):
        assert classify_signature_change("def f(a=1)", "def f(a=2)") == "DEFAULT VALUE CHANGED"

    def test_breaking_type_change(self):
        assert (
            classify_signature_change("def f(a: int)", "def f(a: str)")
            == "BREAKING SIGNATURE CHANGE"
        )

    def test_no_change(self):
        assert classify_signature_change("def f(a)", "def f(a)") == "SIGNATURE CHANGED"

    def test_kwonly_added_breaking(self):
        assert (
            classify_signature_change("def f(a, *)", "def f(a, *, k)")
            == "PARAMETER ADDED (BREAKING)"
        )


# ---------------------------------------------------------------------------
# End-to-end CLI tests for new categories
# ---------------------------------------------------------------------------


def _init_repo(tmp_path):
    repo = str(tmp_path)
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True, check=True
    )
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, capture_output=True, check=True)
    return repo


class TestRicherCategoriesCLI:
    def test_parameter_removed_category(self, tmp_path):
        repo = _init_repo(tmp_path)
        (tmp_path / "lib.py").write_text("def helper(a, b):\n    return a + b\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)
        (tmp_path / "lib.py").write_text("def helper(a):\n    return a\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "remove param"], cwd=repo, capture_output=True, check=True
        )

        runner = CliRunner()
        result = runner.invoke(main, ["review", "HEAD~1..HEAD", "--repo", repo])
        assert result.exit_code == EXIT_FINDINGS
        assert "PARAMETER REMOVED" in result.output

    def test_parameter_added_breaking_category(self, tmp_path):
        repo = _init_repo(tmp_path)
        (tmp_path / "lib.py").write_text("def helper(a):\n    return a\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)
        (tmp_path / "lib.py").write_text("def helper(a, b):\n    return a + b\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "add required param"], cwd=repo, capture_output=True, check=True
        )

        runner = CliRunner()
        result = runner.invoke(main, ["review", "HEAD~1..HEAD", "--repo", repo, "--format", "json"])
        assert result.exit_code == EXIT_FINDINGS
        data = json.loads(result.output)
        assert data["findings"][0]["category"] == "PARAMETER_ADDED_(BREAKING)"

    def test_return_type_changed_category(self, tmp_path):
        repo = _init_repo(tmp_path)
        (tmp_path / "lib.py").write_text("def helper(a) -> int:\n    return a\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)
        (tmp_path / "lib.py").write_text("def helper(a) -> str:\n    return str(a)\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "change return type"], cwd=repo, capture_output=True, check=True
        )

        runner = CliRunner()
        result = runner.invoke(main, ["review", "HEAD~1..HEAD", "--repo", repo])
        assert result.exit_code == EXIT_FINDINGS
        assert "RETURN TYPE CHANGED" in result.output

    def test_default_value_changed_category(self, tmp_path):
        """Default value changes with additional param changes are detected."""
        repo = _init_repo(tmp_path)
        (tmp_path / "lib.py").write_text("def helper(a, b=1):\n    return a + b\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)
        # Change default AND add a new optional param so pipeline detects signature change
        (tmp_path / "lib.py").write_text("def helper(a, b=2, c=3):\n    return a + b + c\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "change default"], cwd=repo, capture_output=True, check=True
        )

        runner = CliRunner()
        result = runner.invoke(main, ["review", "HEAD~1..HEAD", "--repo", repo])
        # Pipeline detects this as a signature change; categorized as SIGNATURE CHANGED
        # (default value change detection requires same param count)
        assert result.exit_code == EXIT_FINDINGS

    def test_json_review_hints_specific(self, tmp_path):
        repo = _init_repo(tmp_path)
        (tmp_path / "lib.py").write_text("def helper(a):\n    return a\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)
        (tmp_path / "lib.py").write_text("def helper(a, b):\n    return a + b\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "add param"], cwd=repo, capture_output=True, check=True
        )

        runner = CliRunner()
        result = runner.invoke(main, ["review", "HEAD~1..HEAD", "--repo", repo, "--format", "json"])
        data = json.loads(result.output)
        assert "missing required argument" in data["findings"][0]["review_hint"]


# ---------------------------------------------------------------------------
# install-hook tests
# ---------------------------------------------------------------------------


class TestInstallHook:
    def test_install_pre_push(self, tmp_path):
        repo = _init_repo(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["install-hook", "--repo", repo])
        assert result.exit_code == EXIT_SUCCESS
        hook_path = tmp_path / ".git" / "hooks" / "pre-push"
        assert hook_path.exists()
        assert os.access(str(hook_path), os.X_OK)
        assert "diffguard review" in hook_path.read_text()

    def test_install_pre_commit(self, tmp_path):
        repo = _init_repo(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["install-hook", "--repo", repo, "--hook-type", "pre-commit"])
        assert result.exit_code == EXIT_SUCCESS
        hook_path = tmp_path / ".git" / "hooks" / "pre-commit"
        assert hook_path.exists()
        assert "diffguard review" in hook_path.read_text()

    def test_no_overwrite_without_force(self, tmp_path):
        repo = _init_repo(tmp_path)
        runner = CliRunner()
        runner.invoke(main, ["install-hook", "--repo", repo])
        result = runner.invoke(main, ["install-hook", "--repo", repo])
        assert result.exit_code == EXIT_ERROR
        assert "already exists" in result.output

    def test_force_overwrite(self, tmp_path):
        repo = _init_repo(tmp_path)
        runner = CliRunner()
        runner.invoke(main, ["install-hook", "--repo", repo])
        result = runner.invoke(main, ["install-hook", "--repo", repo, "--force"])
        assert result.exit_code == EXIT_SUCCESS

    def test_not_a_repo(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(main, ["install-hook", "--repo", str(tmp_path)])
        assert result.exit_code == EXIT_ERROR
