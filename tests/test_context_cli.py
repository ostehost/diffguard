"""Tests for the `diffguard review` CLI command."""

from __future__ import annotations

import json
import os
import subprocess
from unittest.mock import patch

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
    subprocess.run(
        ["git", "config", "core.hooksPath", ""], cwd=repo, capture_output=True, check=True
    )
    return repo


_REAL_SUBPROCESS_RUN = subprocess.run


def _simulate_empty_no_index_equality(command, *args, **kwargs):
    """Model Git variants that report an empty file as rc=0 with no patch."""
    if isinstance(command, (list, tuple)) and "--no-index" in command:
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
    return _REAL_SUBPROCESS_RUN(command, *args, **kwargs)


def _configure_hostile_diff_output(repo: str) -> None:
    for key, value in (
        ("diff.noprefix", "true"),
        ("diff.mnemonicPrefix", "true"),
        ("diff.srcPrefix", "HOSTILE-OLD/"),
        ("diff.dstPrefix", "HOSTILE-NEW/"),
        ("diff.external", "false"),
        ("diff.hostile.textconv", "false"),
        ("color.ui", "always"),
    ):
        subprocess.run(
            ["git", "config", key, value],
            cwd=repo,
            capture_output=True,
            check=True,
        )


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
        assert "PARAMETER ADDED (REQUIRED)" in result.output
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
        assert data["version"] == "1.1.0"
        assert data["mode"] == "committed"
        assert data["ref_range"] == "HEAD~1..HEAD"
        assert len(data["findings"]) > 0
        finding = data["findings"][0]
        assert finding["category_id"] == "required_parameter_added"
        assert finding["rule_id"] == "DG102"
        assert finding["symbol"] == "helper"
        assert finding["source_file"] is None
        assert "references" in finding
        assert all(ref["resolution"] == "unresolved" for ref in finding["references"])
        assert "review_hint" in finding
        assert data["stats"]["silence_reason"] is None

    def test_move_json_preserves_source_and_destination_paths(self, tmp_path):
        repo = _init_repo(tmp_path)
        (tmp_path / "old_module.py").write_text(
            "def helper():\n    return 1\n\ndef stays_old():\n    return 2\n"
        )
        (tmp_path / "new_module.py").write_text("def stays_new():\n    return 3\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "baseline"], cwd=repo, capture_output=True, check=True
        )

        (tmp_path / "old_module.py").write_text("def stays_old():\n    return 2\n")
        (tmp_path / "new_module.py").write_text(
            "def stays_new():\n    return 3\n\ndef helper():\n    return 1\n"
        )
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "move helper"],
            cwd=repo,
            capture_output=True,
            check=True,
        )

        result = CliRunner().invoke(
            main,
            ["review", "HEAD~1..HEAD", "--repo", repo, "--format", "json"],
        )

        assert result.exit_code == EXIT_FINDINGS
        move = next(
            finding
            for finding in json.loads(result.output)["findings"]
            if finding["category_id"] == "possible_symbol_move"
        )
        assert move["file"] == "new_module.py"
        assert move["source_file"] == "old_module.py"

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

    def test_silent_addition_and_body_change_do_not_scan_reference_parse_gap(self, tmp_path):
        repo = _init_repo(tmp_path)
        (tmp_path / "lib.py").write_text("def body_only():\n    return 1\n")
        (tmp_path / "broken.py").write_text("body_only(\nadded(\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "baseline"], cwd=repo, capture_output=True, check=True
        )

        (tmp_path / "lib.py").write_text("def body_only():\n    return 2\n")
        (tmp_path / "added.py").write_text("def added():\n    return 1\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "silent changes"], cwd=repo, capture_output=True, check=True
        )

        result = CliRunner().invoke(
            main,
            ["review", "HEAD~1..HEAD", "--repo", repo, "--format", "json"],
        )

        assert result.exit_code == EXIT_SUCCESS
        data = json.loads(result.output)
        assert data["findings"] == []
        assert data["warnings"] == []

    def test_signature_finding_retains_reference_scan_parse_gap(self, tmp_path):
        repo = _init_repo(tmp_path)
        (tmp_path / "lib.py").write_text("def surfaced(value):\n    return value\n")
        (tmp_path / "broken.py").write_text("surfaced(\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "baseline"], cwd=repo, capture_output=True, check=True
        )

        (tmp_path / "lib.py").write_text(
            "def surfaced(value, required):\n    return value + required\n"
        )
        subprocess.run(["git", "add", "lib.py"], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "signature"], cwd=repo, capture_output=True, check=True
        )

        result = CliRunner().invoke(
            main,
            ["review", "HEAD~1..HEAD", "--repo", repo, "--format", "json"],
        )

        assert result.exit_code == EXIT_FINDINGS
        data = json.loads(result.output)
        assert [finding["symbol"] for finding in data["findings"]] == ["surfaced"]
        assert data["warnings"] == [
            {
                "code": "parse_gap",
                "message": "broken.py: reference scan has a parse gap",
                "file": "broken.py",
            }
        ]

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

    def test_staged_review_uses_index_not_worktree(self, tmp_path):
        """--staged analyzes the index and ignores unstaged working tree edits."""
        repo = _init_repo(tmp_path)

        (tmp_path / "lib.py").write_text("def helper(a):\n    return a\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)

        (tmp_path / "lib.py").write_text("def helper(a, b):\n    return a + b\n")
        subprocess.run(["git", "add", "lib.py"], cwd=repo, capture_output=True, check=True)
        (tmp_path / "lib.py").write_text("def helper(a, b=1):\n    return a + b\n")

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["review", "--staged", "--repo", repo, "--format", "json"],
        )

        assert result.exit_code == EXIT_FINDINGS
        data = json.loads(result.output)
        assert data["ref_range"] == "HEAD..:index"
        assert data["findings"]
        assert "def helper(a, b)" in data["findings"][0]["after_signature"]
        assert "b=1" not in data["findings"][0]["after_signature"]

    def test_staged_review_rejects_ref_range(self, tmp_path):
        """--staged has one unambiguous source: the git index."""
        repo = _init_repo(tmp_path)

        runner = CliRunner()
        result = runner.invoke(main, ["review", "HEAD", "--staged", "--repo", repo])

        assert result.exit_code == EXIT_ERROR
        assert "--staged cannot be combined with a ref range" in result.output

    @pytest.mark.parametrize("mode", ["committed", "staged"])
    def test_review_from_nested_repo_path_uses_top_level_paths(self, tmp_path, mode):
        repo = _init_repo(tmp_path)
        (tmp_path / "lib.py").write_text("def helper(a=1):\n    return a\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "baseline"], cwd=repo, capture_output=True, check=True
        )
        nested = tmp_path / "package" / "internal"
        nested.mkdir(parents=True)
        (tmp_path / "lib.py").write_text("def helper(a):\n    return a\n")
        subprocess.run(["git", "add", "lib.py"], cwd=repo, capture_output=True, check=True)

        if mode == "committed":
            subprocess.run(
                ["git", "commit", "-m", "remove default"],
                cwd=repo,
                capture_output=True,
                check=True,
            )
            command = ["review", "HEAD~1..HEAD"]
        else:
            command = ["review", "--staged"]

        result = CliRunner().invoke(
            main,
            [*command, "--repo", str(nested), "--format", "json"],
        )

        assert result.exit_code == EXIT_FINDINGS
        data = json.loads(result.output)
        assert data["findings"][0]["file"] == "lib.py"
        assert not any("content unavailable" in warning["message"] for warning in data["warnings"])


