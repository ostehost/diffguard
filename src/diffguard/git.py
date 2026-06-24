"""Git subprocess access — run git and return its raw output.

All git subprocess calls live here. Nothing else touches git. Parsing of
the unified-diff text these functions return lives in :mod:`diffguard.diff`.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Sequence
from pathlib import Path

logger = logging.getLogger(__name__)


def get_diff(
    ref_range: str,
    repo_path: str | Path = ".",
) -> str:
    """Run git diff and return raw unified diff text."""
    result = subprocess.run(
        ["git", "diff", "--no-renames", ref_range],
        capture_output=True,
        text=True,
        cwd=str(repo_path),
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        # Extract just the first meaningful line from git's stderr
        first_line = stderr.split("\n")[0] if stderr else "unknown error"
        if "not a git repository" in stderr.lower():
            msg = f"Not a git repository: {repo_path}"
        elif "unknown revision" in stderr.lower() or "bad revision" in stderr.lower():
            msg = f"Invalid ref range '{ref_range}': {first_line}"
        else:
            msg = f"git diff failed: {first_line}"
        logger.error(msg)
        raise RuntimeError(msg)
    return result.stdout


def get_staged_diff(repo_path: str | Path = ".") -> str:
    """Run git diff for staged/index changes and return raw unified diff text."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--no-renames"],
        capture_output=True,
        text=True,
        cwd=str(repo_path),
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        first_line = stderr.split("\n")[0] if stderr else "unknown error"
        if "not a git repository" in stderr.lower():
            msg = f"Not a git repository: {repo_path}"
        else:
            msg = f"git diff --cached failed: {first_line}"
        logger.error(msg)
        raise RuntimeError(msg)
    return result.stdout


def get_merge_base(
    ref_a: str,
    ref_b: str,
    repo_path: str | Path = ".",
) -> str | None:
    """Return the merge-base commit of two refs, or None if it can't be found.

    Used to resolve git's three-dot (``A...B``) symmetric-difference syntax to a
    concrete base commit so the diff and the symbol baseline agree.
    """
    result = subprocess.run(
        ["git", "merge-base", ref_a, ref_b],
        capture_output=True,
        text=True,
        cwd=str(repo_path),
        check=False,
    )
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def get_file_at_ref(
    ref: str,
    file_path: str,
    repo_path: str | Path = ".",
) -> str | None:
    """Retrieve file contents at a specific git ref. Returns None if missing."""
    result = subprocess.run(
        ["git", "show", f"{ref}:{file_path}"],
        capture_output=True,
        text=True,
        cwd=str(repo_path),
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def get_file_from_index(
    file_path: str,
    repo_path: str | Path = ".",
) -> str | None:
    """Retrieve staged file contents from the git index."""
    result = subprocess.run(
        ["git", "show", f":{file_path}"],
        capture_output=True,
        text=True,
        cwd=str(repo_path),
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def list_files_at_ref(ref: str, repo_path: str | Path = ".") -> list[str]:
    """List all tracked file paths at *ref* (empty list on failure)."""
    result = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", ref],
        capture_output=True,
        text=True,
        cwd=str(repo_path),
        check=False,
    )
    if result.returncode != 0:
        return []
    out = result.stdout.strip()
    return out.split("\n") if out else []


def grep_files(
    pattern: str,
    ref: str,
    repo_path: str | Path = ".",
    pathspecs: Sequence[str] = (),
) -> list[str] | None:
    """Return repo-relative paths at *ref* whose contents match *pattern*.

    Returns an empty list when nothing matches, and *None* when git grep is
    unavailable — so the caller can fall back to scanning all files.
    """
    try:
        result = subprocess.run(
            ["git", "grep", "-l", pattern, ref, "--", *pathspecs],
            capture_output=True,
            text=True,
            cwd=str(repo_path),
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return []
    paths: list[str] = []
    for line in result.stdout.strip().split("\n"):
        # "git grep <ref>" prefixes each match with "ref:".
        paths.append(line.split(":", 1)[1] if ":" in line else line)
    return paths
