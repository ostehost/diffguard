[![PyPI](https://img.shields.io/pypi/v/diffguard)](https://pypi.org/project/diffguard/)
[![License](https://img.shields.io/badge/license-BSL%201.1-blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://pypi.org/project/diffguard/)

# DiffGuard

DiffGuard is a deterministic, local contract-change verifier for coding agents. It compares Git snapshots or a base commit with the current worktree, extracts supported-language symbols with tree-sitter, and emits Pydantic-validated findings about signature changes, removals, and moves.

It reports structural evidence, not review opinions. Reference locations are labeled as unresolved syntactic imports, calls, or non-call references; DiffGuard does not claim compiler-grade ownership resolution.

## Install and version boundary

The interfaces documented here require DiffGuard `0.2.0` or newer. Earlier releases do not include
worktree review or the current review schema. Install a compatible published release when available:

```bash
python -m pip install "diffguard>=0.2.0,<0.3"
```

Or run the same versioned code from a source checkout:

```bash
uv sync --locked --group dev --group docs
uv run diffguard review --against origin/main --worktree --format json
```

Review modes are explicit:

```bash
# Committed range (existing behavior)
uv run diffguard review main..feature --format json

# Index only (existing behavior)
uv run diffguard review --staged --format json

# Base merge-base versus staged + unstaged + added/deleted worktree state
uv run diffguard review --against origin/main --worktree --format json
```

`diffguard summarize` with no ref range compares `HEAD` with the full current worktree,
including staged, unstaged, and untracked files. Commands may be run from a repository
subdirectory; DiffGuard resolves the top-level worktree before binding Git paths to content.

Exit codes: `0` no findings, `1` findings, `2` tool error. After Click has successfully
parsed the command line, JSON review mode emits the review schema on all three paths. Click's own
usage errors, such as an unknown option, remain plain CLI diagnostics.

`summarize` additionally exits `3` when the diff is empty and `4` when some files cannot be
parsed. With `--format json`, the empty-diff path still emits a valid `schema_version: "2.0"`
envelope with zero files and changes.

## Evidence and limits

DiffGuard currently detects:

- pure signature edits even when the body is unchanged;
- bounded Python call-shape changes such as required parameter addition, removal, reorder, and default removal;
- Python annotation/default changes as syntactic contract evidence without treating annotations as runtime proof;
- signature syntax changes for extracted TypeScript/JavaScript function, arrow-function, class,
  and class-method declarations and for extracted Go function and method declarations;
- removed symbols and possible cross-file move candidates;
- unresolved AST-context imports, calls, and non-call references, including references in changed files.

It does not resolve imports or symbol ownership, run a compiler/type checker, detect arbitrary logic/security/performance bugs, or prove that a same-named reference targets the changed declaration. Parse gaps are warnings and suppress symbol findings for the affected file.

TypeScript overload declarations, TypeScript/JavaScript interface members, and Go interface methods
are not currently extracted. Compatibility for the extracted TypeScript/JavaScript and Go
declarations remains unknown without compiler and type information.

## Agent closeout

Use DiffGuard once near completion, resolve or explicitly explain findings and warnings, then run project checks. A repository skill is provided at [`.agents/skills/diffguard-closeout/SKILL.md`](.agents/skills/diffguard-closeout/SKILL.md). Current snippets are in the [agent integration guide](docs/agent-integration.md).

## GitHub Action

The composite Action installs DiffGuard from `github.action_path` against the exact runtime
dependency versions exported from `uv.lock`, so execution is tied to the selected Action checkout
rather than a PyPI release and runtime versions cannot drift within the package's public ranges.
Each run installs into a unique temporary virtual environment and removes it at closeout, avoiding
changes or concurrent-install races in setup-python's interpreter on persistent self-hosted
runners. The Action preinstalls the complete, exact PEP 517 backend closure from
`action-build-constraints.txt` and disables build isolation for the local checkout, preventing a
second unconstrained build-time resolution. Pin a reviewed immutable commit SHA:

`actions/setup-python` v6.3.0 runs on Node 24. Self-hosted Action consumers require GitHub
Actions Runner v2.327.1 or newer; GitHub-hosted runners are managed by GitHub.

```yaml
- uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
  with:
    fetch-depth: 0
    persist-credentials: false
- uses: ostehost/diffguard@<full-commit-sha>
  with:
    post-comment: "false"
```

Optional PR comments require `pull-requests: write`; keep them disabled when structured outputs are consumed elsewhere. Action output is bounded to protect GitHub's per-job output limit: check `findings-truncated` before treating `findings` as complete. Also require `analysis-incomplete` to be `false`: exit `0` can still carry warnings or parse gaps, and the Action reports those as incomplete instead of clearing its PR comment. When truncation occurs, the full protected output remains in the step log.

On a `pull_request` run with `post-comment: "true"`, omit `ref-range`: the Action rejects a custom range before invoking DiffGuard so the posted comment's base/head identity always matches the range actually reviewed. Custom ranges remain available when comments are disabled or outside a pull-request event.

When comments are enabled, queue the review job per PR as defense in depth and to preserve final ordering; cancellation is not an API-write fence. The Action's correctness boundary is its non-overlapping v2 comment identity, versioned by the analyzed base and head SHAs. It snapshots, adopts, updates, and deletes only comments for that exact state, then rechecks freshness after a findings write and rolls back only its own state if stale. Because GitHub does not document compare-and-swap for comment writes, the Action never mutates cross-state, future-version, or legacy comments. Historical-state comments can therefore remain; each v2 comment visibly labels its analyzed base and head so a base-only update cannot make an older result look current.

```yaml
jobs:
  diffguard:
    concurrency:
      group: ${{ github.workflow }}-diffguard-${{ github.event.pull_request.number || github.run_id }}
      cancel-in-progress: false
```

## Validation

`just validate-corpus` runs the network-free synthetic corpus and prints its current case counts,
misses, false positives, expected/observed parse-gap drift, and per-rule metrics. Its check mode
fails when an expected parse gap disappears as well as when an unexpected one appears. Those
results describe only the checked-in labels, not real-world precision.

Canonical local gates:

```bash
uv lock --check
just ci
just validate-corpus
just docs-build
just build
```

Supported languages: Python, TypeScript/JavaScript, and Go. No additional languages are part of this recovery.

BSL 1.1 — see [LICENSE](LICENSE).
