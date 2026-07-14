"""Git subprocess access — run git and return its raw output.

All git subprocess calls live here. Nothing else touches git. Parsing of
the unified-diff text these functions return lives in :mod:`diffguard.diff`.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from contextlib import ExitStack
import logging
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

logger = logging.getLogger(__name__)

_GIT_TEXT_ENCODING = sys.getfilesystemencoding()
_MACHINE_DIFF_ARGS = (
    "--no-color",
    "--no-ext-diff",
    "--no-textconv",
    "--src-prefix=a/",
    "--dst-prefix=b/",
    "--no-renames",
)
_SUPPORTED_SOURCE_SUFFIXES = (".go", ".js", ".jsx", ".py", ".ts", ".tsx")
_DISABLE_FSMONITOR_ARGS = ("-c", "core.fsmonitor=false")


def _terminal_safe_log_text(value: str) -> str:
    """Escape untrusted text for one physical log line.

    Repository paths, refs, and Git stderr can contain terminal controls. Keep
    the operational value and raised exception untouched, but make every
    non-printable code point visible before it reaches the logging boundary.
    """
    chars: list[str] = []
    for char in value:
        if char == "\n":
            chars.append("\\n")
            continue
        if char == "\r":
            chars.append("\\r")
            continue
        if char == "\t":
            chars.append("\\t")
            continue

        codepoint = ord(char)
        if 0xDC80 <= codepoint <= 0xDCFF:
            chars.append(f"\\x{codepoint - 0xDC00:02x}")
        elif char.isprintable():
            chars.append(char)
        elif codepoint <= 0xFF:
            chars.append(f"\\x{codepoint:02x}")
        elif codepoint <= 0xFFFF:
            chars.append(f"\\u{codepoint:04x}")
        else:
            chars.append(f"\\U{codepoint:08x}")
    return "".join(chars)


def _log_error(message: str) -> None:
    """Log one error after crossing the terminal-safe presentation boundary."""
    logger.error("%s", _terminal_safe_log_text(message))


def _decode_git_path_record(stdout: bytes) -> str:
    """Decode one path record while preserving every path byte.

    Path-valued Git output is captured as bytes so universal-newline decoding
    cannot mistake a legitimate trailing carriage return for part of Git's
    record terminator.  Consume exactly one native line ending, accepting a
    lone LF on Windows as well because Git for Windows may emit Unix endings.
    """
    terminator = os.linesep.encode("ascii")
    if stdout.endswith(terminator):
        stdout = stdout[: -len(terminator)]
    elif terminator != b"\n" and stdout.endswith(b"\n"):
        stdout = stdout[:-1]
    return stdout.decode(_GIT_TEXT_ENCODING, errors="surrogateescape")


def _split_diff_records(diff_text: str) -> list[str]:
    """Split a Git patch at file-record boundaries without parsing its hunks."""
    records: list[str] = []
    current: list[str] = []
    for line in diff_text.splitlines(keepends=True):
        if line.startswith("diff --git ") and current:
            records.append("".join(current))
            current = []
        current.append(line)
    if current:
        records.append("".join(current))
    return records


def _supported_binary_record(record: str) -> bool:
    """Return whether a binary record names a supported source-language path."""
    if "\nBinary files " not in record:
        return False
    header = record.split("\n", 1)[0]
    return any(
        header.endswith(suffix) or header.endswith(f'{suffix}"')
        for suffix in _SUPPORTED_SOURCE_SUFFIXES
    )


def _force_supported_binary_records_to_text(
    diff_text: str,
    command: list[str],
    repo_path: str | Path,
    expected_returncode: int,
) -> str:
    """Rerun only when attributes hide supported source changes as binary.

    The ordinary machine diff retains Git's binary classification. If a
    supported source path is nevertheless suppressed by an attribute such as
    ``*.py -diff``, a textual rerun supplies that record while unrelated
    binary records remain untouched.
    """
    records = _split_diff_records(diff_text)
    if not any(_supported_binary_record(record) for record in records):
        return diff_text

    diff_index = command.index("diff")
    text_command = [*command[: diff_index + 1], "--text", *command[diff_index + 1 :]]
    text_result = subprocess.run(
        text_command,
        capture_output=True,
        text=True,
        encoding=_GIT_TEXT_ENCODING,
        errors="surrogateescape",
        cwd=str(repo_path),
        check=False,
    )
    if text_result.returncode != expected_returncode:
        _raise_git_error(text_result.stderr, repo_path, "git textual diff failed")

    text_records: dict[str, deque[str]] = {}
    for record in _split_diff_records(text_result.stdout):
        header = record.split("\n", 1)[0]
        text_records.setdefault(header, deque()).append(record)

    merged: list[str] = []
    for record in records:
        header = record.split("\n", 1)[0]
        queued_records = text_records.get(header)
        replacement = queued_records.popleft() if queued_records else None
        if not _supported_binary_record(record):
            merged.append(record)
            continue
        if replacement is None:
            raise RuntimeError(f"Git textual diff omitted supported source record: {header}")
        merged.append(replacement)
    return "".join(merged)


def _raise_git_error(stderr: str, repo_path: str | Path, fallback: str) -> NoReturn:
    """Log and raise a RuntimeError from a failed git invocation's stderr.

    Special-cases the not-a-repository error; otherwise prefixes *fallback* with
    git's first stderr line.
    """
    stderr = stderr.strip()
    first_line = stderr.split("\n")[0] if stderr else "unknown error"
    if "not a git repository" in stderr.lower():
        msg = f"Not a git repository: {repo_path}"
    else:
        msg = f"{fallback}: {first_line}"
    _log_error(msg)
    raise RuntimeError(msg)


def is_git_repository(repo_path: str | Path = ".") -> bool:
    """Return whether *repo_path* is inside a Git worktree."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            encoding=_GIT_TEXT_ENCODING,
            errors="surrogateescape",
            cwd=str(repo_path),
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


