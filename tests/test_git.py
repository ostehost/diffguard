"""Tests for diffguard.git snapshot helpers."""

from __future__ import annotations

import errno
import logging
import os
from pathlib import Path
import stat
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from diffguard.diff import parse_diff
from diffguard.engine.pipeline import run_pipeline
from diffguard.git import (
    _decode_git_path_record,
    _force_supported_binary_records_to_text,
    get_diff,
    get_file_at_snapshot,
    get_file_from_worktree,
    get_hooks_dir,
    get_repository_root,
    get_staged_diff,
    get_worktree_diff,
    grep_files,
    list_files_at_ref,
    list_files_at_snapshot,
)


def _write_bytes_path(repo: Path, relative_path: bytes, content: bytes) -> str:
    """Write a POSIX byte path and return its surrogate-safe string form."""
    decoded = os.fsdecode(relative_path)
    assert os.fsencode(decoded) == relative_path
    with open(os.path.join(os.fsencode(repo), relative_path), "wb") as handle:
        handle.write(content)
    return decoded


def _commit_all(repo: Path, message: str) -> None:
    """Commit the complete fixture worktree without relying on global config."""
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=T",
            "-c",
            "user.email=t@t.com",
            "-c",
            "core.hooksPath=",
            "commit",
            "-m",
            message,
        ],
        cwd=repo,
        capture_output=True,
        check=True,
    )


def _git_tree_with_bytes_path(repo: Path, relative_path: bytes, content: bytes) -> str:
    """Create a one-file Git tree without asking the filesystem to store its path."""
    blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo,
        input=content,
        capture_output=True,
        check=True,
    ).stdout.strip()
    entry = b"100644 blob " + blob + b"\t" + relative_path + b"\0"
    return (
        subprocess.run(
            ["git", "mktree", "-z"],
            cwd=repo,
            input=entry,
            capture_output=True,
            check=True,
        )
        .stdout.strip()
        .decode("ascii")
    )


def _commit_tree(repo: Path, tree: str, message: str, parent: str | None = None) -> str:
    """Commit a fixture tree through Git plumbing and return its object id."""
    args = [
        "git",
        "-c",
        "user.name=T",
        "-c",
        "user.email=t@t.com",
        "commit-tree",
        tree,
    ]
    if parent is not None:
        args.extend(["-p", parent])
    args.extend(["-m", message])
    return (
        subprocess.run(args, cwd=repo, capture_output=True, check=True)
        .stdout.strip()
        .decode("ascii")
    )


def test_repository_root_from_nested_directory(tmp_path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    nested = tmp_path / "package" / "internal"
    nested.mkdir(parents=True)

    assert get_repository_root(nested) == tmp_path


def test_repository_root_preserves_trailing_space(tmp_path: Path) -> None:
    repo = tmp_path / "repository "
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)

    assert get_repository_root(repo) == repo


def test_repository_root_consumes_only_one_platform_record_terminator() -> None:
    completed = subprocess.CompletedProcess(
        [],
        0,
        stdout=b"/repo\r\r\n",
        stderr=b"",
    )

    with (
        patch("diffguard.git.os.linesep", "\r\n"),
        patch("diffguard.git.subprocess.run", return_value=completed) as run,
    ):
        root = get_repository_root("/repo")

    assert root == Path("/repo\r")
    assert "text" not in run.call_args.kwargs


@pytest.mark.skipif(os.name == "nt", reason="Windows paths cannot end in carriage return")
def test_repository_root_preserves_trailing_carriage_return(tmp_path: Path) -> None:
    repo = tmp_path / "repository\r"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)

    assert get_repository_root(repo) == repo


def test_hooks_dir_preserves_relative_core_hooks_path_trailing_space(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "core.hooksPath", "hooks "],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )

    assert get_hooks_dir(tmp_path) == tmp_path / "hooks "


def test_hooks_dir_consumes_only_one_platform_record_terminator() -> None:
    completed = subprocess.CompletedProcess(
        [],
        0,
        stdout=b"hooks\r\r\n",
        stderr=b"",
    )

    with (
        patch("diffguard.git.os.linesep", "\r\n"),
        patch("diffguard.git.is_git_repository", return_value=True),
        patch("diffguard.git.subprocess.run", return_value=completed) as run,
    ):
        hooks_dir = get_hooks_dir("/repo")

    assert hooks_dir == (Path("/repo") / "hooks\r").resolve()
    assert "text" not in run.call_args.kwargs


