"""Tests for diffguard.engine.deps — dependency reference scanning."""

from __future__ import annotations


from diffguard.engine.deps import (
    _git_grep_files,
    _scan_file_for_symbols,
    find_references,
)


class TestScanFileForSymbols:
    """Unit tests for _scan_file_for_symbols."""

    def test_finds_identifier_in_python(self):
        source = "from foo import bar\nresult = bar(42)\n"
        hits = _scan_file_for_symbols(source, "python", {"bar"})
        assert len(hits) == 2
        names = [h[0] for h in hits]
        assert all(n == "bar" for n in names)

    def test_import_context_detected(self):
        source = "from foo import bar\n"
        hits = _scan_file_for_symbols(source, "python", {"bar"})
        assert len(hits) == 1
        assert hits[0][2] == "import"

    def test_call_context_detected(self):
        source = "x = process_request(environ)\n"
        hits = _scan_file_for_symbols(source, "python", {"process_request"})
        assert len(hits) == 1
        assert hits[0][2] == "call"

    def test_no_match_returns_empty(self):
        source = "x = 42\n"
        hits = _scan_file_for_symbols(source, "python", {"nonexistent"})
        assert hits == []

    def test_typescript_identifiers(self):
        source = "import { foo } from './bar';\nfoo();\n"
        hits = _scan_file_for_symbols(source, "typescript", {"foo"})
        assert len(hits) >= 2

    def test_multiple_symbols(self):
        source = "import a\nimport b\na()\nb()\n"
        hits = _scan_file_for_symbols(source, "python", {"a", "b"})
        assert len(hits) == 4

    def test_line_numbers_correct(self):
        source = "x = 1\ny = 2\nfoo()\n"
        hits = _scan_file_for_symbols(source, "python", {"foo"})
        assert len(hits) == 1
        assert hits[0][1] == 3  # line 3


class TestFindReferencesIntegration:
    """Integration tests using the actual diffguard repo."""

    def test_finds_refs_in_own_repo(self, tmp_path):
        """Test that find_references works on a real git repo."""
        import subprocess

        # Create a small test repo
        repo = str(tmp_path)
        subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=repo, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=repo, capture_output=True, check=True,
        )

        # Create files
        (tmp_path / "lib.py").write_text("def helper():\n    return 42\n")
        (tmp_path / "main.py").write_text("from lib import helper\nhelper()\n")
        (tmp_path / "other.py").write_text("x = 1\n")

        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=repo, capture_output=True, check=True,
        )

        refs = find_references(
            repo_path=repo,
            changed_symbols=["helper"],
            ref="HEAD",
            changed_files={"lib.py"},
        )

        assert len(refs) == 2  # import + call in main.py
        assert all(r.file_path == "main.py" for r in refs)
        assert any(r.context == "import" for r in refs)
        assert any(r.context == "call" for r in refs)

    def test_excludes_changed_files(self, tmp_path):
        """Files in the diff should be excluded from dep scanning."""
        import subprocess

        repo = str(tmp_path)
        subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=repo, capture_output=True, check=True)

        (tmp_path / "a.py").write_text("def foo(): pass\nfoo()\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)

        refs = find_references(
            repo_path=repo,
            changed_symbols=["foo"],
            ref="HEAD",
            changed_files={"a.py"},
        )
        assert refs == []

    def test_empty_symbols_returns_empty(self, tmp_path):
        refs = find_references(
            repo_path=str(tmp_path),
            changed_symbols=[],
            ref="HEAD",
            changed_files=set(),
        )
        assert refs == []


class TestGitGrepPreFilter:
    """Tests for git grep pre-filter."""

    def test_git_grep_finds_files(self, tmp_path):
        """git grep should find files containing the symbol."""
        import subprocess

        repo = str(tmp_path)
        subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=repo, capture_output=True, check=True)

        (tmp_path / "a.py").write_text("def helper(): pass\n")
        (tmp_path / "b.py").write_text("helper()\n")
        (tmp_path / "c.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)

        files = _git_grep_files(repo, {"helper"}, "HEAD")
        assert "a.py" in files
        assert "b.py" in files
        assert "c.py" not in files

    def test_git_grep_reduces_scan(self, tmp_path):
        """Pre-filter should reduce files scanned vs scanning all."""
        import subprocess

        repo = str(tmp_path)
        subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=repo, capture_output=True, check=True)

        # Create many files, only 1 references the symbol
        for i in range(20):
            (tmp_path / f"file_{i}.py").write_text(f"x_{i} = {i}\n")
        (tmp_path / "caller.py").write_text("from lib import target_func\ntarget_func()\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)

        files = _git_grep_files(repo, {"target_func"}, "HEAD")
        assert len(files) == 1
        assert "caller.py" in files