def get_repository_root(repo_path: str | Path = ".") -> Path:
    """Return the top-level directory for the worktree containing *repo_path*.

    Git diff paths are rooted at the worktree top level even when Git is
    invoked from a subdirectory. Callers that combine those paths with index or
    worktree content must therefore bind all operations to this one root.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            cwd=str(repo_path),
            check=False,
        )
    except OSError as exc:
        msg = f"Not a git repository: {repo_path}"
        _log_error(msg)
        raise RuntimeError(msg) from exc
    if result.returncode != 0:
        stderr = result.stderr.decode(_GIT_TEXT_ENCODING, errors="surrogateescape")
        _raise_git_error(stderr, repo_path, "git repository root lookup failed")
    root = _decode_git_path_record(result.stdout)
    if not root:
        raise RuntimeError(f"Git returned an empty repository root for: {repo_path}")
    return Path(root)


def get_hooks_dir(repo_path: str | Path = ".") -> Path:
    """Return Git's configured hooks directory for *repo_path*.

    ``git rev-parse --git-path hooks`` accounts for linked worktrees and
    ``core.hooksPath``. Relative results are interpreted from Git's working
    directory, matching Git's own hook lookup behavior.
    """
    if not is_git_repository(repo_path):
        raise RuntimeError(f"Not a git repository: {repo_path}")
    result = subprocess.run(
        ["git", "rev-parse", "--git-path", "hooks"],
        capture_output=True,
        cwd=str(repo_path),
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode(_GIT_TEXT_ENCODING, errors="surrogateescape")
        _raise_git_error(stderr, repo_path, "git hook path lookup failed")
    raw_path = _decode_git_path_record(result.stdout)
    if not raw_path:
        raise RuntimeError(f"Git returned an empty hooks path for: {repo_path}")
    hooks_path = Path(raw_path)
    if not hooks_path.is_absolute():
        hooks_path = Path(repo_path) / hooks_path
    return hooks_path.resolve()


def get_diff(
    ref_range: str,
    repo_path: str | Path = ".",
) -> str:
    """Run git diff and return raw unified diff text."""
    command = [
        "git",
        "-c",
        "core.quotePath=false",
        "diff",
        *_MACHINE_DIFF_ARGS,
        "--end-of-options",
        ref_range,
        "--",
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding=_GIT_TEXT_ENCODING,
        errors="surrogateescape",
        cwd=str(repo_path),
        check=False,
    )
    if result.returncode != 0:
        lower = result.stderr.lower()
        if "unknown revision" in lower or "bad revision" in lower:
            first_line = result.stderr.strip().split("\n")[0]
            msg = f"Invalid ref range '{ref_range}': {first_line}"
            _log_error(msg)
            raise RuntimeError(msg)
        _raise_git_error(result.stderr, repo_path, "git diff failed")
    return _force_supported_binary_records_to_text(
        result.stdout, command, repo_path, result.returncode
    )


def get_staged_diff(repo_path: str | Path = ".") -> str:
    """Run git diff for staged/index changes and return raw unified diff text."""
    command = [
        "git",
        "-c",
        "core.quotePath=false",
        *_DISABLE_FSMONITOR_ARGS,
        "diff",
        *_MACHINE_DIFF_ARGS,
        "--cached",
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding=_GIT_TEXT_ENCODING,
        errors="surrogateescape",
        cwd=str(repo_path),
        check=False,
    )
    if result.returncode != 0:
        _raise_git_error(result.stderr, repo_path, "git diff --cached failed")
    return _force_supported_binary_records_to_text(
        result.stdout, command, repo_path, result.returncode
    )


def resolve_commit(ref: str, repo_path: str | Path = ".") -> str | None:
    """Resolve *ref* to a commit SHA, or return ``None`` when invalid."""
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"],
        capture_output=True,
        text=True,
        encoding=_GIT_TEXT_ENCODING,
        errors="surrogateescape",
        cwd=str(repo_path),
        check=False,
    )
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def _list_untracked_files(repo_path: str | Path = ".") -> list[str]:
    """List untracked, non-ignored worktree files."""
    result = subprocess.run(
        [
            "git",
            *_DISABLE_FSMONITOR_ARGS,
            "ls-files",
            "-z",
            "--others",
            "--exclude-standard",
        ],
        capture_output=True,
        text=True,
        encoding=_GIT_TEXT_ENCODING,
        errors="surrogateescape",
        cwd=str(repo_path),
        check=False,
    )
    if result.returncode != 0:
        _raise_git_error(result.stderr, repo_path, "git ls-files failed")
    return [path for path in result.stdout.split("\0") if path]


def _quote_git_patch_path(path: str) -> str:
    """Return a lossless C-quoted path token for a Git patch header.

    ``git ls-files -z`` exposes repository paths as filesystem-decoded text
    with ``surrogateescape``. Re-encoding with the same contract and quoting
    unsafe octets preserves tabs, newlines, quotes, backslashes, and path bytes
    that are not valid in the filesystem encoding. Always quoting also avoids
    ambiguity between the two paths in a ``diff --git`` header.
    """
    raw_path = path.encode(_GIT_TEXT_ENCODING, errors="surrogateescape")
    encoded: list[str] = []
    for octet in raw_path:
        if 0x20 <= octet <= 0x7E and octet not in {ord('"'), ord("\\")}:
            encoded.append(chr(octet))
        else:
            encoded.append(f"\\{octet:03o}")
    return f'"{"".join(encoded)}"'


def _lstat_regular_untracked_file(
    file_path: str,
    repo_path: str | Path,
) -> os.stat_result:
    """Preflight one untracked path without following its final component.

    This rejects entries already known to be non-regular before Git opens the
    pathname. It does not make the mutable worktree lookup atomic.
    """
    try:
        file_stat = os.lstat(Path(repo_path) / file_path)
    except OSError as exc:
        _raise_git_error(str(exc), repo_path, f"git diff failed for {file_path}")
    if not stat.S_ISREG(file_stat.st_mode):
        _raise_git_error(
            "untracked diff target is not a regular file",
            repo_path,
            f"git diff failed for {file_path}",
        )
    return file_stat


def _empty_untracked_diff(
    file_path: str,
    repo_path: str | Path,
) -> str:
    """Synthesize Git's header-only addition for an empty untracked file."""
    file_stat = _lstat_regular_untracked_file(file_path, repo_path)
    if file_stat.st_size != 0:
        _raise_git_error(
            "git diff --no-index produced no patch for a non-empty file",
            repo_path,
            f"git diff failed for {file_path}",
        )

    git_mode = "100755" if file_stat.st_mode & stat.S_IXUSR else "100644"
    old_path = _quote_git_patch_path(f"a/{file_path}")
    new_path = _quote_git_patch_path(f"b/{file_path}")
    return f"diff --git {old_path} {new_path}\nnew file mode {git_mode}\n"


