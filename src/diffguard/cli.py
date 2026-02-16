"""DiffGuard CLI entry point."""

from __future__ import annotations

import json
import logging
import re
import sys

import click

from diffguard import __version__
from diffguard.engine.deps import find_references
from diffguard.engine.pipeline import FileContentProvider, run_pipeline
from diffguard.git import get_diff, get_file_at_ref
from diffguard.schema import FileChange, DiffGuardOutput, SymbolChange

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


# ---------------------------------------------------------------------------
# context command
# ---------------------------------------------------------------------------


def _has_high_signal_changes(
    output: DiffGuardOutput,
    dep_refs: list | None = None,
) -> bool:
    """Check if there are any high-signal changes worth reporting."""
    for fc in output.files:
        for sc in fc.changes:
            # Signature changes (before != after)
            if sc.before_signature and sc.after_signature:
                return True
            # Breaking changes
            if sc.breaking:
                return True
            # Removed symbols
            if sc.kind.endswith("_removed"):
                return True
            # Moved symbols
            if sc.kind == "moved":
                return True

    # External dependency references only matter if we already found
    # high-signal changes for those symbols (checked above).
    # Don't trigger on dep_refs alone — body-only changes with callers
    # are not high-signal.

    return False


def _categorize_change(sc: SymbolChange) -> str:
    """Return a category label for a symbol change."""
    from diffguard.engine.signatures import classify_signature_change

    if sc.kind.endswith("_removed"):
        return "SYMBOL REMOVED"
    if sc.kind == "moved":
        return "SYMBOL MOVED"
    if sc.before_signature and sc.after_signature:
        return classify_signature_change(sc.before_signature, sc.after_signature)
    return "CHANGED"


def _review_hint_for_category(category: str) -> str:
    """Return a review hint string for a given category."""
    hints = {
        "PARAMETER REMOVED": "These callers will break — removed parameter no longer accepted",
        "PARAMETER ADDED (BREAKING)": "These callers will break — missing required argument",
        "RETURN TYPE CHANGED": "Callers depending on the return type may break",
        "DEFAULT VALUE CHANGED": "Verify callers expect the new default value",
        "BREAKING SIGNATURE CHANGE": "Check all callers handle the new signature",
        "SIGNATURE CHANGED": "Review the signature change for compatibility",
        "SYMBOL REMOVED": "Ensure no remaining callers depend on this symbol",
        "SYMBOL MOVED": "Update imports in dependent files",
    }
    return hints.get(category, "Review this change")


def _shorten_paths(paths: list[str]) -> list[str]:
    """Strip common prefix from a list of paths."""
    if not paths:
        return paths
    if len(paths) == 1:
        # Just use filename
        return [paths[0].rsplit("/", 1)[-1]]
    # Find common prefix
    parts = [p.split("/") for p in paths]
    prefix_len = 0
    for i in range(min(len(p) for p in parts)):
        if len(set(p[i] for p in parts)) == 1:
            prefix_len = i + 1
        else:
            break
    if prefix_len > 0:
        return ["/".join(p[prefix_len:]) for p in parts]
    return paths


