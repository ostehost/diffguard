#!/usr/bin/env python3
"""A/B test: Does diffguard context + deps help AI review agents?

For each test case:
  A (baseline): raw git diff → Claude review
  B (treatment): diffguard context output + raw git diff → Claude review

Outputs both reviews for manual blind comparison.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-20250514"

REVIEW_PROMPT = """You are a senior code reviewer. Review this pull request and list every issue you find.

For each issue, provide:
- **Severity**: critical / warning / info
- **File**: which file
- **Line**: approximate line if known
- **Issue**: what's wrong
- **Why**: why it matters

Focus on:
- Breaking changes that affect callers
- Missing updates to dependent code
- Type mismatches
- Behavioral changes that could cause bugs
- API contract violations

Be thorough. List ALL issues, even minor ones."""


def run_cmd(cmd, cwd=None):
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return result.stdout


def get_diff(repo_path, ref_range):
    return run_cmd(["git", "diff", ref_range], cwd=repo_path)


def get_diffguard_context(repo_path, ref_range):
    diffguard = Path(__file__).parent.parent / ".venv" / "bin" / "diffguard"
    result = subprocess.run(
        [str(diffguard), "context", ref_range, "--repo", str(repo_path)],
        capture_output=True,
        text=True,
        cwd=repo_path,
    )
    return result.stdout


def call_claude(system, user_content):
    """Call Claude API and return the response text."""
    import urllib.request

    body = json.dumps(
        {
            "model": MODEL,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": user_content}],
            "system": system,
        }
    )

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body.encode(),
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )

    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
        return data["content"][0]["text"]


def run_test(name, repo_path, ref_range):
    print(f"\n{'=' * 60}")
    print(f"TEST: {name}")
    print(f"Repo: {repo_path}, Range: {ref_range}")
    print(f"{'=' * 60}")

    diff = get_diff(repo_path, ref_range)
    context = get_diffguard_context(repo_path, ref_range)

    print(f"\nDiff size: {len(diff)} chars")
    print(f"Context size: {len(context)} chars")
    print(f"\n--- DiffGuard Context ---\n{context}\n--- End Context ---\n")

    # A: baseline (diff only)
    print("Running baseline review (diff only)...")
    review_a = call_claude(
        REVIEW_PROMPT, f"Here is the git diff to review:\n\n```diff\n{diff[:50000]}\n```"
    )

    # B: treatment (context + diff)
    print("Running treatment review (context + diff)...")
    review_b = call_claude(
        REVIEW_PROMPT,
        f"Here is structured context about the changes:\n\n{context}\n\nHere is the full git diff:\n\n```diff\n{diff[:50000]}\n```",
    )

    # Output
    outdir = Path(__file__).parent.parent / "tests" / "ab_results"
    outdir.mkdir(exist_ok=True)

    safe_name = name.replace(" ", "_").replace("/", "_")
    (outdir / f"{safe_name}_A_baseline.md").write_text(f"# Baseline Review: {name}\n\n{review_a}")
    (outdir / f"{safe_name}_B_treatment.md").write_text(f"# Treatment Review: {name}\n\n{review_b}")
    (outdir / f"{safe_name}_context.md").write_text(f"# DiffGuard Context: {name}\n\n{context}")

    print(f"\nResults written to tests/ab_results/{safe_name}_*.md")
    return review_a, review_b


TEST_CASES = [
    {
        "name": "react-restructure",
        "repo": str(Path(__file__).parent.parent / "tests/ab_repos/react-test-app"),
        "ref_range": "HEAD~1..HEAD",
    },
    {
        "name": "diffguard-filter-tests",
        "repo": str(Path(__file__).parent.parent),
        "ref_range": "2f5d45a..335190f",  # feat: unsupported file warning + README showcase
    },
    {
        "name": "diffguard-cli-packaging",
        "repo": str(Path(__file__).parent.parent),
        "ref_range": "11efd59..f549226",  # Phase 4 — Full CLI + PyPI packaging
    },
]


if __name__ == "__main__":
    if not ANTHROPIC_API_KEY:
        print("Set ANTHROPIC_API_KEY environment variable")
        sys.exit(1)

    for tc in TEST_CASES:
        try:
            run_test(tc["name"], tc["repo"], tc["ref_range"])
        except Exception as e:
            print(f"ERROR on {tc['name']}: {e}")