def _canonicalize_dash_patch_path(patch_text: str) -> str:
    """Restore repo path ``-`` after invoking no-index diff with ``./-``."""
    lines = patch_text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith(("@@", "GIT binary patch")):
            break
        if line.startswith(("diff --git ", "+++ ", "Binary files ")):
            lines[index] = line.replace("a/./-", "a/-").replace("b/./-", "b/-")
    return "".join(lines)


def _empty_blob_oid(repo_path: str | Path) -> str:
    """Return Git's full empty-blob object ID for this repository format."""
    result = subprocess.run(
        ["git", "hash-object", "--stdin"],
        input="",
        capture_output=True,
        text=True,
        encoding=_GIT_TEXT_ENCODING,
        errors="surrogateescape",
        cwd=str(repo_path),
        check=False,
    )
    if result.returncode != 0:
        _raise_git_error(result.stderr, repo_path, "git empty-blob hash failed")

    if not result.stdout.endswith("\n") or result.stdout.count("\n") != 1:
        _raise_git_error(
            "git hash-object returned a malformed object ID",
            repo_path,
            "git empty-blob hash failed",
        )
    object_id = result.stdout[:-1]
    if len(object_id) < 4 or any(char not in "0123456789abcdef" for char in object_id):
        _raise_git_error(
            "git hash-object returned a malformed object ID",
            repo_path,
            "git empty-blob hash failed",
        )
    return object_id