class TestSummarizeCommand:
    def test_default_from_nested_repo_uses_complete_worktree_snapshot(self, tmp_path):
        repo = _init_repo(tmp_path)
        (tmp_path / "lib.py").write_text("def helper(a=1):\n    return a\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "baseline"], cwd=repo, capture_output=True, check=True
        )
        nested = tmp_path / "package" / "internal"
        nested.mkdir(parents=True)
        (tmp_path / "lib.py").write_text("def helper(a):\n    return a\n")
        (tmp_path / "added.py").write_text("def added():\n    return 1\n")

        result = CliRunner().invoke(
            main,
            ["summarize", "--repo", str(nested), "--format", "json"],
        )

        assert result.exit_code == EXIT_SUCCESS
        data = json.loads(result.output)
        assert data["meta"]["ref_range"] == "HEAD..:worktree"
        assert data["meta"]["warnings"] == []
        assert {file["path"] for file in data["files"]} == {"added.py", "lib.py"}
        lib = next(file for file in data["files"] if file["path"] == "lib.py")
        assert any(change["kind"] == "signature_changed" for change in lib["changes"])

    def test_nested_worktree_includes_empty_code_and_non_code_files(self, tmp_path):
        repo = _init_repo(tmp_path)
        (tmp_path / "baseline.py").write_text("VALUE = 1\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "baseline"], cwd=repo, capture_output=True, check=True
        )
        nested = tmp_path / "package" / "internal"
        nested.mkdir(parents=True)
        docs = tmp_path / "docs"
        docs.mkdir()
        (tmp_path / "empty.py").write_text("")
        (docs / "empty.md").write_text("")

        with patch(
            "diffguard.git.subprocess.run",
            side_effect=_simulate_empty_no_index_equality,
        ):
            result = CliRunner().invoke(
                main,
                ["summarize", "--repo", str(nested), "--format", "json"],
            )

        assert result.exit_code == EXIT_SUCCESS
        data = json.loads(result.output)
        assert data["meta"]["stats"] == {"files": 2, "additions": 0, "deletions": 0}
        files = {file["path"]: file for file in data["files"]}
        assert set(files) == {"docs/empty.md", "empty.py"}
        assert files["empty.py"]["change_type"] == "added"
        assert files["empty.py"]["language"] == "python"
        assert files["docs/empty.md"]["change_type"] == "added"
        assert files["docs/empty.md"]["unsupported_language"] is True


