"""DiffGuard CLI entry point."""

from __future__ import annotations

import logging
import sys
from typing import NoReturn

import click

from diffguard import __version__, hooks, report
from diffguard.engine._refs import split_ref_range
from diffguard.engine.deps import scan_references
from diffguard.engine.findings import extract_findings
from diffguard.engine.pipeline import FileContentProvider, run_pipeline
from diffguard.git import (
    get_diff,
    get_file_at_snapshot,
    get_file_at_ref,
    get_file_from_index,
    get_merge_base,
    get_repository_root,
    get_staged_diff,
    get_worktree_diff,
    resolve_commit,
)
from diffguard.schema import DiffGuardOutput, ReviewMode

logger = logging.getLogger(__name__)

# Exit codes
EXIT_SUCCESS = 0  # No high-signal findings (silence)
EXIT_FINDINGS = 1  # Findings present — agent should read output
EXIT_ERROR = 2  # Something went wrong
EXIT_NO_CHANGES = 3  # No changes in diff (summarize command)
EXIT_PARTIAL = 4  # Parse errors in some files (summarize command)


def _make_content_provider(repo_path: str) -> FileContentProvider:
    """Create a file content provider bound to a repo path."""

    def _get(ref: str, file_path: str) -> str | None:
        return get_file_at_ref(ref, file_path, repo_path=repo_path)

    return _get


def _make_staged_content_provider(repo_path: str) -> FileContentProvider:
    """Create a content provider that compares HEAD to the git index."""

    def _get(ref: str, file_path: str) -> str | None:
        if ref == ":index":
            return get_file_from_index(file_path, repo_path=repo_path)
        return get_file_at_ref(ref, file_path, repo_path=repo_path)

    return _get


def _make_worktree_content_provider(repo_path: str) -> FileContentProvider:
    """Create a provider comparing a commit baseline to current worktree files."""

    def _get(ref: str, file_path: str) -> str | None:
        return get_file_at_snapshot(ref, file_path, repo_path=repo_path)

    return _get


def _normalize_ref_range(ref_range: str, repo: str) -> str:
    """Resolve git's three-dot range to a concrete ``<merge-base>..<new>`` range.

    ``git diff A...B`` compares ``B`` against the merge-base of ``A`` and ``B``,
    so the symbol baseline must use that same base. When the merge-base resolves
    we rewrite ``A...B`` to ``<merge-base>..B`` — the diff and the per-file
    content fetch then agree on the baseline. If it can't be resolved the range
    is left untouched, letting ``git diff`` surface the error instead of the
    pipeline silently analyzing against the wrong base. Two-dot and bare ranges
    pass through unchanged.
    """
    if "..." not in ref_range:
        return ref_range
    old, _, new = ref_range.partition("...")
    base = get_merge_base(old or "HEAD", new or "HEAD", repo)
    if base is None:
        return ref_range
    return f"{base}..{new or 'HEAD'}"


def _format_output(
    output: DiffGuardOutput,
    fmt: str,
    tier: str,
    *,
    include_tests: bool,
    show_skipped: bool,
) -> str:
    """Format pipeline output according to --format flag."""
    if fmt == "json":
        return report.render_summary_json(output)
    selected_tier = fmt if fmt in ("oneliner", "short", "detailed") else tier
    return report.render_summary_text(
        output,
        selected_tier,
        include_tests=include_tests,
        show_skipped=show_skipped,
    )


@click.group()
@click.version_option(__version__, "--version", "-v")
def main() -> None:
    """DiffGuard — Catches the structural breaks that pass code review. Analyzes git diffs to surface high-signal changes."""