def _is_complete_empty_addition(record: str, empty_blob_oid: str | None) -> bool:
    """Validate Git's exact header-only record for one empty regular file."""
    if empty_blob_oid is None:
        return False
    lines = record.splitlines(keepends=True)
    if len(lines) != 3:
        return False
    if lines[1] not in {"new file mode 100644\n", "new file mode 100755\n"}:
        return False
    index_match = re.fullmatch(r"index (0+)\.\.([0-9a-f]+)\n", lines[2])
    if index_match is None:
        return False
    zero_oid, abbreviated_oid = index_match.groups()
    return (
        len(zero_oid) == len(abbreviated_oid)
        and 4 <= len(abbreviated_oid) <= len(empty_blob_oid)
        and empty_blob_oid.startswith(abbreviated_oid)
    )


def _is_complete_untracked_patch(
    patch_text: str,
    *,
    empty_blob_oid: str | None,
) -> bool:
    """Return whether no-index output contains one complete file-addition record."""
    records = _split_diff_records(patch_text)
    if len(records) != 1:
        return False
    record = records[0]
    if not record.startswith("diff --git ") or "\nnew file mode " not in record:
        return False
    if empty_blob_oid is not None:
        return _is_complete_empty_addition(record, empty_blob_oid)
    text_addition = "\n--- /dev/null\n" in record and "\n+++ " in record and "\n@@ " in record
    binary_addition = "\nBinary files /dev/null and " in record
    return text_addition or binary_addition


