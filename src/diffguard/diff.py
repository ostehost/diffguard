"""Unified-diff parsing — text in, structured ``FileDiff`` objects out.

Pure parsing of git's textual diff output. No subprocess calls, no git
access; :mod:`diffguard.git` produces the text this module consumes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

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
        match = re.match(r"^diff --git a/(.*) b/(.*)$", lines[i])
        if match is None:
            i += 1
            continue

        old_path, new_path, change_type, is_binary, i = _parse_file_header(
            lines, i + 1, match.group(1), match.group(2)
        )

        canonical = new_path or old_path or ""
        file_diff = FileDiff(
            old_path=old_path,
            new_path=new_path,
            change_type=change_type,
            binary=is_binary,
            generated=False if skip_generated else is_generated(canonical, generated_patterns),
        )
        if not is_binary:
            i = _parse_hunks(lines, i, file_diff)
        files.append(file_diff)

    return files


def _parse_file_header(
    lines: list[str],
    i: int,
    a_path: str,
    b_path: str,
) -> tuple[str | None, str | None, Literal["added", "removed", "modified"], bool, int]:
    """Consume a file's extended header (mode / ---/+++ / Binary) lines.

    *i* indexes the line after ``diff --git``. Returns
    ``(old_path, new_path, change_type, is_binary, next_i)`` where *next_i*
    indexes the first hunk header (``@@``) or the following file.
    """
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
        if hdr.startswith("@@"):
            break  # leave i on the @@ line for the hunk parser
        if hdr.startswith("new file mode"):
            change_type = "added"
            old_path = None
        elif hdr.startswith("deleted file mode"):
            change_type = "removed"
            new_path = None
        elif hdr == "--- /dev/null":
            old_path = None
            change_type = "added"
        elif hdr == "+++ /dev/null":
            new_path = None
            change_type = "removed"
        # Other extended headers (index, similarity, plain ---/+++) carry no
        # path/type signal and are simply consumed.
        i += 1

    return old_path, new_path, change_type, is_binary, i


def _parse_hunks(lines: list[str], i: int, file_diff: FileDiff) -> int:
    """Parse every hunk of one file. Returns the index of the next file header."""
    while i < len(lines) and not lines[i].startswith("diff --git "):
        if not lines[i].startswith("@@"):
            i += 1
            continue
        match = _HUNK_RE.match(lines[i])
        if match is None:
            i += 1
            continue
        header = HunkHeader(
            old_start=int(match.group(1)),
            old_count=int(match.group(2) or "1"),
            new_start=int(match.group(3)),
            new_count=int(match.group(4) or "1"),
            section=match.group(5).strip() if match.group(5) else "",
        )
        hunk, i = _parse_hunk_body(lines, i, header)
        file_diff.hunks.append(hunk)
    return i


def _blank_is_context(lines: list[str], i: int) -> bool:
    """A blank diff line is an (empty) context line only when more diff content
    follows it; otherwise it marks the end of the diff."""
    return i + 1 < len(lines) and lines[i + 1].startswith(
        ("diff --git ", "@@", "+", "-", " ", "\\ ")
    )


def _parse_hunk_body(lines: list[str], i: int, header: HunkHeader) -> tuple[DiffHunk, int]:
    """Parse a hunk's body lines. *i* indexes the ``@@`` header; returns the
    populated hunk and the index of the first line past it."""
    hunk = DiffHunk(header=header)
    i += 1
    old_ln = header.old_start
    new_ln = header.new_start

    while i < len(lines) and not lines[i].startswith(("diff --git ", "@@")):
        dl = lines[i]
        if dl.startswith("+"):
            hunk.lines.append(DiffLine(origin="+", content=dl[1:], new_lineno=new_ln))
            new_ln += 1
        elif dl.startswith("-"):
            hunk.lines.append(DiffLine(origin="-", content=dl[1:], old_lineno=old_ln))
            old_ln += 1
        elif dl.startswith(" "):
            hunk.lines.append(
                DiffLine(origin=" ", content=dl[1:], old_lineno=old_ln, new_lineno=new_ln)
            )
            old_ln += 1
            new_ln += 1
        elif dl == "" and _blank_is_context(lines, i):
            hunk.lines.append(
                DiffLine(origin=" ", content="", old_lineno=old_ln, new_lineno=new_ln)
            )
            old_ln += 1
            new_ln += 1
        elif dl == "":
            i += 1
            break  # a blank line with nothing after it ends the diff
        # else: "\ No newline at end of file" / any other line — skip, don't record.
        i += 1

    return hunk, i