def _format_context_output(
    output: DiffGuardOutput,
    ref_range: str,
    dep_refs: list | None = None,
) -> str:
    """Format pipeline output as actionable review instructions."""
    from diffguard.engine.deps import Reference
    from diffguard.engine.summarizer import is_test_file

    # Collect high-signal changes
    items: list[tuple[FileChange, SymbolChange]] = []
    for fc in output.files:
        for sc in fc.changes:
            if (
                (sc.before_signature and sc.after_signature)
                or sc.breaking
                or sc.kind.endswith("_removed")
                or sc.kind == "moved"
            ):
                items.append((fc, sc))

    if not items:
        return ""

    # Build dep lookup: symbol_name -> list of references
    dep_map: dict[str, list[Reference]] = {}
    if dep_refs:
        for ref in dep_refs:
            dep_map.setdefault(ref.symbol_name, []).append(ref)

    lines: list[str] = [
        f"⚠ DiffGuard: {len(items)} change{'s' if len(items) != 1 else ''} need{'s' if len(items) == 1 else ''} review"
    ]
    lines.append("")

    for idx, (fc, sc) in enumerate(items, 1):
        category = _categorize_change(sc)
        sig_text = _sig_display(sc)
        line_ref = f":{sc.line}" if sc.line else ""

        lines.append(f"{idx}. {category}: {sig_text}")
        lines.append(f"   File: {fc.path}{line_ref}")

        # Impact section
        call_refs = dep_map.get(sc.name, [])
        call_refs = [r for r in call_refs if r.context == "call"]

        # Separate test vs prod callers
        test_refs = [r for r in call_refs if is_test_file(r.file_path)]
        prod_refs = [r for r in call_refs if not is_test_file(r.file_path)]

        if sc.breaking:
            if prod_refs:
                lines.append(
                    f"   Impact: {len(prod_refs)} caller{'s' if len(prod_refs) != 1 else ''} rely on the default:"
                )
                for r in prod_refs[:5]:
                    short_path = r.file_path.rsplit("/", 1)[-1]
                    lines.append(f"     {short_path}:{r.line}  `{r.source_line}`")
            else:
                lines.append("   Impact: Breaking change")
        elif sc.before_signature and sc.after_signature and not sc.breaking:
            if prod_refs:
                caller_parts = []
                by_file: dict[str, int] = {}
                for r in prod_refs:
                    fname = r.file_path.rsplit("/", 1)[-1]
                    by_file[fname] = by_file.get(fname, 0) + 1
                caller_parts = [
                    f"{f} ({n} call{'s' if n != 1 else ''})" for f, n in by_file.items()
                ]
                lines.append("   Impact: Backward-compatible (new kwarg has default)")
                lines.append(f"   Callers: {', '.join(caller_parts)}")
            else:
                lines.append("   Impact: Backward-compatible (new kwarg has default)")
        elif sc.kind.endswith("_removed"):
            if prod_refs:
                lines.append(
                    f"   Impact: {len(prod_refs)} caller{'s' if len(prod_refs) != 1 else ''} will break:"
                )
                for r in prod_refs[:5]:
                    short_path = r.file_path.rsplit("/", 1)[-1]
                    lines.append(f"     {short_path}:{r.line}  `{r.source_line}`")
            else:
                lines.append("   Impact: Symbol removed")

        # Show test callers compactly
        if test_refs:
            # Group by file
            by_file: dict[str, int] = {}
            for r in test_refs:
                fname = r.file_path.rsplit("/", 1)[-1]
                by_file[fname] = by_file.get(fname, 0) + 1
            parts = [f"{f} ({n} call{'s' if n != 1 else ''})" for f, n in by_file.items()]
            lines.append(f"   Callers: {', '.join(parts)}")

        # Review instruction
        lines.append(f"   Review: {_review_hint_for_category(category)}")

        lines.append("")

    return "\n".join(lines).rstrip()


def _sig_display(sc: SymbolChange) -> str:
    """Format signature change display — compact, one-line."""

    def _compact_sig(sig: str) -> str:
        """Extract just the def/class line and collapse to one line."""
        # Strip decorators — find the first 'def ' or 'class ' line
        for line in sig.split("\n"):
            stripped = line.strip()
            if stripped.startswith(("def ", "class ", "func ", "function ")):
                # If multi-line params, collapse them
                if "(" in stripped and ")" not in stripped:
                    # Grab remaining lines until closing paren
                    start = sig.index(stripped)
                    rest = sig[start:]
                    paren_depth = 0
                    result_chars = []
                    for ch in rest:
                        if ch == "(":
                            paren_depth += 1
                        elif ch == ")":
                            paren_depth -= 1
                        if ch == "\n":
                            ch = " "
                        result_chars.append(ch)
                        if paren_depth == 0 and ch == ")":
                            # Grab return type if present
                            remaining = rest[len("".join(result_chars)) :]
                            arrow = remaining.split("\n")[0].strip()
                            if arrow.startswith("->"):
                                result_chars.append(f" {arrow}")
                            break
                    return " ".join("".join(result_chars).split())
                return stripped
        # Fallback: collapse whole thing
        return " ".join(sig.split())

    def _strip_keyword(sig: str) -> str:
        """Strip leading def/class/func/function keyword for compact display."""
        for kw in ("def ", "class ", "func ", "function "):
            if sig.startswith(kw):
                sig = sig[len(kw) :]
                break
        # Strip return type annotation and trailing colon for compactness
        sig = re.sub(r"\)\s*->.*$", ")", sig)
        sig = sig.rstrip(":")
        return sig

    if sc.before_signature and sc.after_signature:
        before = _strip_keyword(_compact_sig(sc.before_signature))
        after = _strip_keyword(_compact_sig(sc.after_signature))
        return f"{before} → {after}"
    if sc.signature:
        return _strip_keyword(_compact_sig(sc.signature))
    return f"`{sc.name}`"


