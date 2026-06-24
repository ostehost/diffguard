"""Tests for git ref-range parsing (engine/_refs.py) and three-dot
merge-base normalization (cli._normalize_ref_range)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from diffguard.cli import _normalize_ref_range
from diffguard.engine._refs import split_ref_range
from diffguard.git import get_merge_base


class TestSplitRefRange:
    def test_two_dot_range(self) -> None:
        assert split_ref_range("HEAD~1..HEAD") == ("HEAD~1", "HEAD")

    def test_two_dot_named_refs(self) -> None:
        assert split_ref_range("main..feature") == ("main", "feature")

    def test_three_dot_fallback_uses_endpoints(self) -> None:
        # Direct/in-process callers that skip normalization get the endpoints.
        assert split_ref_range("main...feature") == ("main", "feature")

    def test_multi_dot_splits_on_first_separator(self) -> None:
        # No leading-dot leak, no nonsensical bare ref.
        assert split_ref_range("A..B..C") == ("A", "B..C")

    def test_omitted_left_endpoint_means_head(self) -> None:
        # git reads "..B" as "HEAD..B".
        assert split_ref_range("..B") == ("HEAD", "B")

    def test_omitted_right_endpoint_means_head(self) -> None:
        # git reads "A.." as "A..HEAD".
        assert split_ref_range("A..") == ("A", "HEAD")

    def test_lone_separator_is_head_to_head(self) -> None:
        assert split_ref_range("..") == ("HEAD", "HEAD")

    def test_omitted_endpoint_three_dot(self) -> None:
        assert split_ref_range("...feature") == ("HEAD", "feature")

    def test_bare_ref_resolves_to_parent(self) -> None:
        assert split_ref_range("HEAD") == ("HEAD~1", "HEAD")

    def test_bare_named_ref(self) -> None:
        assert split_ref_range("abc123") == ("abc123~1", "abc123")


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return out.stdout.strip()


def _make_diverged_repo(repo: Path) -> tuple[str, str]:
    """Build a repo where main advances past a feature branch's fork point.

    Returns (main_sha, feature_sha). The merge-base is the fork commit, NOT the
    current main tip — the case where three-dot vs two-dot diverge.
    """
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "core.hooksPath", ""], cwd=repo, check=True)

    (repo / "base.py").write_text("def base():\n    return 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "fork point")
    fork_sha = _git(repo, "rev-parse", "HEAD")

    # Feature branch off the fork point.
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "feature.py").write_text("def feature():\n    return 2\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "add feature")
    feature_sha = _git(repo, "rev-parse", "HEAD")

    # main advances independently after the fork.
    _git(repo, "checkout", "-q", "main")
    (repo / "unrelated.py").write_text("def unrelated():\n    return 3\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "unrelated work on main")

    return fork_sha, feature_sha


class TestMergeBaseNormalization:
    def test_get_merge_base(self, tmp_path: Path) -> None:
        fork_sha, _ = _make_diverged_repo(tmp_path)
        base = get_merge_base("main", "feature", str(tmp_path))
        assert base == fork_sha

    def test_get_merge_base_unrelated_returns_none(self, tmp_path: Path) -> None:
        assert get_merge_base("nope-a", "nope-b", str(tmp_path)) is None

    def test_three_dot_normalizes_to_merge_base(self, tmp_path: Path) -> None:
        fork_sha, _ = _make_diverged_repo(tmp_path)
        normalized = _normalize_ref_range("main...feature", str(tmp_path))
        assert normalized == f"{fork_sha}..feature"

    def test_two_dot_passes_through(self, tmp_path: Path) -> None:
        _make_diverged_repo(tmp_path)
        assert _normalize_ref_range("master..feature", str(tmp_path)) == "master..feature"

    def test_unresolvable_three_dot_left_untouched(self, tmp_path: Path) -> None:
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        assert _normalize_ref_range("nope-a...nope-b", str(tmp_path)) == "nope-a...nope-b"