@main.command()
@click.argument("ref_range", required=False, default=None)
@click.option(
    "--diff",
    "diff_source",
    default=None,
    help="Read unified diff from stdin. Use '--diff -' for pipe input.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["json", "oneliner", "short", "detailed"]),
    default="json",
    help="Output format (default: json).",
)
@click.option(
    "--tier",
    type=click.Choice(["oneliner", "short", "detailed"]),
    default="detailed",
    help="Summary tier for JSON output (default: detailed).",
)
@click.option(
    "--skip-generated",
    "--no-generated",
    is_flag=True,
    default=False,
    help="Skip generated file detection.",
)
@click.option(
    "--include-tests",
    is_flag=True,
    default=False,
    help="Include test file changes in summary text output.",
)
@click.option(
    "--show-skipped",
    is_flag=True,
    default=False,
    help="Show skipped (unsupported/binary/generated) files in summary text.",
)
@click.option(
    "--repo",
    default=".",
    help="Repository path or a directory within it (default: current directory).",
)
def summarize(
    ref_range: str | None,
    diff_source: str | None,
    fmt: str,
    tier: str,
    skip_generated: bool,
    include_tests: bool,
    show_skipped: bool,
    repo: str,
) -> None:
    """Summarize git changes.

    REF_RANGE: Git ref range like HEAD~1..HEAD or main..feature.
    Default: staged, unstaged, and untracked worktree changes.
    """
    try:
        diff_text: str
        range_label: str
        content_provider: FileContentProvider | None

        if diff_source == "-":
            diff_text = sys.stdin.read()
            range_label = "stdin"
            content_provider = None
        else:
            repo = str(get_repository_root(repo))
            if ref_range is not None:
                ref_range = _normalize_ref_range(ref_range, repo)
                diff_text = get_diff(ref_range, repo_path=repo)
                range_label = ref_range
                content_provider = _make_content_provider(repo)
            else:
                diff_text = get_worktree_diff("HEAD", repo_path=repo)
                range_label = "HEAD..:worktree"
                content_provider = _make_worktree_content_provider(repo)

        if not diff_text.strip():
            if fmt == "json":
                click.echo(report.render_empty_summary_json(range_label))
            else:
                click.echo("No changes found.", err=True)
            sys.exit(EXIT_NO_CHANGES)

        output = run_pipeline(
            diff_text,
            range_label,
            content_provider,
            skip_generated=skip_generated,
            include_tests=include_tests,
            show_skipped=show_skipped,
        )

        has_parse_errors = any(fc.parse_error for fc in output.files)

        text = _format_output(
            output,
            fmt,
            tier,
            include_tests=include_tests,
            show_skipped=show_skipped,
        )
        click.echo(text)

        if has_parse_errors:
            sys.exit(EXIT_PARTIAL)
        sys.exit(EXIT_SUCCESS)

    except Exception as exc:
        logger.debug("CLI error", exc_info=True)
        click.echo(f"Error: {report.terminal_safe_text(str(exc))}", err=True)
        sys.exit(EXIT_ERROR)


def _run_review(
    ref_range: str,
    repo: str,
    deps: bool,
    verbose: bool,
    fmt: str,
    *,
    staged: bool = False,
    worktree: bool = False,
    against: str | None = None,
) -> None:
    """Core implementation behind the review command."""
    mode: ReviewMode = "worktree" if worktree else "staged" if staged else "committed"
    try:
        repo = str(get_repository_root(repo))
        if worktree:
            requested_base = against or "HEAD"
            if resolve_commit(requested_base, repo) is None:
                raise RuntimeError(f"Invalid base ref '{requested_base}'")
            base = get_merge_base(requested_base, "HEAD", repo)
            if base is None:
                raise RuntimeError(f"No merge base between '{requested_base}' and HEAD")
            diff_text = get_worktree_diff(base, repo_path=repo)
            ref_range = f"{base}..:worktree"
            content_provider = _make_worktree_content_provider(repo)
            reference_snapshot = ":worktree"
        elif staged:
            diff_text = get_staged_diff(repo_path=repo)
            ref_range = "HEAD..:index"
            content_provider = _make_staged_content_provider(repo)
            reference_snapshot = ":index"
        else:
            ref_range = _normalize_ref_range(ref_range, repo)
            diff_text = get_diff(ref_range, repo_path=repo)
            content_provider = _make_content_provider(repo)
            _, reference_snapshot = split_ref_range(ref_range)

        if not diff_text.strip():
            if fmt == "json":
                click.echo(report.render_empty_json(ref_range, mode, "no changes in diff"))
            else:
                click.echo("No changes found.", err=True)
            sys.exit(EXIT_SUCCESS)

        output = run_pipeline(diff_text, ref_range, content_provider)

        findings = extract_findings(output)
        names = list(dict.fromkeys(finding.change.name for finding in findings))
        dep_refs = None
        if deps and names:
            scan = scan_references(repo, names, reference_snapshot)
            dep_refs = scan.references
            output.meta.warnings.extend(scan.warnings)

        if dep_refs is not None:
            findings = extract_findings(output, dep_refs)
        has_findings = bool(findings)

        if fmt == "json":
            click.echo(report.render_json(output, ref_range, mode, findings))
            sys.exit(EXIT_FINDINGS if has_findings else EXIT_SUCCESS)

        # Text format
        if output.meta.warnings:
            click.echo("DiffGuard analysis warnings:", err=True)
            for warning in output.meta.warnings:
                click.echo(f"- {report.terminal_safe_text(warning)}", err=True)
        if not verbose and not has_findings:
            sys.exit(EXIT_SUCCESS)

        text = report.render_text(findings)
        if text:
            click.echo(text)
            sys.exit(EXIT_FINDINGS)
        sys.exit(EXIT_SUCCESS)

    except SystemExit:
        raise
    except Exception as exc:
        logger.debug("CLI error", exc_info=True)
        if fmt == "json":
            click.echo(report.render_error_json(ref_range, mode, str(exc)))
        else:
            click.echo(f"Error: {report.terminal_safe_text(str(exc))}", err=True)
        sys.exit(EXIT_ERROR)