class TestWorktreeReview:
    """Hermetic end-to-end coverage for base-to-worktree review state."""

    @staticmethod
    def _baseline(tmp_path) -> str:
        repo = _init_repo(tmp_path)
        (tmp_path / "lib.py").write_text("def helper(a=1):\n    return a\n")
        (tmp_path / "main.py").write_text("from lib import helper\nvalue = helper()\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "baseline"], cwd=repo, capture_output=True, check=True
        )
        return repo

    @staticmethod
    def _review(repo: str, *args: str):
        return CliRunner().invoke(
            main,
            [
                "review",
                "--worktree",
                "--against",
                "HEAD",
                "--repo",
                repo,
                "--format",
                "json",
                *args,
            ],
        )

    def test_clean(self, tmp_path):
        repo = self._baseline(tmp_path)
        result = self._review(repo)
        assert result.exit_code == EXIT_SUCCESS
        data = json.loads(result.output)
        assert data["mode"] == "worktree"
        assert data["findings"] == []
        assert data["stats"]["silence_reason"] == "no changes in diff"

    def test_staged_only(self, tmp_path):
        repo = self._baseline(tmp_path)
        (tmp_path / "lib.py").write_text("def helper(a):\n    return a\n")
        subprocess.run(["git", "add", "lib.py"], cwd=repo, capture_output=True, check=True)
        result = self._review(repo)
        assert result.exit_code == EXIT_FINDINGS
        assert json.loads(result.output)["findings"][0]["category_id"] == "default_removed"

    def test_unstaged_only_and_changed_file_reference(self, tmp_path):
        repo = self._baseline(tmp_path)
        (tmp_path / "lib.py").write_text("def helper(a):\n    return a\n\nvalue = helper(1)\n")
        result = self._review(repo)
        assert result.exit_code == EXIT_FINDINGS
        finding = json.loads(result.output)["findings"][0]
        assert any(
            ref["file"] == "lib.py" and ref["kind"] == "call" for ref in finding["references"]
        )

    def test_changed_file_destructuring_bindings_are_not_references(self, tmp_path):
        repo = self._baseline(tmp_path)
        (tmp_path / "lib.py").write_text(
            "def helper(a):\n"
            "    return a\n"
            "\n"
            "value = helper(1)\n"
            "first, helper = values\n"
            "for first, helper in rows:\n"
            "    pass\n"
        )

        result = self._review(repo)

        assert result.exit_code == EXIT_FINDINGS
        finding = json.loads(result.output)["findings"][0]
        assert [
            (ref["file"], ref["line"], ref["kind"])
            for ref in finding["references"]
            if ref["file"] == "lib.py"
        ] == [("lib.py", 4, "call")]

    def test_nested_repo_path_uses_top_level_tracked_and_untracked_paths(self, tmp_path):
        self._baseline(tmp_path)
        nested = tmp_path / "package" / "internal"
        nested.mkdir(parents=True)
        (tmp_path / "lib.py").write_text("def helper(a):\n    return a\n")
        (tmp_path / "added.py").write_text("def added():\n    return 1\n")

        result = self._review(str(nested))

        assert result.exit_code == EXIT_FINDINGS
        data = json.loads(result.output)
        assert data["stats"]["files_analyzed"] == 2
        assert data["findings"][0]["file"] == "lib.py"
        assert not any("content unavailable" in warning["message"] for warning in data["warnings"])

    def test_nested_review_counts_empty_code_and_non_code_files(self, tmp_path):
        self._baseline(tmp_path)
        nested = tmp_path / "package" / "internal"
        nested.mkdir(parents=True)
        docs = tmp_path / "docs"
        docs.mkdir()
        (tmp_path / "empty.py").write_text("")
        (docs / "empty.md").write_text("")

        with patch(
            "diffguard.git.subprocess.run",
            side_effect=_simulate_empty_no_index_equality,
        ):
            result = self._review(str(nested), "--no-deps")

        assert result.exit_code == EXIT_SUCCESS
        data = json.loads(result.output)
        assert data["stats"]["files_analyzed"] == 2
        assert data["stats"]["silence_reason"] == "no high-signal changes"
        assert data["findings"] == []
        assert data["warnings"] == []

    def test_hostile_diff_output_config_does_not_hide_worktree_changes(self, tmp_path):
        repo = self._baseline(tmp_path)
        (tmp_path / ".gitattributes").write_text("*.py diff=hostile\n")
        subprocess.run(["git", "add", ".gitattributes"], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "configure diff driver"],
            cwd=repo,
            capture_output=True,
            check=True,
        )
        _configure_hostile_diff_output(repo)
        (tmp_path / "lib.py").write_text("def helper(a):\n    return a\n")
        (tmp_path / "added.py").write_text("def added():\n    return 1\n")

        result = self._review(repo, "--no-deps")

        assert result.exit_code == EXIT_FINDINGS
        data = json.loads(result.output)
        assert data["stats"]["files_analyzed"] == 2
        assert any(
            finding["file"] == "lib.py" and finding["category_id"] == "default_removed"
            for finding in data["findings"]
        )
        assert data["warnings"] == []

    def test_unicode_filename_is_analyzed(self, tmp_path):
        repo = _init_repo(tmp_path)
        source = tmp_path / "café.py"
        source.write_text("def helper(value=1):\n    return value\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "baseline"], cwd=repo, capture_output=True, check=True
        )
        source.write_text("def helper(value):\n    return value\n")

        result = self._review(repo)

        assert result.exit_code == EXIT_FINDINGS
        data = json.loads(result.output)
        assert data["findings"][0]["file"] == "café.py"
        assert data["findings"][0]["category_id"] == "default_removed"

    def test_untracked_filename_with_tab_is_counted(self, tmp_path):
        repo = self._baseline(tmp_path)
        (tmp_path / "new\tmodule.py").write_text("def added():\n    return 1\n")

        result = self._review(repo)

        assert result.exit_code == EXIT_SUCCESS
        data = json.loads(result.output)
        assert data["stats"]["files_analyzed"] == 1
        assert data["findings"] == []

    def test_symlink_type_change_does_not_fabricate_symbol_removal(self, tmp_path):
        repo = self._baseline(tmp_path)
        (tmp_path / "target.txt").write_text("not Python source\n")
        (tmp_path / "lib.py").unlink()
        (tmp_path / "lib.py").symlink_to("target.txt")

        result = self._review(repo)

        assert result.exit_code == EXIT_SUCCESS
        data = json.loads(result.output)
        assert data["findings"] == []
        assert any("content unavailable" in warning["message"] for warning in data["warnings"])

    def test_mixed_staged_and_unstaged(self, tmp_path):
        repo = self._baseline(tmp_path)
        (tmp_path / "lib.py").write_text("def helper(a):\n    return a\n")
        subprocess.run(["git", "add", "lib.py"], cwd=repo, capture_output=True, check=True)
        (tmp_path / "main.py").write_text(
            "from lib import helper\nvalue = helper(1)\nextra = helper(2)\n"
        )
        result = self._review(repo)
        assert result.exit_code == EXIT_FINDINGS
        finding = json.loads(result.output)["findings"][0]
        assert sum(ref["file"] == "main.py" for ref in finding["references"]) == 3

    @pytest.mark.parametrize("staged", [False, True])
    def test_added_file(self, tmp_path, staged):
        repo = self._baseline(tmp_path)
        (tmp_path / "added.py").write_text("def added():\n    return 1\n")
        if staged:
            subprocess.run(["git", "add", "added.py"], cwd=repo, capture_output=True, check=True)
        result = self._review(repo)
        assert result.exit_code == EXIT_SUCCESS
        data = json.loads(result.output)
        assert data["stats"]["files_analyzed"] == 1
        assert data["findings"] == []

    def test_deleted_file(self, tmp_path):
        repo = self._baseline(tmp_path)
        (tmp_path / "lib.py").unlink()
        result = self._review(repo)
        assert result.exit_code == EXIT_FINDINGS
        assert json.loads(result.output)["findings"][0]["category_id"] == "symbol_removed"

    def test_renamed_symbol(self, tmp_path):
        repo = self._baseline(tmp_path)
        (tmp_path / "lib.py").write_text("def renamed(a=1):\n    return a\n")
        result = self._review(repo)
        assert result.exit_code == EXIT_FINDINGS
        categories = {item["category_id"] for item in json.loads(result.output)["findings"]}
        assert "symbol_removed" in categories

    def test_invalid_base_is_schema_valid_error(self, tmp_path):
        repo = self._baseline(tmp_path)
        result = CliRunner().invoke(
            main,
            [
                "review",
                "--worktree",
                "--against",
                "does-not-exist",
                "--repo",
                repo,
                "--format",
                "json",
            ],
        )
        assert result.exit_code == EXIT_ERROR
        data = json.loads(result.output)
        assert data["status"] == "error"
        assert data["error"]["code"] == "tool_error"

    def test_non_repository_reports_repository_error(self, tmp_path):
        result = self._review(str(tmp_path))
        assert result.exit_code == EXIT_ERROR
        data = json.loads(result.output)
        assert data["status"] == "error"
        assert data["mode"] == "worktree"
        assert data["error"]["message"] == f"Not a git repository: {tmp_path}"

    def test_parse_gap_warns_without_fabricated_finding(self, tmp_path):
        repo = self._baseline(tmp_path)
        (tmp_path / "lib.py").write_text("def helper(a:\n    return a\n")
        result = self._review(repo)
        assert result.exit_code == EXIT_SUCCESS
        data = json.loads(result.output)
        assert data["findings"] == []
        assert data["stats"]["parse_errors"] == 1
        assert any(warning["code"] == "parse_gap" for warning in data["warnings"])

    def test_parse_gap_is_visible_in_text_mode(self, tmp_path):
        repo = self._baseline(tmp_path)
        (tmp_path / "lib.py").write_text("def helper(a:\n    return a\n")
        result = CliRunner().invoke(
            main,
            ["review", "--worktree", "--against", "HEAD", "--repo", repo],
        )
        assert result.exit_code == EXIT_SUCCESS
        assert "parse gap" in result.output
        assert "analysis warnings" in result.output


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
