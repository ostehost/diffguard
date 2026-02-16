"""Git diff parsing and file retrieval.

All git subprocess calls live here. Nothing else touches git.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

DEFAULT_GENERATED_PATTERNS: tuple[str, ...] = (
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "Pipfile.lock",
    "Cargo.lock",
    "go.sum",
    "composer.lock",
    "Gemfile.lock",
    "flake.lock",
    ".min.js",
    ".min.css",
    ".map",
    "vendor/",
    "node_modules/",
    "third_party/",
    "__generated__/",
    ".pb.go",
    "_generated.go",
)


@dataclass(frozen=True)
class HunkHeader:
    """Parsed @@ header."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    section: str = ""


@dataclass(frozen=True)
class DiffLine:
    """A single line from a diff hunk."""

    origin: Literal["+", "-", " "]
    content: str
    old_lineno: int | None = None
    new_lineno: int | None = None


@dataclass
class DiffHunk:
    """A contiguous hunk."""

    header: HunkHeader
    lines: list[DiffLine] = field(default_factory=list)


@dataclass
class FileDiff:
    """Parsed diff for a single file."""

    old_path: str | None  # None for new files
    new_path: str | None  # None for deleted files
    change_type: Literal["added", "removed", "modified"]
    binary: bool = False
    generated: bool = False
    hunks: list[DiffHunk] = field(default_factory=list)

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""

    @property
    def additions(self) -> int:
        return sum(1 for h in self.hunks for ln in h.lines if ln.origin == "+")

    @property
    def deletions(self) -> int:
        return sum(1 for h in self.hunks for ln in h.lines if ln.origin == "-")


_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)?$")


def is_generated(path: str, patterns: tuple[str, ...] = DEFAULT_GENERATED_PATTERNS) -> bool:
    """Check whether a file path matches generated/vendored patterns."""
    for pat in patterns:
        if pat.endswith("/"):  # directory prefix
            if f"/{pat}" in f"/{path}" or path.startswith(pat):
                return True
        elif pat.startswith("."):  # extension/suffix
            if path.endswith(pat):
                return True
        else:  # exact filename (basename)
            if path == pat or path.endswith(f"/{pat}"):
                return True
    return False


def parse_diff(
    diff_text: str,
    generated_patterns: tuple[str, ...] = DEFAULT_GENERATED_PATTERNS,
    *,
    skip_generated: bool = False,
) -> list[FileDiff]:
    """Parse unified diff text into structured FileDiff objects."""
    files: list[FileDiff] = []
    lines = diff_text.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i]

        # Look for "diff --git" header
        if not line.startswith("diff --git "):
            i += 1
            continue

        # Parse the diff --git a/X b/Y header
        match = re.match(r"^diff --git a/(.*) b/(.*)$", line)
        if not match:
            i += 1
            continue

        a_path = match.group(1)
        b_path = match.group(2)
        i += 1

        # Consume extended header lines
        old_path: str | None = a_path
        new_path: str | None = b_path
        is_binary = False
        change_type: Literal["added", "removed", "modified"] = "modified"

        while i < len(lines) and not lines[i].startswith("diff --git "):
            hdr = lines[i]

            if hdr.startswith("Binary files"):
                is_binary = True
                i += 1
                break

            if hdr.startswith("new file mode"):
                change_type = "added"
                old_path = None
                i += 1
                continue

            if hdr.startswith("deleted file mode"):
                change_type = "removed"
                new_path = None
                i += 1
                continue

            if hdr.startswith("--- "):
                if hdr == "--- /dev/null":
                    old_path = None
                    change_type = "added"
                i += 1
                continue

            if hdr.startswith("+++ "):
                if hdr == "+++ /dev/null":
                    new_path = None
                    change_type = "removed"
                i += 1
                continue

            if hdr.startswith("@@"):
                break  # Start of hunks

            # Other extended headers (index, similarity, etc.)
            i += 1
            continue

        # Determine canonical path for generated check
        canonical = new_path or old_path or ""
        gen = False if skip_generated else is_generated(canonical, generated_patterns)

        file_diff = FileDiff(
            old_path=old_path,
            new_path=new_path,
            change_type=change_type,
            binary=is_binary,
            generated=gen,
        )

        # Skip hunk parsing for binary files
        if is_binary:
            files.append(file_diff)
            continue

        # Parse hunks
        while i < len(lines) and not lines[i].startswith("diff --git "):
            if lines[i].startswith("@@"):
                hunk_match = _HUNK_RE.match(lines[i])
                if not hunk_match:
                    i += 1
                    continue

                header = HunkHeader(
                    old_start=int(hunk_match.group(1)),
                    old_count=int(hunk_match.group(2) or "1"),
                    new_start=int(hunk_match.group(3)),
                    new_count=int(hunk_match.group(4) or "1"),
                    section=hunk_match.group(5).strip() if hunk_match.group(5) else "",
                )
                hunk = DiffHunk(header=header)
                i += 1

                old_ln = header.old_start
                new_ln = header.new_start

                while i < len(lines) and not lines[i].startswith(("diff --git ", "@@")):
                    dl = lines[i]
                    if dl.startswith("+"):
                        hunk.lines.append(
                            DiffLine(
                                origin="+",
                                content=dl[1:],
                                old_lineno=None,
                                new_lineno=new_ln,
                            )
                        )
                        new_ln += 1
                    elif dl.startswith("-"):
                        hunk.lines.append(
                            DiffLine(
                                origin="-",
                                content=dl[1:],
                                old_lineno=old_ln,
                                new_lineno=None,
                            )
                        )
                        old_ln += 1
                    elif dl.startswith(" "):
                        hunk.lines.append(
                            DiffLine(
                                origin=" ",
                                content=dl[1:],
                                old_lineno=old_ln,
                                new_lineno=new_ln,
                            )
                        )
                        old_ln += 1
                        new_ln += 1
                    elif dl.startswith("\\ No newline at end of file"):
                        i += 1
                        continue
                    else:
                        # Empty context line (blank line in diff)
                        if dl == "":
                            # Could be end of diff or empty context line
                            # Check if next line continues the diff
                            if i + 1 < len(lines) and lines[i + 1].startswith(
                                ("diff --git ", "@@", "+", "-", " ", "\\ ")
                            ):
                                # empty context line
                                hunk.lines.append(
                                    DiffLine(
                                        origin=" ",
                                        content="",
                                        old_lineno=old_ln,
                                        new_lineno=new_ln,
                                    )
                                )
                                old_ln += 1
                                new_ln += 1
                            else:
                                i += 1
                                break
                        else:
                            i += 1
                            continue
                    i += 1

                file_diff.hunks.append(hunk)
            else:
                i += 1

        files.append(file_diff)

    return files


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
