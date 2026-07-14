"""Contract tests for the bounded DiffGuard closeout skill runner."""

from __future__ import annotations

import json
import os
import runpy
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, cast

import pytest

_RUNNER = (
    Path(__file__).parents[1]
    / ".agents"
    / "skills"
    / "diffguard-closeout"
    / "scripts"
    / "run_review.py"
)


def _fake_uv(tmp_path: Path, body: str) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "uv"
    executable.write_text(f"#!/bin/sh\n{body}", encoding="utf-8")
    executable.chmod(0o755)
    return bin_dir


def _run(
    tmp_path: Path,
    fake_uv: str,
    *args: str,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    bin_dir = _fake_uv(tmp_path, fake_uv)
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    return subprocess.run(
        [
            sys.executable,
            str(_RUNNER),
            "--artifact-dir",
            str(artifact_dir),
            *args,
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        check=False,
    )


def _artifact_paths(result: subprocess.CompletedProcess[str]) -> tuple[Path, Path]:
    values = [json.loads(line.partition(": ")[2]) for line in result.stdout.splitlines()]
    assert len(values) == 2
    return Path(values[0]), Path(values[1])


def test_runner_cleans_first_artifact_if_second_creation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    namespace = runpy.run_path(str(_RUNNER))
    create_artifacts = namespace["_private_artifacts"]
    real_mkstemp = tempfile.mkstemp
    calls = 0

    def fail_second_mkstemp(*args: Any, **kwargs: Any) -> tuple[int, str]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated second artifact failure")
        return cast(tuple[int, str], real_mkstemp(*args, **kwargs))

    monkeypatch.setattr(tempfile, "mkstemp", fail_second_mkstemp)

    with pytest.raises(OSError, match="simulated second artifact failure"):
        create_artifacts(tmp_path)

    assert calls == 2
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("value", ["nan", "inf"])
def test_runner_rejects_nonfinite_timeout(tmp_path: Path, value: str) -> None:
    result = _run(
        tmp_path,
        "exit 0\n",
        "--timeout-seconds",
        value,
    )

    assert result.returncode == 2
    assert "must be finite and greater than zero" in result.stderr


def test_runner_stops_and_reaps_review_after_artifact_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    namespace = runpy.run_path(str(_RUNNER))
    run_bounded = namespace["_run_bounded"]
    real_popen = subprocess.Popen
    processes: list[subprocess.Popen[bytes]] = []

    def recording_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        process: subprocess.Popen[bytes] = real_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", recording_popen)
    stdout_artifact = tempfile.TemporaryFile("w+b")
    stderr_artifact = tempfile.TemporaryFile("w+b")
    stdout_artifact.close()
    started = time.monotonic()
    try:
        return_code, failure = run_bounded(
            [
                sys.executable,
                "-c",
                (
                    "import sys, time; "
                    "sys.stdout.write('output'); sys.stdout.flush(); time.sleep(30)"
                ),
            ],
            stdout_artifact,
            stderr_artifact,
            timeout_seconds=10,
            max_output_bytes=1024,
        )
    finally:
        stderr_artifact.close()

    assert return_code == 2
    assert failure is not None and "unable to capture review output" in failure
    assert time.monotonic() - started < 2
    assert len(processes) == 1
    assert processes[0].poll() is not None


def test_runner_preserves_review_exit_and_private_artifacts(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        "printf '%s\\n' '{\"status\":\"ok\"}'\nprintf '%s\\n' diagnostic >&2\nexit 1\n",
    )

    assert result.returncode == 1
    review_path, stderr_path = _artifact_paths(result)
    assert review_path.read_text(encoding="utf-8") == '{"status":"ok"}\n'
    assert stderr_path.read_text(encoding="utf-8") == "diagnostic\n"
    assert stat.S_IMODE(review_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(stderr_path.stat().st_mode) == 0o600


def test_runner_resolves_project_from_selected_repo_when_called_elsewhere(tmp_path: Path) -> None:
    repo = tmp_path / "selected-repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    result = _run(
        tmp_path,
        "pwd\nprintf '<%s>\\n' \"$@\"\n",
        "--repo",
        "../selected-repo",
        cwd=outside,
    )

    assert result.returncode == 0
    review_path, stderr_path = _artifact_paths(result)
    output = review_path.read_text(encoding="utf-8").splitlines()
    assert output[0] == str(outside.resolve())
    assert output[1:] == [
        "<run>",
        "<--project>",
        f"<{repo.resolve()}>",
        "<--locked>",
        "<diffguard>",
        "<review>",
        "<--against>",
        "<origin/main>",
        "<--worktree>",
        "<--format>",
        "<json>",
        "<--repo>",
        f"<{repo.resolve()}>",
    ]
    assert stderr_path.read_bytes() == b""


def test_runner_maps_timeout_to_tool_error_and_retains_partial_artifacts(tmp_path: Path) -> None:
    started = time.monotonic()
    result = _run(
        tmp_path,
        "printf partial\nsleep 5\n",
        "--timeout-seconds",
        "0.1",
    )

    assert result.returncode == 2
    assert time.monotonic() - started < 2
    assert "wall-time limit exceeded" in result.stderr
    review_path, stderr_path = _artifact_paths(result)
    assert review_path.exists()
    assert stderr_path.exists()


def test_runner_bounds_descendant_that_outlives_review_wrapper(tmp_path: Path) -> None:
    started = time.monotonic()
    result = _run(
        tmp_path,
        "sleep 5 &\nexit 0\n",
        "--timeout-seconds",
        "0.1",
    )

    assert result.returncode == 2
    assert time.monotonic() - started < 2
    assert "wall-time limit exceeded" in result.stderr
    _artifact_paths(result)


def test_runner_maps_output_limit_to_tool_error_and_caps_artifacts(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        'i=0\nwhile [ "$i" -lt 100 ]; do printf x; i=$((i + 1)); done\n',
        "--max-output-bytes",
        "32",
    )

    assert result.returncode == 2
    assert "combined output limit exceeded" in result.stderr
    review_path, stderr_path = _artifact_paths(result)
    assert review_path.stat().st_size + stderr_path.stat().st_size <= 32
