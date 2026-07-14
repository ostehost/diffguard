"""Unified-diff parsing — text in, structured ``FileDiff`` objects out.

Pure parsing of git's textual diff output. No subprocess calls, no git
access; :mod:`diffguard.git` produces the text this module consumes.
"""

from __future__ import annotations

import posixpath
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


def _decode_git_quoted_path(token: str) -> str:
    """Decode one C-quoted path token from a Git patch header."""
    if not (token.startswith('"') and token.endswith('"')):
        return token

    escapes = {
        "a": "\a",
        "b": "\b",
        "t": "\t",
        "n": "\n",
        "v": "\v",
        "f": "\f",
        "r": "\r",
        "\\": "\\",
        '"': '"',
    }
    raw = token[1:-1]
    decoded: list[str] = []
    index = 0
    while index < len(raw):
        if raw[index] != "\\":
            decoded.append(raw[index])
            index += 1
            continue
        if index + 1 >= len(raw):
            raise ValueError("Incomplete Git path escape")

        if raw[index + 1] in "01234567":
            octets = bytearray()
            while index + 1 < len(raw) and raw[index] == "\\" and raw[index + 1] in "01234567":
                end = index + 1
                while end < len(raw) and end < index + 4 and raw[end] in "01234567":
                    end += 1
                octets.append(int(raw[index + 1 : end], 8))
                index = end
            decoded.append(octets.decode("utf-8", errors="surrogateescape"))
            continue

        escaped = escapes.get(raw[index + 1])
        if escaped is None:
            raise ValueError(f"Unsupported Git path escape: \\{raw[index + 1]}")
        decoded.append(escaped)
        index += 2
    return "".join(decoded)


def _parse_git_header_paths(line: str) -> tuple[str, str] | None:
    """Return old/new paths from a no-renames ``diff --git`` header."""
    prefix = "diff --git "
    if not line.startswith(prefix):
        return None
    payload = line[len(prefix) :]
    if payload.startswith('"'):
        escaped = False
        closing = -1
        for index, char in enumerate(payload[1:], start=1):
            if char == '"' and not escaped:
                closing = index
                break
            escaped = char == "\\" and not escaped
            if char != "\\":
                escaped = False
        if closing == -1 or closing + 2 > len(payload):
            return None
        first = payload[: closing + 1]
        second = payload[closing + 1 :].lstrip()
        try:
            old_token = _decode_git_quoted_path(first)
            new_token = _decode_git_quoted_path(second)
        except (SyntaxError, ValueError):
            return None
        if old_token.startswith("a/") and new_token.startswith("b/"):
            return old_token[2:], new_token[2:]
        return None

    if not payload.startswith("a/"):
        return None
    candidates: list[tuple[str, str]] = []
    search_from = 0
    while True:
        separator = payload.find(" b/", search_from)
        if separator == -1:
            break
        candidates.append((payload[2:separator], payload[separator + 3 :]))
        search_from = separator + 1
    for old_path, new_path in candidates:
        if old_path == new_path:
            return old_path, new_path
    return candidates[-1] if candidates else None


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


def _normalized_path_key(path: str) -> str:
    """Return a lexical key for comparing repository-relative Git paths."""
    return posixpath.normpath(path) if path else ""


def _coalesce_recreated_files(files: list[FileDiff]) -> list[FileDiff]:
    """Reconcile one split removal/addition for a path into a modification.

    ``get_worktree_diff`` appends one no-index patch per untracked file after
    Git's tracked-file patch. A tracked deletion that is recreated as an
    untracked file can therefore be separated from its addition by arbitrary
    records. Only an unambiguous pair is folded: exactly two records for the
    normalized path, with a well-formed removal preceding a well-formed
    addition. Other duplicate-path sequences are preserved as supplied.
    """
    records_by_path: dict[str, list[tuple[int, FileDiff]]] = {}
    for index, file_diff in enumerate(files):
        key = _normalized_path_key(file_diff.path)
        if key:
            records_by_path.setdefault(key, []).append((index, file_diff))

    additions_to_remove: set[int] = set()
    for records in records_by_path.values():
        if len(records) != 2:
            continue
        (removed_index, removed), (added_index, added) = records
        if not (
            removed_index < added_index
            and removed.change_type == "removed"
            and removed.old_path is not None
            and removed.new_path is None
            and added.change_type == "added"
            and added.old_path is None
            and added.new_path is not None
        ):
            continue

        removed.new_path = added.new_path
        removed.change_type = "modified"
        removed.binary = removed.binary or added.binary
        removed.generated = removed.generated or added.generated
        removed.hunks.extend(added.hunks)
        additions_to_remove.add(added_index)

    return [file_diff for index, file_diff in enumerate(files) if index not in additions_to_remove]


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
        paths = _parse_git_header_paths(lines[i])
        if paths is None:
            i += 1
            continue

        header_old_path, header_new_path = paths
        old_path, new_path, change_type, is_binary, i = _parse_file_header(
            lines, i + 1, header_old_path, header_new_path
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

    # Git uses split delete/add records for mode changes, and the worktree
    # assembler can produce the same shape non-adjacently when a staged
    # deletion is recreated as an untracked file. Reconcile after parsing so
    # unrelated intervening records retain their order.
    return _coalesce_recreated_files(files)


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