@pytest.mark.skipif(os.name == "nt", reason="Windows uses CRLF as its record terminator")
def test_git_path_record_preserves_trailing_carriage_return() -> None:
    assert _decode_git_path_record(b"hooks\r\n") == "hooks\r"


def test_worktree_read_does_not_follow_symlink(tmp_path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside-secret.py"
    outside.write_text("SECRET = 'must not be read'\n", encoding="utf-8")
    link = tmp_path / "linked.py"
    os.symlink(outside, link)

    content = get_file_from_worktree("linked.py", tmp_path)

    assert content is None


def test_snapshot_content_rejects_invalid_utf8_across_boundaries(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    path = tmp_path / "invalid.py"
    path.write_bytes(b"target()\n\xff\n")
    _commit_all(tmp_path, "invalid utf8")

    assert get_file_at_snapshot("HEAD", "invalid.py", tmp_path) is None
    assert get_file_at_snapshot(":index", "invalid.py", tmp_path) is None
    assert get_file_at_snapshot(":worktree", "invalid.py", tmp_path) is None


def test_worktree_read_does_not_follow_symlinked_parent(tmp_path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "secret.py").write_text("SECRET = 'must not be read'\n", encoding="utf-8")
    os.symlink(outside, tmp_path / "package", target_is_directory=True)

    content = get_file_from_worktree("package/secret.py", tmp_path)

    assert content is None


def test_worktree_read_rejects_parent_traversal(tmp_path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.py"
    outside.write_text("SECRET = 'must not be read'\n", encoding="utf-8")

    content = get_file_from_worktree(f"../{outside.name}", tmp_path)

    assert content is None


def test_worktree_read_accepts_contained_regular_file(tmp_path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    source = package / "module.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")

    assert get_file_from_worktree("package/module.py", tmp_path) == "VALUE = 1\n"


def test_grep_files_distinguishes_no_matches_from_command_failure(tmp_path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    (tmp_path / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "module.py"], cwd=tmp_path, capture_output=True, check=True)

    assert grep_files("missing_symbol", ":index", tmp_path, ("*.py",)) == []
    assert grep_files("missing_symbol", "missing-ref", tmp_path, ("*.py",)) is None


def test_grep_files_treats_patterns_as_fixed_strings_for_every_snapshot(tmp_path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    (tmp_path / "module.ts").write_text("handler$();\n", encoding="utf-8")
    subprocess.run(["git", "add", "module.ts"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=T",
            "-c",
            "user.email=t@t.com",
            "-c",
            "core.hooksPath=",
            "commit",
            "-m",
            "init",
        ],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )

    for ref in ("HEAD", ":index", ":worktree"):
        assert grep_files("handler$", ref, tmp_path, ("*.ts",)) == ["module.ts"]


def _mock_worktree_diff_results(
    untracked: subprocess.CompletedProcess[str],
    file_path: str = "added.py",
) -> list[subprocess.CompletedProcess[str]]:
    """Return subprocess results for tracked diff, untracked listing, and no-index diff."""
    return [
        subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        subprocess.CompletedProcess([], 0, stdout=f"{file_path}\0", stderr=""),
        untracked,
    ]


def test_worktree_diff_logs_hostile_filename_as_one_safe_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    file_path = "hostile\nFORGED\x1b[2J.py"
    tracked = subprocess.CompletedProcess([], 0, stdout="", stderr="")
    listed = subprocess.CompletedProcess([], 0, stdout=f"{file_path}\0", stderr="")

    caplog.clear()
    with (
        patch("diffguard.git.subprocess.run", side_effect=[tracked, listed]),
        patch("diffguard.git.os.lstat", side_effect=FileNotFoundError("disappeared")),
        caplog.at_level(logging.ERROR, logger="diffguard.git"),
        pytest.raises(RuntimeError) as exc_info,
    ):
        get_worktree_diff("HEAD", "/repo")

    # Preserve exact exception data for structured callers; escape only the log.
    assert file_path in str(exc_info.value)
    assert len(caplog.records) == 1
    logged = caplog.records[0].getMessage()
    assert "\n" not in logged
    assert "\x1b" not in logged
    assert r"hostile\nFORGED\x1b[2J.py" in logged


_REGULAR_FILE_STAT = SimpleNamespace(st_mode=0o100644, st_size=1)
_MACHINE_DIFF_FLAGS = {
    "--no-color",
    "--no-ext-diff",
    "--no-textconv",
    "--src-prefix=a/",
    "--dst-prefix=b/",
    "--no-renames",
}


def _assert_machine_diff_command(command: list[str]) -> None:
    assert _MACHINE_DIFF_FLAGS <= set(command)


def test_committed_and_staged_diff_force_machine_stable_output() -> None:
    completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")

    with patch("diffguard.git.subprocess.run", return_value=completed) as run:
        get_diff("base..head", "/repo")
    committed_command = run.call_args.args[0]
    _assert_machine_diff_command(committed_command)
    assert committed_command[-3:] == ["--end-of-options", "base..head", "--"]

    with patch("diffguard.git.subprocess.run", return_value=completed) as run:
        get_staged_diff("/repo")
    staged_command = run.call_args.args[0]
    _assert_machine_diff_command(staged_command)
    assert staged_command[-1] == "--cached"


@pytest.mark.parametrize("diff_kind", ["committed", "worktree"])
def test_revision_output_option_cannot_redirect_diff(
    tmp_path: Path,
    diff_kind: str,
) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    source = tmp_path / "module.py"
    source.write_text("VALUE = 0\n", encoding="utf-8")
    _commit_all(tmp_path, "baseline")
    source.write_text("VALUE = 1\n", encoding="utf-8")
    redirected = tmp_path / "redirected.patch"
    option = f"--output={redirected}"

    with pytest.raises(RuntimeError):
        if diff_kind == "committed":
            get_diff(option, tmp_path)
        else:
            get_worktree_diff(option, tmp_path)

    assert not redirected.exists()


@pytest.mark.skipif(os.name == "nt", reason="executable external-diff fixture is POSIX-only")
@pytest.mark.parametrize("diff_kind", ["committed", "worktree"])
def test_revision_ext_diff_option_cannot_execute_external_diff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    diff_kind: str,
) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    source = tmp_path / "module.py"
    source.write_text("VALUE = 0\n", encoding="utf-8")
    _commit_all(tmp_path, "baseline")
    source.write_text("VALUE = 1\n", encoding="utf-8")
    marker = tmp_path / "external-diff-ran"
    external_diff = tmp_path / "external-diff"
    external_diff.write_text(
        f"#!/bin/sh\nprintf ran > {marker!s}\n",
        encoding="utf-8",
    )
    external_diff.chmod(0o755)
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", str(external_diff))

    with pytest.raises(RuntimeError):
        if diff_kind == "committed":
            get_diff("--ext-diff", tmp_path)
        else:
            get_worktree_diff("--ext-diff", tmp_path)

    assert not marker.exists()


def test_supported_binary_replacement_advances_past_textual_duplicate() -> None:
    header = "diff --git a/contract.py b/contract.py\n"
    original_removal = (
        header
        + "deleted file mode 120000\n"
        + "--- a/contract.py\n"
        + "+++ /dev/null\n"
        + "@@ -1 +0,0 @@\n"
        + "-target.py\n"
    )
    original_addition = (
        header + "new file mode 100644\n" + "Binary files /dev/null and b/contract.py differ\n"
    )
    textual_removal = original_removal
    textual_addition = (
        header
        + "new file mode 100644\n"
        + "--- /dev/null\n"
        + "+++ b/contract.py\n"
        + "@@ -0,0 +1 @@\n"
        + "+def contract(): ...\n"
    )
    completed = subprocess.CompletedProcess(
        [], 0, stdout=textual_removal + textual_addition, stderr=""
    )

    with patch("diffguard.git.subprocess.run", return_value=completed):
        merged = _force_supported_binary_records_to_text(
            original_removal + original_addition,
            ["git", "diff"],
            "/repo",
            0,
        )

    assert merged == original_removal + textual_addition


@pytest.mark.skipif(os.name == "nt", reason="regular-to-symlink mode changes are POSIX-only")
def test_hostile_binary_attribute_regular_file_to_symlink_preserves_removal(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    (tmp_path / ".gitattributes").write_text("*.py -diff\n", encoding="utf-8")
    source = tmp_path / "contract.py"
    source.write_text("def contract(value):\n    return value\n", encoding="utf-8")
    _commit_all(tmp_path, "base")

    source.unlink()
    source.symlink_to("target.py")
    _commit_all(tmp_path, "replace regular source with symlink")

    diff_text = get_diff("HEAD^..HEAD", tmp_path)
    assert diff_text.count("diff --git a/contract.py b/contract.py") == 2
    assert "-def contract(value):" in diff_text

    files = parse_diff(diff_text)
    assert len(files) == 1
    assert files[0].path == "contract.py"
    assert files[0].binary is False
    assert files[0].deletions == 2

    result = run_pipeline(
        diff_text,
        "HEAD^..HEAD",
        lambda ref, path: get_file_at_snapshot(ref, path, tmp_path),
    )
    changes = [change for file in result.files for change in file.changes]
    assert any(
        change.kind == "function_removed" and change.name == "contract" for change in changes
    )


def test_machine_diff_ignores_hostile_binary_attribute_for_python(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    (tmp_path / ".gitattributes").write_text("*.py -diff\n", encoding="utf-8")
    source = tmp_path / "contract.py"
    source.write_text("def contract(value):\n    return value\n", encoding="utf-8")
    binary = tmp_path / "payload.bin"
    binary.write_bytes(b"\x00before\n")
    _commit_all(tmp_path, "base")

    source.write_text("def contract(value, required):\n    return value\n", encoding="utf-8")
    binary.write_bytes(b"\x00after\n")
    _commit_all(tmp_path, "breaking")

    diff_text = get_diff("HEAD^..HEAD", tmp_path)
    files = {file.path: file for file in parse_diff(diff_text)}
    assert set(files) == {"contract.py", "payload.bin"}
    assert files["contract.py"].binary is False
    assert files["contract.py"].hunks
    assert files["payload.bin"].binary is True
    assert files["payload.bin"].hunks == []

    result = run_pipeline(
        diff_text,
        "HEAD^..HEAD",
        lambda ref, path: get_file_at_snapshot(ref, path, tmp_path),
    )
    by_path = {file.path: file for file in result.files}
    assert by_path["contract.py"].binary is False
    assert [change.category_id for change in by_path["contract.py"].changes] == [
        "required_parameter_added"
    ]
    assert by_path["payload.bin"].binary is True


def test_worktree_diff_accepts_exit_one_with_real_untracked_patch() -> None:
    patch_text = (
        "diff --git a/added.py b/added.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/added.py\n"
        "@@ -0,0 +1 @@\n"
        "+VALUE = 1\n"
    )
    untracked = subprocess.CompletedProcess([], 1, stdout=patch_text, stderr="")

    with (
        patch(
            "diffguard.git.subprocess.run",
            side_effect=_mock_worktree_diff_results(untracked),
        ) as run,
        patch("diffguard.git.os.lstat", return_value=_REGULAR_FILE_STAT),
    ):
        result = get_worktree_diff("HEAD", "/repo")

    assert result == patch_text
    _assert_machine_diff_command(run.call_args_list[0].args[0])
    no_index_command = run.call_args_list[2].args[0]
    _assert_machine_diff_command(no_index_command)
    assert no_index_command[:5] == [
        "git",
        "-c",
        "core.quotePath=false",
        "-c",
        "core.safecrlf=false",
    ]


@pytest.mark.parametrize("returncode", [1, 2])
def test_worktree_diff_rejects_fatal_stderr_even_with_complete_patch(
    returncode: int,
) -> None:
    patch_text = (
        "diff --git a/added.py b/added.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/added.py\n"
        "@@ -0,0 +1 @@\n"
        "+VALUE = 1\n"
    )
    untracked = subprocess.CompletedProcess(
        [],
        returncode,
        stdout=patch_text,
        stderr="fatal: patch generation incomplete\n",
    )

    with (
        patch(
            "diffguard.git.subprocess.run",
            side_effect=_mock_worktree_diff_results(untracked),
        ),
        patch("diffguard.git.os.lstat", return_value=_REGULAR_FILE_STAT),
        pytest.raises(RuntimeError, match="fatal: patch generation incomplete"),
    ):
        get_worktree_diff("HEAD", "/repo")


def test_worktree_diff_accepts_real_autocrlf_warning_for_untracked_file(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    (tmp_path / "baseline.py").write_text("BASELINE = True\n", encoding="utf-8")
    _commit_all(tmp_path, "baseline")
    subprocess.run(
        ["git", "config", "core.autocrlf", "true"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "core.safecrlf", "warn"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    (tmp_path / "added.py").write_text("VALUE = 1\n", encoding="utf-8", newline="\n")

    diff_text = get_worktree_diff("HEAD", tmp_path)

    assert [file.path for file in parse_diff(diff_text)] == ["added.py"]
    assert "+VALUE = 1" in diff_text


@pytest.mark.parametrize(
    "file_path",
    [
        "added.py",
        "docs/empty notes.md",
        'odd/tab\tline\nquote"slash\\-\udcff.py',
    ],
)
def test_worktree_diff_synthesizes_exit_zero_empty_untracked_file(file_path: str) -> None:
    untracked = subprocess.CompletedProcess([], 0, stdout="", stderr="")

    with (
        patch(
            "diffguard.git.subprocess.run",
            side_effect=_mock_worktree_diff_results(untracked, file_path),
        ),
        patch(
            "diffguard.git.os.lstat",
            return_value=SimpleNamespace(st_mode=0o100644, st_size=0),
        ),
    ):
        result = get_worktree_diff("HEAD", "/repo")

    assert result.count("\n") == 2
    assert "\nnew file mode 100644\n" in result
    files = parse_diff(result)
    assert len(files) == 1
    assert files[0].old_path is None
    assert files[0].new_path == file_path
    assert files[0].change_type == "added"
    assert files[0].hunks == []


def test_worktree_diff_synthesized_empty_file_preserves_executable_mode() -> None:
    untracked = subprocess.CompletedProcess([], 0, stdout="", stderr="")

    with (
        patch(
            "diffguard.git.subprocess.run",
            side_effect=_mock_worktree_diff_results(untracked),
        ),
        patch(
            "diffguard.git.os.lstat",
            return_value=SimpleNamespace(st_mode=0o100755, st_size=0),
        ),
    ):
        result = get_worktree_diff("HEAD", "/repo")

    assert "new file mode 100755" in result


@pytest.mark.parametrize("object_format", ["sha1", "sha256"])
def test_worktree_diff_accepts_empty_patch_with_long_abbreviation_and_object_format(
    tmp_path: Path,
    object_format: str,
) -> None:
    init_command = ["git", "init"]
    if object_format == "sha256":
        init_command.append("--object-format=sha256")
    initialized = subprocess.run(
        init_command,
        cwd=tmp_path,
        capture_output=True,
        check=False,
    )
    if initialized.returncode != 0:
        pytest.skip(f"Git does not support {object_format} repositories")

    (tmp_path / "baseline.py").write_text("BASELINE = True\n", encoding="utf-8")
    _commit_all(tmp_path, "baseline")
    subprocess.run(
        ["git", "config", "core.abbrev", "12"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    (tmp_path / "empty.py").touch()
    empty_blob_oid = (
        subprocess.run(
            ["git", "hash-object", "--stdin"],
            cwd=tmp_path,
            input=b"",
            capture_output=True,
            check=True,
        )
        .stdout.decode("ascii")
        .strip()
    )

    diff_text = get_worktree_diff("HEAD", tmp_path)

    assert f"index {'0' * 12}..{empty_blob_oid[:12]}\n" in diff_text
    files = parse_diff(diff_text)
    assert len(files) == 1
    assert files[0].path == "empty.py"
    assert files[0].change_type == "added"
    assert files[0].hunks == []


@pytest.mark.parametrize(
    "index_line",
    [
        "index 000000000000.e69de29bb2d1\n",
        "index 00000000000g..e69de29bb2d1\n",
        "index 00000000000..e69de29bb2d1\n",
        "index 000000000000..deadbeefcafe\n",
        "index 000000000000..e69de29bb2d1",
        "index 000000000000..e69de29bb2d1 extra\n",
        "index 000000000000..e69de29bb2d1\nforged trailer\n",
        (
            "index 000000000000..deadbeefcafe\n"
            "--- /dev/null\n"
            "+++ b/added.py\n"
            "@@ -0,0 +1 @@\n"
            "+forged content\n"
        ),
    ],
)
def test_worktree_diff_rejects_malformed_or_incorrect_empty_index(
    index_line: str,
) -> None:
    patch_text = f"diff --git a/added.py b/added.py\nnew file mode 100644\n{index_line}"
    untracked = subprocess.CompletedProcess([], 1, stdout=patch_text, stderr="")
    empty_blob = subprocess.CompletedProcess(
        [],
        0,
        stdout="e69de29bb2d1d6434b8b29ae775ad8c2e48c5391\n",
        stderr="",
    )

    with (
        patch(
            "diffguard.git.subprocess.run",
            side_effect=[*_mock_worktree_diff_results(untracked), empty_blob],
        ),
        patch(
            "diffguard.git.os.lstat",
            return_value=SimpleNamespace(st_mode=0o100644, st_size=0),
        ),
        pytest.raises(RuntimeError, match="git diff --no-index produced no valid patch"),
    ):
        get_worktree_diff("HEAD", "/repo")


def test_worktree_diff_rejects_header_only_patch_for_nonempty_file() -> None:
    patch_text = (
        "diff --git a/added.py b/added.py\nnew file mode 100644\nindex 000000000000..e69de29bb2d1\n"
    )
    untracked = subprocess.CompletedProcess([], 1, stdout=patch_text, stderr="")

    with (
        patch(
            "diffguard.git.subprocess.run",
            side_effect=_mock_worktree_diff_results(untracked),
        ),
        patch("diffguard.git.os.lstat", return_value=_REGULAR_FILE_STAT),
        pytest.raises(RuntimeError, match="git diff --no-index produced no valid patch"),
    ):
        get_worktree_diff("HEAD", "/repo")


@pytest.mark.parametrize(
    ("st_mode", "st_size", "expected"),
    [
        (0o100644, 1, "no patch for a non-empty file"),
    ],
)
def test_worktree_diff_rejects_exit_zero_without_patch_for_unsafe_target(
    st_mode: int,
    st_size: int,
    expected: str,
) -> None:
    untracked = subprocess.CompletedProcess([], 0, stdout="", stderr="")

    with (
        patch(
            "diffguard.git.subprocess.run",
            side_effect=_mock_worktree_diff_results(untracked),
        ),
        patch(
            "diffguard.git.os.lstat",
            return_value=SimpleNamespace(st_mode=st_mode, st_size=st_size),
        ),
        pytest.raises(RuntimeError, match=expected),
    ):
        get_worktree_diff("HEAD", "/repo")


@pytest.mark.parametrize(
    "st_mode",
    [
        stat.S_IFLNK | 0o777,
        stat.S_IFIFO | 0o644,
    ],
)
def test_worktree_diff_rejects_nonregular_entry_before_no_index(st_mode: int) -> None:
    untracked = subprocess.CompletedProcess([], 1, stdout="unreachable", stderr="")

    with (
        patch(
            "diffguard.git.subprocess.run",
            side_effect=_mock_worktree_diff_results(untracked),
        ) as run,
        patch(
            "diffguard.git.os.lstat",
            return_value=SimpleNamespace(st_mode=st_mode, st_size=0),
        ),
        pytest.raises(RuntimeError, match="untracked diff target is not a regular file"),
    ):
        get_worktree_diff("HEAD", "/repo")

    assert run.call_count == 2


@pytest.mark.skipif(os.name == "nt", reason="FIFO and POSIX symlink fixtures")
@pytest.mark.parametrize("entry_kind", ["fifo", "symlink", "symlink-to-fifo"])
def test_worktree_diff_preflights_real_nonregular_entry(
    tmp_path: Path,
    entry_kind: str,
) -> None:
    entry = tmp_path / "unsafe"
    if entry_kind == "fifo":
        os.mkfifo(entry)
    elif entry_kind == "symlink":
        target = tmp_path / "regular-target"
        target.write_text("content\n", encoding="utf-8")
        entry.symlink_to(target.name)
    else:
        target = tmp_path / "fifo-target"
        os.mkfifo(target)
        entry.symlink_to(target.name)

    unreachable = subprocess.CompletedProcess([], 1, stdout="unreachable", stderr="")
    with (
        patch(
            "diffguard.git.subprocess.run",
            side_effect=_mock_worktree_diff_results(unreachable, entry.name),
        ) as run,
        pytest.raises(RuntimeError, match="untracked diff target is not a regular file"),
    ):
        get_worktree_diff("HEAD", tmp_path)

    assert run.call_count == 2


@pytest.mark.parametrize(
    ("stdout", "stderr", "expected"),
    [
        ("", "fatal: added.py disappeared", "fatal: added.py disappeared"),
        ("not a patch\n", "", "git diff --no-index produced no valid patch"),
        (
            "diff --git a/added.py b/added.py\n",
            "fatal: patch generation incomplete",
            "fatal: patch generation incomplete",
        ),
    ],
)
def test_worktree_diff_rejects_exit_one_error_or_missing_patch(
    stdout: str,
    stderr: str,
    expected: str,
) -> None:
    untracked = subprocess.CompletedProcess([], 1, stdout=stdout, stderr=stderr)

    with (
        patch(
            "diffguard.git.subprocess.run",
            side_effect=_mock_worktree_diff_results(untracked),
        ),
        patch("diffguard.git.os.lstat", return_value=_REGULAR_FILE_STAT),
        pytest.raises(RuntimeError, match=expected),
    ):
        get_worktree_diff("HEAD", "/repo")


def _configure_hostile_diff_output(repo: Path) -> None:
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


def test_parser_facing_diffs_override_hostile_output_config(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    (tmp_path / ".gitattributes").write_text("*.py diff=hostile\n", encoding="utf-8")
    module = tmp_path / "module.py"
    module.write_text("VALUE = 0\n", encoding="utf-8")
    _commit_all(tmp_path, "baseline")
    _configure_hostile_diff_output(tmp_path)

    module.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "module.py"], cwd=tmp_path, capture_output=True, check=True)
    assert [file.path for file in parse_diff(get_staged_diff(tmp_path))] == ["module.py"]

    _commit_all(tmp_path, "tracked change")
    assert [file.path for file in parse_diff(get_diff("HEAD~1..HEAD", tmp_path))] == ["module.py"]

    module.write_text("VALUE = 2\n", encoding="utf-8")
    (tmp_path / "added.py").write_text("ADDED = True\n", encoding="utf-8")
    assert {file.path for file in parse_diff(get_worktree_diff("HEAD", tmp_path))} == {
        "added.py",
        "module.py",
    }


@pytest.mark.parametrize(
    ("file_path", "content", "executable", "expected_additions", "expected_mode"),
    [
        ("-", "DASH_FILE = True\n", False, 1, "100644"),
        ("-", "", False, 0, "100644"),
        pytest.param(
            "-",
            "",
            True,
            0,
            "100755",
            marks=pytest.mark.skipif(os.name == "nt", reason="executable mode is POSIX-only"),
        ),
        ("dir/-", "NESTED_DASH = True\n", False, 1, "100644"),
    ],
)
def test_worktree_diff_disambiguates_untracked_dash_path(
    tmp_path: Path,
    file_path: str,
    content: str,
    executable: bool,
    expected_additions: int,
    expected_mode: str,
) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    (tmp_path / "baseline.py").write_text("BASELINE = True\n", encoding="utf-8")
    _commit_all(tmp_path, "baseline")
    untracked = tmp_path / file_path
    untracked.parent.mkdir(parents=True, exist_ok=True)
    untracked.write_text(content, encoding="utf-8")
    if executable:
        untracked.chmod(0o755)

    diff_text = get_worktree_diff("HEAD", tmp_path)
    files = parse_diff(diff_text)

    assert "a/./-" not in diff_text
    assert "b/./-" not in diff_text
    assert len(files) == 1
    assert files[0].path == file_path
    assert files[0].change_type == "added"
    assert files[0].additions == expected_additions
    assert f"new file mode {expected_mode}" in diff_text


@pytest.mark.skipif(os.name == "nt", reason="POSIX Git paths are byte strings")
def test_non_utf8_git_tree_paths_round_trip_without_filesystem_support(
    tmp_path: Path,
) -> None:
    """Commit and index operations preserve path bytes rejected by some filesystems."""
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    tracked_bytes = b"tracked-\xff.py"
    tracked = os.fsdecode(tracked_bytes)
    assert os.fsencode(tracked) == tracked_bytes

    baseline_tree = _git_tree_with_bytes_path(tmp_path, tracked_bytes, b"needle = 0\n")
    baseline = _commit_tree(tmp_path, baseline_tree, "baseline")
    changed_tree = _git_tree_with_bytes_path(tmp_path, tracked_bytes, b"needle = 1\n")
    changed = _commit_tree(tmp_path, changed_tree, "change byte path", baseline)
    subprocess.run(
        ["git", "update-ref", "HEAD", changed],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "read-tree", changed],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )

    committed_files = parse_diff(get_diff(f"{baseline}..{changed}", tmp_path))
    assert [os.fsencode(file.path) for file in committed_files] == [tracked_bytes]
    assert [os.fsencode(path) for path in list_files_at_ref("HEAD", tmp_path)] == [tracked_bytes]
    assert [os.fsencode(path) for path in list_files_at_snapshot(":index", tmp_path)] == [
        tracked_bytes
    ]
    assert [
        os.fsencode(path) for path in grep_files("needle", "HEAD", tmp_path, ("*.py",)) or []
    ] == [tracked_bytes]
    assert [
        os.fsencode(path) for path in grep_files("needle", ":index", tmp_path, ("*.py",)) or []
    ] == [tracked_bytes]
    assert get_file_at_snapshot("HEAD", tracked, tmp_path) == "needle = 1\n"
    assert get_file_at_snapshot(":index", tracked, tmp_path) == "needle = 1\n"

    staged_tree = _git_tree_with_bytes_path(tmp_path, tracked_bytes, b"needle = 2\n")
    subprocess.run(
        ["git", "read-tree", staged_tree],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    staged_files = parse_diff(get_staged_diff(tmp_path))
    assert [os.fsencode(file.path) for file in staged_files] == [tracked_bytes]
    assert get_file_at_snapshot(":index", tracked, tmp_path) == "needle = 2\n"

    # The path cannot exist on every POSIX filesystem, but diffing the missing
    # worktree entry still exercises surrogate-safe path output from `git diff`.
    worktree_files = parse_diff(get_worktree_diff("HEAD", tmp_path))
    assert [os.fsencode(file.path) for file in worktree_files] == [tracked_bytes]


@pytest.mark.skipif(os.name == "nt", reason="POSIX permits arbitrary non-NUL path bytes")
def test_non_utf8_worktree_paths_round_trip_when_filesystem_supports_them(
    tmp_path: Path,
) -> None:
    """Every Git path surface preserves undecodable POSIX filename bytes."""
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    tracked_bytes = b"tracked-\xff.py"
    untracked_bytes = b"untracked-\xfe.py"
    try:
        tracked = _write_bytes_path(tmp_path, tracked_bytes, b"needle = 0\n")
    except OSError as exc:
        if exc.errno in {errno.EILSEQ, errno.EINVAL}:
            pytest.skip("filesystem rejects non-UTF-8 path bytes")
        raise
    _commit_all(tmp_path, "baseline")

    _write_bytes_path(tmp_path, tracked_bytes, b"needle = 1\n")
    _commit_all(tmp_path, "change tracked byte path")

    committed_files = parse_diff(get_diff("HEAD~1..HEAD", tmp_path))
    assert [os.fsencode(file.path) for file in committed_files] == [tracked_bytes]
    assert [os.fsencode(path) for path in list_files_at_ref("HEAD", tmp_path)] == [tracked_bytes]
    assert get_file_at_snapshot("HEAD", tracked, tmp_path) == "needle = 1\n"

    _write_bytes_path(tmp_path, tracked_bytes, b"needle = 2\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True, check=True)
    staged_files = parse_diff(get_staged_diff(tmp_path))
    assert [os.fsencode(file.path) for file in staged_files] == [tracked_bytes]

    _write_bytes_path(tmp_path, tracked_bytes, b"needle = 3\n")
    untracked = _write_bytes_path(tmp_path, untracked_bytes, b"needle = 4\n")
    worktree_files = parse_diff(get_worktree_diff("HEAD", tmp_path))
    assert {os.fsencode(file.path) for file in worktree_files} == {
        tracked_bytes,
        untracked_bytes,
    }

    assert [os.fsencode(path) for path in list_files_at_snapshot(":index", tmp_path)] == [
        tracked_bytes
    ]
    assert {os.fsencode(path) for path in list_files_at_snapshot(":worktree", tmp_path)} == {
        tracked_bytes,
        untracked_bytes,
    }

    assert [
        os.fsencode(path) for path in grep_files("needle", "HEAD", tmp_path, ("*.py",)) or []
    ] == [tracked_bytes]
    assert [
        os.fsencode(path) for path in grep_files("needle", ":index", tmp_path, ("*.py",)) or []
    ] == [tracked_bytes]
    assert {
        os.fsencode(path) for path in grep_files("needle", ":worktree", tmp_path, ("*.py",)) or []
    } == {tracked_bytes, untracked_bytes}

    assert get_file_at_snapshot(":index", tracked, tmp_path) == "needle = 2\n"
    assert get_file_at_snapshot(":worktree", tracked, tmp_path) == "needle = 3\n"
    assert get_file_at_snapshot(":worktree", untracked, tmp_path) == "needle = 4\n"