def get_worktree_diff(base_ref: str, repo_path: str | Path = ".") -> str:
    """Diff a base commit against staged, unstaged, and untracked worktree state."""
    command = [
        "git",
        "-c",
        "core.quotePath=false",
        "diff",
        *_MACHINE_DIFF_ARGS,
        "--end-of-options",
        base_ref,
        "--",
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding=_GIT_TEXT_ENCODING,
        errors="surrogateescape",
        cwd=str(repo_path),
        check=False,
    )
    if result.returncode != 0:
        _raise_git_error(result.stderr, repo_path, "git worktree diff failed")

    parts = [
        _force_supported_binary_records_to_text(
            result.stdout, command, repo_path, result.returncode
        )
    ]
    cached_empty_blob_oid: str | None = None
    for file_path in _list_untracked_files(repo_path):
        file_stat = _lstat_regular_untracked_file(file_path, repo_path)
        diff_operand = "./-" if file_path == "-" else file_path
        untracked_command = [
            "git",
            "-c",
            "core.quotePath=false",
            "-c",
            "core.safecrlf=false",
            "diff",
            *_MACHINE_DIFF_ARGS,
            "--no-index",
            "--",
            "/dev/null",
            diff_operand,
        ]
        untracked = subprocess.run(
            untracked_command,
            capture_output=True,
            text=True,
            encoding=_GIT_TEXT_ENCODING,
            errors="surrogateescape",
            cwd=str(repo_path),
            check=False,
        )
        if untracked.returncode not in (0, 1):
            _raise_git_error(untracked.stderr, repo_path, f"git diff failed for {file_path}")
        if untracked.stderr.strip():
            _raise_git_error(untracked.stderr, repo_path, f"git diff failed for {file_path}")
        expected_empty_blob_oid: str | None = None
        if untracked.stdout and file_stat.st_size == 0:
            if cached_empty_blob_oid is None:
                cached_empty_blob_oid = _empty_blob_oid(repo_path)
            expected_empty_blob_oid = cached_empty_blob_oid
        has_patch = _is_complete_untracked_patch(
            untracked.stdout,
            empty_blob_oid=expected_empty_blob_oid,
        )
        if (untracked.returncode == 1 and not has_patch) or (untracked.stdout and not has_patch):
            _raise_git_error(
                "git diff --no-index produced no valid patch",
                repo_path,
                f"git diff failed for {file_path}",
            )
        if untracked.stdout:
            patch_text = _force_supported_binary_records_to_text(
                untracked.stdout, untracked_command, repo_path, untracked.returncode
            )
            if file_path == "-":
                patch_text = _canonicalize_dash_patch_path(patch_text)
            parts.append(patch_text)
        else:
            parts.append(_empty_untracked_diff(file_path, repo_path))
    return "".join(parts)


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
        ["git", "merge-base", "--end-of-options", ref_a, ref_b],
        capture_output=True,
        text=True,
        encoding=_GIT_TEXT_ENCODING,
        errors="surrogateescape",
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
    """Retrieve strict UTF-8 file content at a ref, or ``None`` if unavailable."""
    result = subprocess.run(
        ["git", "show", "--end-of-options", f"{ref}:{file_path}"],
        capture_output=True,
        cwd=str(repo_path),
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None


def get_file_from_index(
    file_path: str,
    repo_path: str | Path = ".",
) -> str | None:
    """Retrieve strict UTF-8 index content, or ``None`` if unavailable."""
    result = subprocess.run(
        ["git", "show", f":{file_path}"],
        capture_output=True,
        cwd=str(repo_path),
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None


def get_file_from_worktree(
    file_path: str,
    repo_path: str | Path = ".",
) -> str | None:
    """Read a regular UTF-8 worktree file, or ``None`` when unsafe/unavailable."""
    components = file_path.split("/")
    if (
        not file_path
        or "\0" in file_path
        or file_path.startswith("/")
        or any(component in {"", ".", ".."} for component in components)
        or (os.name == "nt" and any("\\" in component for component in components))
    ):
        return None

    try:
        root = Path(repo_path).resolve(strict=True)
        if not root.is_dir():
            return None

        # On POSIX, walk the path relative to an already-open repository
        # directory and refuse symlinks at every component. Checking only the
        # final path lets a replaced parent directory escape the worktree.
        no_follow = getattr(os, "O_NOFOLLOW", 0)
        if no_follow and os.open in os.supports_dir_fd:
            directory_flags = (
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_DIRECTORY", 0)
                | no_follow
            )
            file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | no_follow
            with ExitStack() as stack:
                directory_fd = os.open(root, directory_flags)
                stack.callback(os.close, directory_fd)
                for component in components[:-1]:
                    directory_fd = os.open(
                        component,
                        directory_flags,
                        dir_fd=directory_fd,
                    )
                    stack.callback(os.close, directory_fd)

                file_fd = os.open(components[-1], file_flags, dir_fd=directory_fd)
                try:
                    if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                        return None
                    handle = os.fdopen(file_fd, "r", encoding="utf-8")
                    file_fd = -1
                    with handle:
                        return handle.read()
                finally:
                    if file_fd >= 0:
                        os.close(file_fd)

        # Conservative fallback for platforms without openat/O_NOFOLLOW.
        # Re-check every component before resolving and require containment.
        path = root
        for component in components:
            path /= component
            if path.is_symlink():
                return None
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root) or not resolved.is_file():
            return None
        return resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def get_file_at_snapshot(
    ref: str,
    file_path: str,
    repo_path: str | Path = ".",
) -> str | None:
    """Retrieve content from a commit, the index, or the current worktree."""
    if ref == ":worktree":
        return get_file_from_worktree(file_path, repo_path)
    if ref == ":index":
        return get_file_from_index(file_path, repo_path)
    return get_file_at_ref(ref, file_path, repo_path)


def _list_files_at_ref_with_status(
    ref: str,
    repo_path: str | Path = ".",
) -> tuple[list[str], bool]:
    """List tracked paths and whether the snapshot listing was definitive."""
    try:
        result = subprocess.run(
            [
                "git",
                "ls-tree",
                "-r",
                "-z",
                "--name-only",
                "--end-of-options",
                ref,
            ],
            capture_output=True,
            text=True,
            encoding=_GIT_TEXT_ENCODING,
            errors="surrogateescape",
            cwd=str(repo_path),
            check=False,
        )
    except OSError:
        return [], False
    if result.returncode != 0:
        return [], False
    return [path for path in result.stdout.split("\0") if path], True


def list_files_at_ref(ref: str, repo_path: str | Path = ".") -> list[str]:
    """List all tracked file paths at *ref* (empty list on failure)."""
    return _list_files_at_ref_with_status(ref, repo_path)[0]


def list_files_at_snapshot_with_status(
    ref: str,
    repo_path: str | Path = ".",
) -> tuple[list[str], bool]:
    """List snapshot paths and whether the listing was definitive."""
    if ref == ":worktree":
        args = ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    elif ref == ":index":
        args = ["git", "ls-files", "-z", "--cached"]
    else:
        return _list_files_at_ref_with_status(ref, repo_path)
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding=_GIT_TEXT_ENCODING,
            errors="surrogateescape",
            cwd=str(repo_path),
            check=False,
        )
    except OSError:
        return [], False
    if result.returncode != 0:
        return [], False
    paths = [path for path in result.stdout.split("\0") if path]
    if ref == ":worktree":
        return [path for path in paths if (Path(repo_path) / path).is_file()], True
    return paths, True


