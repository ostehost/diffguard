"""Tests for the A/B oracle helper script."""

from __future__ import annotations

import importlib.util
import subprocess
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

import pytest


def _load_ab_oracle() -> ModuleType:
    script_path = Path(__file__).parents[1] / "scripts" / "ab_oracle_lite.py"
    spec = importlib.util.spec_from_file_location("ab_oracle_lite", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _completed(
    cmd: Sequence[str],
    returncode: int,
    *,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[Sequence[str]]:
    return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)


def test_run_test_aborts_before_claude_when_git_diff_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    oracle = _load_ab_oracle()
    calls: list[Sequence[str]] = []

    def fake_run(
        cmd: Sequence[str], *args: object, **kwargs: object
    ) -> subprocess.CompletedProcess[Sequence[str]]:
        calls.append(cmd)
        return _completed(cmd, 128, stderr="fatal: bad revision 'missing'")

    def fail_call_claude(system: str, user_content: str) -> str:
        pytest.fail("call_claude should not be invoked after a diff failure")

    monkeypatch.setattr(oracle.subprocess, "run", fake_run)
    monkeypatch.setattr(oracle, "call_claude", fail_call_claude)

    with pytest.raises(RuntimeError, match="fatal: bad revision"):
        oracle.run_test("bad range", tmp_path, "missing")

    assert calls == [["git", "diff", "missing"]]


def test_run_test_aborts_before_claude_when_diffguard_context_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    oracle = _load_ab_oracle()
    calls: list[Sequence[str]] = []

    def fake_run(
        cmd: Sequence[str], *args: object, **kwargs: object
    ) -> subprocess.CompletedProcess[Sequence[str]]:
        calls.append(cmd)
        if list(cmd[:2]) == ["git", "diff"]:
            return _completed(cmd, 0, stdout="diff --git a/lib.py b/lib.py\n")
        return _completed(cmd, 2, stderr="Error: diffguard exploded")

    def fail_call_claude(system: str, user_content: str) -> str:
        pytest.fail("call_claude should not be invoked after a context failure")

    monkeypatch.setattr(oracle.subprocess, "run", fake_run)
    monkeypatch.setattr(oracle, "call_claude", fail_call_claude)

    with pytest.raises(RuntimeError, match="diffguard exploded"):
        oracle.run_test("bad context", tmp_path, "HEAD~1..HEAD")

    assert len(calls) == 2
    assert calls[0] == ["git", "diff", "HEAD~1..HEAD"]
    assert "review" in calls[1]
    assert "--format" in calls[1]
    assert "json" in calls[1]


def test_get_diffguard_context_accepts_review_findings_exit_one(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    oracle = _load_ab_oracle()
    calls: list[Sequence[str]] = []

    def fake_run(
        cmd: Sequence[str], *args: object, **kwargs: object
    ) -> subprocess.CompletedProcess[Sequence[str]]:
        calls.append(cmd)
        return _completed(cmd, 1, stdout='{"findings": []}\n')

    monkeypatch.setattr(oracle.subprocess, "run", fake_run)

    assert oracle.get_diffguard_context(tmp_path, "HEAD~1..HEAD") == '{"findings": []}\n'
    assert calls == [
        [
            str(Path(__file__).parents[1] / ".venv" / "bin" / "diffguard"),
            "review",
            "HEAD~1..HEAD",
            "--repo",
            str(tmp_path),
            "--format",
            "json",
        ]
    ]


def test_get_diffguard_context_uses_live_review_cli(tmp_path: Path) -> None:
    oracle = _load_ab_oracle()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "core.hooksPath", ""], cwd=repo, check=True)
    (repo / "lib.py").write_text("def helper(value):\n    return value\n")
    (repo / "app.py").write_text("from lib import helper\nprint(helper(1))\n")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "test: init"], cwd=repo, capture_output=True, check=True)
    (repo / "lib.py").write_text("def helper(value, other):\n    return value + other\n")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "test: change signature"], cwd=repo, capture_output=True, check=True
    )

    context = oracle.get_diffguard_context(repo, "HEAD~1..HEAD")

    assert '"ref_range": "HEAD~1..HEAD"' in context
    assert "helper" in context
