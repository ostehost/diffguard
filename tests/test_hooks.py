"""Tests for Git hook templates and installation."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from diffguard.hooks import HookError, PRE_PUSH_HOOK, install_hook


Z40 = "0" * 40
Z64 = "0" * 64


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _run_pre_push(tmp_path: Path, stdin: str, git_script: str) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    hook_path = tmp_path / "pre-push"
    _write_executable(hook_path, PRE_PUSH_HOOK)
    _write_executable(bin_dir / "git", git_script)
    _write_executable(
        bin_dir / "diffguard",
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$DIFFGUARD_CALLS"\nexit 0\n',
    )
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["DIFFGUARD_CALLS"] = str(tmp_path / "calls")
    return subprocess.run(
        [str(hook_path), "origin", "unused"],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_pre_push_skips_branch_deletion(tmp_path: Path) -> None:
    result = _run_pre_push(
        tmp_path,
        f"refs/heads/topic {Z40} refs/heads/topic {'a' * 40}\n",
        "#!/bin/sh\nexit 99\n",
    )
    assert result.returncode == 0
    assert not (tmp_path / "calls").exists()


def test_pre_push_skips_sha256_branch_deletion(tmp_path: Path) -> None:
    result = _run_pre_push(
        tmp_path,
        f"refs/heads/topic {Z64} refs/heads/topic {'a' * 64}\n",
        "#!/bin/sh\nexit 99\n",
    )
    assert result.returncode == 0
    assert not (tmp_path / "calls").exists()


def test_pre_push_new_branch_uses_default_branch_merge_base(tmp_path: Path) -> None:
    local_sha = "a" * 40
    result = _run_pre_push(
        tmp_path,
        f"refs/heads/topic {local_sha} refs/heads/topic {Z40}\n",
        """#!/bin/sh
case "$1" in
  symbolic-ref) printf '%s\\n' refs/remotes/origin/main ;;
  rev-parse) printf '%s\\n' base-sha ;;
  merge-base) printf '%s\\n' merge-base-sha ;;
  *) exit 2 ;;
esac
""",
    )
    assert result.returncode == 0
    assert (tmp_path / "calls").read_text(encoding="utf-8") == (
        f"review merge-base-sha..{local_sha}\n"
    )


def test_pre_push_sha256_new_branch_uses_default_branch_merge_base(tmp_path: Path) -> None:
    local_sha = "a" * 64
    result = _run_pre_push(
        tmp_path,
        f"refs/heads/topic {local_sha} refs/heads/topic {Z64}\n",
        """#!/bin/sh
case "$1" in
  symbolic-ref) printf '%s\\n' refs/remotes/origin/main ;;
  rev-parse) printf '%s\\n' base-sha ;;
  merge-base) printf '%s\\n' merge-base-sha ;;
  *) exit 2 ;;
esac
""",
    )
    assert result.returncode == 0
    assert (tmp_path / "calls").read_text(encoding="utf-8") == (
        f"review merge-base-sha..{local_sha}\n"
    )


def test_pre_push_blocks_new_branch_when_default_branch_is_unknown(tmp_path: Path) -> None:
    local_sha = "a" * 40
    result = _run_pre_push(
        tmp_path,
        f"refs/heads/topic {local_sha} refs/heads/topic {Z40}\n",
        "#!/bin/sh\nexit 1\n",
    )

    assert result.returncode == 2
    assert "could not determine a default branch" in result.stderr
    assert not (tmp_path / "calls").exists()


def _init_repo(path: Path) -> None:
    path.mkdir()
    subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=path,
        capture_output=True,
        check=True,
    )


def test_install_hook_honors_core_hooks_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    hooks_dir = tmp_path / "shared-hooks"
    subprocess.run(
        ["git", "config", "core.hooksPath", str(hooks_dir)],
        cwd=repo,
        capture_output=True,
        check=True,
    )

    installed = install_hook(str(repo), "pre-commit")

    assert Path(installed) == hooks_dir / "pre-commit"
    assert os.access(installed, os.X_OK)


def test_install_hook_rejects_non_repository(tmp_path: Path) -> None:
    try:
        install_hook(str(tmp_path), "pre-push")
    except HookError as exc:
        assert str(exc) == f"Not a git repository: {tmp_path}"
    else:
        raise AssertionError("install_hook accepted a non-repository")


def test_install_hook_accepts_linked_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    linked = tmp_path / "linked"
    _init_repo(repo)
    (repo / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--no-verify", "-m", "baseline"],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "worktree", "add", "-b", "linked", str(linked)],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    hooks_dir = tmp_path / "linked-hooks"
    subprocess.run(
        ["git", "config", "core.hooksPath", str(hooks_dir)],
        cwd=linked,
        capture_output=True,
        check=True,
    )

    installed = install_hook(str(linked), "pre-push")

    assert Path(installed) == hooks_dir / "pre-push"
    assert os.access(installed, os.X_OK)
