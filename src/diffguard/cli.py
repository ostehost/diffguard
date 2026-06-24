"""DiffGuard CLI entry point."""

from __future__ import annotations

import logging
import sys

import click

from diffguard import __version__, hooks, report
from diffguard.engine.findings import extract_findings, has_high_signal, scan_dependencies
from diffguard.engine.pipeline import FileContentProvider, run_pipeline
from diffguard.git import (
    get_diff,
    get_file_at_ref,
    get_file_from_index,
    get_merge_base,
    get_staged_diff,
)
from diffguard.schema import DiffGuardOutput

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
) -> str:
    """Format pipeline output according to --format flag."""
    if fmt == "json":
        return output.model_dump_json(indent=2)
    # Non-JSON: the format flag determines which tier to show
    if fmt in ("oneliner", "short", "detailed"):
        return str(getattr(output.tiered, fmt))
    # Fallback to tier
    return str(getattr(output.tiered, tier))


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
    help="Repository path (default: current directory).",
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
    Default: unstaged changes.
    """
    try:
        diff_text: str
        range_label: str
        content_provider: FileContentProvider | None

        if diff_source == "-":
            diff_text = sys.stdin.read()
            range_label = "stdin"
            content_provider = None
        elif ref_range is not None:
            ref_range = _normalize_ref_range(ref_range, repo)
            diff_text = get_diff(ref_range, repo_path=repo)
            range_label = ref_range
            content_provider = _make_content_provider(repo)
        else:
            diff_text = get_diff("HEAD", repo_path=repo)
            range_label = "HEAD (unstaged)"
            content_provider = _make_content_provider(repo)

        if not diff_text.strip():
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

        text = _format_output(output, fmt, tier)
        click.echo(text)

        if has_parse_errors:
            sys.exit(EXIT_PARTIAL)
        sys.exit(EXIT_SUCCESS)

    except Exception as exc:
        logger.debug("CLI error", exc_info=True)
        click.echo(f"Error: {exc}", err=True)
        sys.exit(EXIT_ERROR)


def _run_review(
    ref_range: str,
    repo: str,
    deps: bool,
    verbose: bool,
    fmt: str,
    *,
    staged: bool = False,
) -> None:
    """Core implementation behind the review command."""
    try:
        if staged:
            diff_text = get_staged_diff(repo_path=repo)
            ref_range = "HEAD..:index"
            content_provider = _make_staged_content_provider(repo)
        else:
            ref_range = _normalize_ref_range(ref_range, repo)
            diff_text = get_diff(ref_range, repo_path=repo)
            content_provider = _make_content_provider(repo)

        if not diff_text.strip():
            if fmt == "json":
                click.echo(report.render_empty_json(ref_range, "no changes in diff"))
            else:
                click.echo("No changes found.", err=True)
            sys.exit(EXIT_SUCCESS)

        output = run_pipeline(diff_text, ref_range, content_provider)

        # Staged review compares HEAD to the index; dependency scanning currently
        # works on committed refs, so pre-commit mode analyzes only the staged diff.
        dep_refs = scan_dependencies(output, ref_range, repo) if deps and not staged else None

        findings = extract_findings(output, dep_refs)
        has_findings = has_high_signal(output)

        if fmt == "json":
            click.echo(report.render_json(output, ref_range, findings))
            sys.exit(EXIT_FINDINGS if has_findings else EXIT_SUCCESS)

        # Text format
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
        click.echo(f"Error: {exc}", err=True)
        sys.exit(EXIT_ERROR)


@main.command()
@click.argument("ref_range", required=False, default=None)
@click.option("--repo", default=".", help="Repository path (default: current directory).")
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
def review(
    ref_range: str | None,
    repo: str,
    deps: bool,
    verbose: bool,
    fmt: str,
    staged: bool,
) -> None:
    """Analyze git changes and surface high-signal findings for code review.

    REF_RANGE: Git ref range like HEAD~3..HEAD or main..feature.
    Default: HEAD~1..HEAD (last commit).

    Detects signature changes, breaking changes, removed/moved symbols,
    and finds callers that may be affected.

    \b
    Exit codes:
      0 — No high-signal findings (silence)
      1 — Findings present (read the output)
      2 — Error
    """
    if staged and ref_range is not None:
        click.echo("Error: --staged cannot be combined with a ref range", err=True)
        sys.exit(EXIT_ERROR)
    if ref_range is None:
        ref_range = "HEAD~1..HEAD"
    _run_review(ref_range, repo, deps, verbose, fmt, staged=staged)


@main.command("install-hook")
@click.option("--repo", default=".", help="Repository path (default: current directory).")
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
        hook_path = hooks.install_hook(repo, hook_type, force=force)
    except hooks.HookError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(EXIT_ERROR)
    click.echo(f"Installed {hook_type} hook: {hook_path}")
