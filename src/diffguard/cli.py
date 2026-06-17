"""DiffGuard CLI entry point."""

from __future__ import annotations

import logging
import sys

import click

from diffguard import __version__, report
from diffguard.engine.deps import Reference, find_references
from diffguard.engine.findings import extract_findings, has_high_signal
from diffguard.engine.pipeline import FileContentProvider, run_pipeline
from diffguard.git import get_diff, get_file_at_ref, get_file_from_index, get_staged_diff
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


def _scan_dependencies(
    output: DiffGuardOutput,
    ref_range: str,
    repo: str,
) -> list[Reference] | None:
    """Find external callers of every changed symbol, or None if there are none."""
    changed_symbols: list[str] = []
    changed_files: set[str] = set()
    for fc in output.files:
        changed_files.add(fc.path)
        changed_symbols.extend(sc.name for sc in fc.changes)

    if not changed_symbols:
        return None

    parts = ref_range.split("..")
    after_ref = parts[1] if len(parts) == 2 else ref_range  # noqa: PLR2004
    return find_references(
        repo_path=repo,
        changed_symbols=changed_symbols,
        ref=after_ref,
        changed_files=changed_files,
    )


def _run_review(
    ref_range: str,
    repo: str,
    deps: bool,
    verbose: bool,
    fmt: str,
    *,
    staged: bool = False,
) -> None:
    """Shared implementation for review/context commands."""
    try:
        if staged:
            diff_text = get_staged_diff(repo_path=repo)
            ref_range = "HEAD..:index"
            content_provider = _make_staged_content_provider(repo)
        else:
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
        dep_refs = _scan_dependencies(output, ref_range, repo) if deps and not staged else None

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


@main.command(hidden=True)
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
def context(ref_range: str | None, repo: str, deps: bool, verbose: bool, fmt: str) -> None:
    """Alias for 'review' (deprecated)."""
    if ref_range is None:
        ref_range = "HEAD~1..HEAD"
    _run_review(ref_range, repo, deps, verbose, fmt)


_PRE_PUSH_HOOK = """\
#!/bin/sh
# DiffGuard pre-push hook — runs diffguard review on pushed changes
# Installed by: diffguard install-hook

remote="$1"
z40=0000000000000000000000000000000000000000

while read local_ref local_sha remote_ref remote_sha; do
    if [ "$remote_sha" = "$z40" ]; then
        # New branch — compare against main/master
        base=$(git rev-parse --verify refs/heads/main 2>/dev/null || git rev-parse --verify refs/heads/master 2>/dev/null || echo "")
        if [ -z "$base" ]; then
            continue
        fi
        range="$base..$local_sha"
    else
        range="$remote_sha..$local_sha"
    fi

    echo "Running diffguard review $range ..."
    diffguard review "$range"
    status=$?
    if [ $status -eq 1 ]; then
        echo ""
        echo "DiffGuard found changes that need review (see above)."
        echo "Push anyway with: git push --no-verify"
        exit 1
    elif [ $status -ne 0 ]; then
        echo ""
        echo "DiffGuard failed with exit $status; blocking push."
        exit $status
    fi
done

exit 0
"""


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
    import os
    import stat

    git_dir = os.path.join(repo, ".git")
    if not os.path.isdir(git_dir):
        click.echo(f"Error: {repo} is not a git repository", err=True)
        sys.exit(EXIT_ERROR)

    hooks_dir = os.path.join(git_dir, "hooks")
    os.makedirs(hooks_dir, exist_ok=True)

    hook_path = os.path.join(hooks_dir, hook_type)
    if os.path.exists(hook_path) and not force:
        click.echo(f"Hook already exists: {hook_path}", err=True)
        click.echo("Use --force to overwrite.", err=True)
        sys.exit(EXIT_ERROR)

    hook_content = _PRE_PUSH_HOOK
    if hook_type == "pre-commit":
        hook_content = """\
#!/bin/sh
# DiffGuard pre-commit hook — runs diffguard review on staged changes
# Installed by: diffguard install-hook

echo "Running diffguard review --staged --no-deps ..."
diffguard review --staged --no-deps
status=$?
if [ $status -eq 1 ]; then
    echo ""
    echo "DiffGuard found changes that need review (see above)."
    echo "Commit anyway with: git commit --no-verify"
    exit 1
elif [ $status -ne 0 ]; then
    echo ""
    echo "DiffGuard failed with exit $status; blocking commit."
    exit $status
fi

exit 0
"""

    with open(hook_path, "w") as f:
        f.write(hook_content)

    # Make executable
    st = os.stat(hook_path)
    os.chmod(hook_path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    click.echo(f"Installed {hook_type} hook: {hook_path}")
