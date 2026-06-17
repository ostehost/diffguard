"""Git subprocess access — run git and return its raw output.

All git subprocess calls live here. Nothing else touches git. Parsing of
the unified-diff text these functions return lives in :mod:`diffguard.diff`.
"""

from __future__ import annotations

import logging
import subprocess
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
