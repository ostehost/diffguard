#!/usr/bin/env python3
"""Run one bounded DiffGuard closeout review and retain private artifacts."""

from __future__ import annotations

import argparse
import json
import math
import os
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import BinaryIO, NoReturn, cast

_DEFAULT_TIMEOUT_SECONDS = 300.0
_DEFAULT_MAX_OUTPUT_BYTES = 10 * 1024 * 1024
_READ_SIZE = 64 * 1024
_TERMINATE_GRACE_SECONDS = 0.25


def _positive_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be finite and greater than zero")
    return parsed


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main", help="Base ref for --worktree review")
    parser.add_argument("--repo", default=".", help="Repository to review")
    parser.add_argument(
        "--timeout-seconds",
        type=_positive_float,
        default=_DEFAULT_TIMEOUT_SECONDS,
        help=f"Wall-time limit (default: {_DEFAULT_TIMEOUT_SECONDS:g})",
    )
    parser.add_argument(
        "--max-output-bytes",
        type=_positive_int,
        default=_DEFAULT_MAX_OUTPUT_BYTES,
        help=f"Combined stdout/stderr limit (default: {_DEFAULT_MAX_OUTPUT_BYTES})",
    )
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=Path(os.environ.get("TMPDIR", "/tmp")),
        help="Existing directory for retained private artifacts",
    )
    return parser


def _private_artifact(directory: Path, suffix: str) -> tuple[BinaryIO, Path]:
    fd, raw_path = tempfile.mkstemp(prefix="diffguard-review.", suffix=suffix, dir=directory)
    path = Path(raw_path)
    try:
        os.chmod(raw_path, 0o600)
        stream = os.fdopen(fd, "wb", buffering=0)
    except (OSError, ValueError) as exc:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            path.unlink()
        except OSError as cleanup_error:
            exc.add_note(f"also unable to remove partial artifact {path!s}: {cleanup_error}")
        raise
    return stream, path


def _private_artifacts(directory: Path) -> tuple[BinaryIO, Path, BinaryIO, Path]:
    """Create both artifacts without leaking the first on partial failure."""
    stdout_file, stdout_path = _private_artifact(directory, ".json")
    try:
        stderr_file, stderr_path = _private_artifact(directory, ".stderr")
    except (OSError, ValueError) as exc:
        stdout_file.close()
        try:
            stdout_path.unlink()
        except OSError as cleanup_error:
            exc.add_note(f"also unable to remove partial artifact {stdout_path!s}: {cleanup_error}")
        raise
    return stdout_file, stdout_path, stderr_file, stderr_path


def _stop_process_group(process: subprocess.Popen[bytes]) -> None:
    """Stop the review and descendants without relying on GNU timeout."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return

    time.sleep(_TERMINATE_GRACE_SECONDS)

    # The group can outlive an already-exited wrapper process (for example, a
    # child holding the output pipes open), so target the group even when the
    # direct child has already been reaped.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _run_bounded(
    command: list[str],
    stdout_artifact: BinaryIO,
    stderr_artifact: BinaryIO,
    *,
    timeout_seconds: float,
    max_output_bytes: int,
) -> tuple[int, str | None]:
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except (OSError, ValueError) as exc:
        message = f"unable to start review: {exc}"
        stderr_artifact.write(message.encode("utf-8", errors="backslashreplace"))
        return 2, message

    assert process.stdout is not None
    assert process.stderr is not None
    selector = selectors.DefaultSelector()
    deadline = time.monotonic() + timeout_seconds
    total_bytes = 0
    failure: str | None = None

    def _interrupt(signum: int, _frame: object) -> None:
        nonlocal failure
        if failure is None:
            failure = f"interrupted by {signal.Signals(signum).name}"
        _stop_process_group(process)

    handled_signals = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
    previous_handlers = {signum: signal.getsignal(signum) for signum in handled_signals}

    try:
        streams: dict[int, BinaryIO] = {
            process.stdout.fileno(): stdout_artifact,
            process.stderr.fileno(): stderr_artifact,
        }
        selector.register(process.stdout, selectors.EVENT_READ)
        selector.register(process.stderr, selectors.EVENT_READ)
        for signum in handled_signals:
            signal.signal(signum, _interrupt)

        while selector.get_map():
            remaining_time = deadline - time.monotonic()
            if remaining_time <= 0 and failure is None:
                failure = f"wall-time limit exceeded ({timeout_seconds:g}s)"
                _stop_process_group(process)

            events = selector.select(timeout=max(0.0, min(0.1, remaining_time)))
            for key, _ in events:
                chunk = os.read(key.fd, _READ_SIZE)
                if not chunk:
                    selector.unregister(key.fileobj)
                    cast(BinaryIO, key.fileobj).close()
                    continue

                capacity = max_output_bytes - total_bytes
                if capacity > 0:
                    streams[key.fd].write(chunk[:capacity])
                    total_bytes += min(len(chunk), capacity)
                if len(chunk) > capacity and failure is None:
                    failure = f"combined output limit exceeded ({max_output_bytes} bytes)"
                    _stop_process_group(process)
    except Exception as exc:
        if failure is None:
            failure = f"unable to capture review output: {exc}"
        _stop_process_group(process)
    finally:
        selector.close()
        for stream in (process.stdout, process.stderr):
            if not stream.closed:
                stream.close()
        for signum, previous_handler in previous_handlers.items():
            signal.signal(signum, previous_handler)

    if failure is None:
        try:
            return_code = process.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            failure = f"wall-time limit exceeded ({timeout_seconds:g}s)"
            _stop_process_group(process)
            return_code = process.wait()
    else:
        return_code = process.wait()
    if failure is not None:
        return 2, failure
    if return_code not in (0, 1, 2):
        return 2, f"unexpected review exit status {return_code}"
    return return_code, None


def _die(message: str) -> NoReturn:
    print(f"DiffGuard closeout runner: {message}", file=sys.stderr)
    raise SystemExit(2)


def main() -> int:
    args = _parser().parse_args()
    if not args.artifact_dir.is_dir():
        _die(f"artifact directory is not an existing directory: {args.artifact_dir!r}")

    repo = Path(args.repo).expanduser().resolve()

    try:
        stdout_file, stdout_path, stderr_file, stderr_path = _private_artifacts(args.artifact_dir)
    except (OSError, ValueError) as exc:
        _die(f"unable to create private artifacts: {exc}")

    try:
        return_code, failure = _run_bounded(
            [
                "uv",
                "run",
                "--project",
                str(repo),
                "--locked",
                "diffguard",
                "review",
                "--against",
                args.base,
                "--worktree",
                "--format",
                "json",
                "--repo",
                str(repo),
            ],
            stdout_file,
            stderr_file,
            timeout_seconds=args.timeout_seconds,
            max_output_bytes=args.max_output_bytes,
        )
    finally:
        stdout_file.close()
        stderr_file.close()

    # JSON quoting prevents artifact paths containing control characters from
    # changing terminal/log structure. The artifacts remain mode 0600.
    print(f"DiffGuard review artifact: {json.dumps(str(stdout_path), ensure_ascii=True)}")
    print(f"DiffGuard stderr artifact: {json.dumps(str(stderr_path), ensure_ascii=True)}")
    if failure is not None:
        print(f"DiffGuard closeout runner: {failure}", file=sys.stderr)
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