def list_files_at_snapshot(ref: str, repo_path: str | Path = ".") -> list[str]:
    """List supported snapshot paths for a commit, index, or worktree."""
    return list_files_at_snapshot_with_status(ref, repo_path)[0]


def grep_files(
    pattern: str,
    ref: str,
    repo_path: str | Path = ".",
    pathspecs: Sequence[str] = (),
) -> list[str] | None:
    """Return repo-relative paths at *ref* whose contents match *pattern*.

    The pattern is treated as a fixed string because callers pass raw symbol
    names, not regular expressions. Returns an empty list when nothing matches,
    and *None* when git grep cannot provide a definitive result — so the caller
    can fall back to scanning all files.
    """
    try:
        if ref == ":worktree":
            args = ["git", "grep", "-F", "-l", "-z", "--untracked", "-e", pattern, "--", *pathspecs]
        elif ref == ":index":
            args = ["git", "grep", "-F", "-l", "-z", "--cached", "-e", pattern, "--", *pathspecs]
        else:
            args = [
                "git",
                "grep",
                "-F",
                "-l",
                "-z",
                "-e",
                pattern,
                "--end-of-options",
                ref,
                "--",
                *pathspecs,
            ]
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding=_GIT_TEXT_ENCODING,
            errors="surrogateescape",
            cwd=str(repo_path),
            check=False,
        )
    except OSError:
        return None
    if result.returncode == 1:
        return []
    if result.returncode != 0:
        return None
    if not result.stdout.strip():
        return []
    paths: list[str] = []
    for line in result.stdout.split("\0"):
        if not line:
            continue
        # "git grep <ref>" prefixes each match with "ref:".
        if ref not in {":worktree", ":index"} and ":" in line:
            paths.append(line.split(":", 1)[1])
        else:
            paths.append(line)
    return paths