def _build_json_output(
    output: DiffGuardOutput,
    ref_range: str,
    dep_refs: list | None = None,
) -> str:
    """Build structured JSON output for the review command."""
    from diffguard.engine.deps import Reference
    from diffguard.engine.summarizer import is_test_file

    dep_map: dict[str, list[Reference]] = {}
    if dep_refs:
        for ref in dep_refs:
            dep_map.setdefault(ref.symbol_name, []).append(ref)

    findings = []
    for fc in output.files:
        for sc in fc.changes:
            if not (
                (sc.before_signature and sc.after_signature)
                or sc.breaking
                or sc.kind.endswith("_removed")
                or sc.kind == "moved"
            ):
                continue

            category = _categorize_change(sc)

            call_refs = dep_map.get(sc.name, [])
            call_refs = [r for r in call_refs if r.context == "call"]
            test_refs = [r for r in call_refs if is_test_file(r.file_path)]
            prod_refs = [r for r in call_refs if not is_test_file(r.file_path)]

            callers = []
            for r in (prod_refs + test_refs)[:10]:
                callers.append(
                    {
                        "file": r.file_path,
                        "line": r.line,
                        "source": r.source_line,
                    }
                )

            finding: dict = {
                "category": category.replace(" ", "_"),
                "symbol": sc.name,
                "file": fc.path,
                "line": sc.line,
            }
            if sc.before_signature:
                finding["before_signature"] = sc.before_signature.strip()
            if sc.after_signature:
                finding["after_signature"] = sc.after_signature.strip()

            finding["impact"] = {
                "production_callers": len(prod_refs),
                "test_callers": len(test_refs),
                "callers": callers,
            }

            finding["review_hint"] = _review_hint_for_category(category)

            findings.append(finding)

    symbols_changed = sum(len(fc.changes) for fc in output.files)
    result = {
        "version": "0.1.0",
        "ref_range": ref_range,
        "findings": findings,
        "stats": {
            "files_analyzed": len(output.files),
            "symbols_changed": symbols_changed,
            "silence_reason": None if findings else "no high-signal changes",
        },
    }
    return json.dumps(result, indent=2)


def _run_review(ref_range: str, repo: str, deps: bool, verbose: bool, fmt: str) -> None:
    """Shared implementation for review/context commands."""
    try:
        diff_text = get_diff(ref_range, repo_path=repo)

        if not diff_text.strip():
            if fmt == "json":
                click.echo(
                    json.dumps(
                        {
                            "version": "0.1.0",
                            "ref_range": ref_range,
                            "findings": [],
                            "stats": {
                                "files_analyzed": 0,
                                "symbols_changed": 0,
                                "silence_reason": "no changes in diff",
                            },
                        },
                        indent=2,
                    )
                )
            else:
                click.echo("No changes found.", err=True)
            sys.exit(EXIT_SUCCESS)

        content_provider = _make_content_provider(repo)
        output = run_pipeline(diff_text, ref_range, content_provider)

        dep_refs = None
        if deps:
            changed_symbols = []
            changed_files = set()
            for fc in output.files:
                changed_files.add(fc.path)
                for sc in fc.changes:
                    changed_symbols.append(sc.name)

            if changed_symbols:
                parts = ref_range.split("..")
                after_ref = parts[1] if len(parts) == 2 else ref_range  # noqa: PLR2004
                dep_refs = find_references(
                    repo_path=repo,
                    changed_symbols=changed_symbols,
                    ref=after_ref,
                    changed_files=changed_files,
                )

        has_findings = _has_high_signal_changes(output, dep_refs)

        if fmt == "json":
            click.echo(_build_json_output(output, ref_range, dep_refs))
            sys.exit(EXIT_FINDINGS if has_findings else EXIT_SUCCESS)

        # Text format
        if not verbose and not has_findings:
            sys.exit(EXIT_SUCCESS)

        text = _format_context_output(output, ref_range, dep_refs)
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
def review(ref_range: str | None, repo: str, deps: bool, verbose: bool, fmt: str) -> None:
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
    if ref_range is None:
        ref_range = "HEAD~1..HEAD"
    _run_review(ref_range, repo, deps, verbose, fmt)


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

echo "Running diffguard review HEAD ..."
diffguard review HEAD
status=$?
if [ $status -eq 1 ]; then
    echo ""
    echo "DiffGuard found changes that need review (see above)."
    echo "Commit anyway with: git commit --no-verify"
    exit 1
fi

exit 0
"""

    with open(hook_path, "w") as f:
        f.write(hook_content)

    # Make executable
    st = os.stat(hook_path)
    os.chmod(hook_path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    click.echo(f"Installed {hook_type} hook: {hook_path}")