@main.command()
@click.argument("ref_range", required=False, default=None)
@click.option(
    "--repo",
    default=".",
    help="Repository path or a directory within it (default: current directory).",
)
@click.option(
    "--deps/--no-deps", default=True, help="Enable dependency scanning (default: enabled)."
)
@click.option(
    "--verbose",
    is_flag=True,
    default=False,
    help="Show full output even when no high-signal changes.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format: 'text' for human-readable review, 'json' for structured output.",
)
@click.option(
    "--staged",
    is_flag=True,
    default=False,
    help="Review staged/index changes for pre-commit use.",
)
@click.option(
    "--worktree",
    is_flag=True,
    default=False,
    help="Review staged, unstaged, added, and deleted worktree state.",
)
@click.option(
    "--against",
    default=None,
    help="Base ref for --worktree; its merge base with HEAD is used (default: HEAD).",
)
def review(
    ref_range: str | None,
    repo: str,
    deps: bool,
    verbose: bool,
    fmt: str,
    staged: bool,
    worktree: bool,
    against: str | None,
) -> None:
    """Analyze git changes and surface high-signal findings for code review.

    REF_RANGE: Git ref range like HEAD~3..HEAD or main..feature.
    Default: HEAD~1..HEAD (last commit).

    Detects signature changes, breaking changes, removed/moved symbols,
    and reports syntactic imports, calls, and non-call references. Name matches
    do not prove symbol ownership.

    \b
    Exit codes:
      0 — No high-signal findings (silence)
      1 — Findings present (read the output)
      2 — Error
    """

    def _option_error(message: str, mode: ReviewMode) -> NoReturn:
        if fmt == "json":
            click.echo(report.render_error_json(ref_range or "", mode, message))
        else:
            click.echo(f"Error: {message}", err=True)
        raise SystemExit(EXIT_ERROR)

    if staged and ref_range is not None:
        _option_error("--staged cannot be combined with a ref range", "staged")
    if worktree and ref_range is not None:
        _option_error("--worktree cannot be combined with a ref range; use --against", "worktree")
    if staged and worktree:
        _option_error("--staged and --worktree are mutually exclusive", "worktree")
    if against is not None and not worktree:
        _option_error("--against requires --worktree", "worktree")
    if ref_range is None and not staged and not worktree:
        ref_range = "HEAD~1..HEAD"
    _run_review(
        ref_range or "HEAD",
        repo,
        deps,
        verbose,
        fmt,
        staged=staged,
        worktree=worktree,
        against=against,
    )


@main.command("install-hook")
@click.option(
    "--repo",
    default=".",
    help="Repository path or a directory within it (default: current directory).",
)
@click.option(
    "--hook-type",
    type=click.Choice(["pre-push", "pre-commit"]),
    default="pre-push",
    help="Git hook type to install (default: pre-push).",
)
@click.option("--force", is_flag=True, default=False, help="Overwrite existing hook.")
def install_hook(repo: str, hook_type: str, force: bool) -> None:
    """Install a git hook that runs diffguard review before push/commit."""
    try:
        repo = str(get_repository_root(repo))
        hook_path = hooks.install_hook(repo, hook_type, force=force)
    except (hooks.HookError, RuntimeError) as exc:
        click.echo(f"Error: {report.terminal_safe_text(str(exc))}", err=True)
        sys.exit(EXIT_ERROR)
    click.echo(f"Installed {hook_type} hook: {report.terminal_safe_text(hook_path)}")
