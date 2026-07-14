# Agent Integration Guide

Run DiffGuard once near task completion against actual worktree state:

```bash
set +e
python3 .agents/skills/diffguard-closeout/scripts/run_review.py \
  --base origin/main \
  --timeout-seconds 300 \
  --max-output-bytes 10485760
rc=$?
set -e
```

Interpret `rc` exactly: `0` no findings, `1` findings, `2` tool or
resource-bound error. On every exit, record the two artifact paths printed by
the runner, validate/read the JSON, and inspect the bounded stderr artifact,
warnings, parse errors, evidence, and analysis gaps. Resolve each finding or
record a specific explanation. Rerun only after a relevant code change, then
run project checks.

On supported macOS/Linux POSIX agent hosts, the standard-library runner avoids
a dependency on GNU `timeout` and invokes the project through `uv run --locked`.
It is not a Windows runner. It stops the review process group after five minutes
or 10 MiB of combined output, maps a bound violation or unexpected child status
to exit `2`, and creates unique JSON and stderr artifacts with mode `0600`, so
concurrent agents cannot clobber one shared result. Retain both as the forensic
receipt through final review or handoff. Report their unique paths and cleanup
owner, then remove only those exact paths after the receiver acknowledges the
handoff; never use a glob or clean the shared temporary directory.

Do not use an after-edit `HEAD~1..HEAD` hook: it inspects the last commit, not the current edits. Prefer Stop/TaskCompleted/finish guidance and `--worktree`.

## Repository skill

The repository-local `diffguard-closeout` skill at `.agents/skills/diffguard-closeout/SKILL.md` contains the complete bounded workflow and canonical checks.

## AGENTS.md snippet

```markdown
## Closeout contract verification

Near task completion, run `diffguard review --against origin/main --worktree --format json` once. Exit 1 means findings, not tool failure: resolve each finding or explain it with evidence. Exit 2 is an error. Inspect warnings/parse gaps, then run the repository's required checks. Treat references as unresolved syntactic evidence, not exact callers.
```

## CLAUDE.md snippet

```markdown
At task completion, run `diffguard review --against origin/main --worktree --format json`. Handle exits 0/1/2 explicitly; never append `|| true`. Resolve or explain findings and warnings before final project checks. Do not run a full scan after every edit.
```

See the standalone [CLAUDE.md snippet](claude-md-snippet.md).

## GitHub Copilot instructions snippet

Add to `.github/copilot-instructions.md`:

```markdown
Before declaring a coding task complete, run `diffguard review --against origin/main --worktree --format json`. Treat exit 1 as structured findings and exit 2 as failure. Read warnings and analysis gaps, and never describe unresolved syntactic references as proven callers.
```

See the standalone [GitHub Copilot instructions](github-copilot-instructions.md).

## Claude Code TaskCompleted/Stop wrapper

Claude Code hook exits are not DiffGuard exits: hook exit `2` blocks `Stop` or `TaskCompleted`, while hook exit `1` is non-blocking. The wrapper must therefore translate DiffGuard findings/errors to hook exit `2`. For a `Stop` hook, consume the hook JSON and allow the second stop attempt when `stop_hook_active` is already true so the hook cannot continue forever.

```bash
hook_input=$(cat)
if ! stop_hook_active=$(printf '%s' "$hook_input" | python3 -c \
  'import json, sys; print(str(bool(json.load(sys.stdin).get("stop_hook_active", False))).lower())'); then
  echo "Invalid Claude hook input JSON" >&2
  exit 2
fi
if [ "$stop_hook_active" = "true" ]; then
  exit 0
fi

set +e
runner_output=$(python3 .agents/skills/diffguard-closeout/scripts/run_review.py \
  --base origin/main \
  --timeout-seconds 300 \
  --max-output-bytes 10485760)
rc=$?
set -e
printf '%s\n' "$runner_output" >&2
case "$rc" in
  0)
    if ! review_artifact=$(printf '%s\n' "$runner_output" | python3 -c '
import json
import sys

prefix = "DiffGuard review artifact: "
paths = [
    json.loads(line.removeprefix(prefix))
    for line in sys.stdin.read().splitlines()
    if line.startswith(prefix)
]
if len(paths) != 1 or not isinstance(paths[0], str) or not paths[0]:
    raise SystemExit(2)
print(paths[0])
'); then
      printf 'DiffGuard clean result had no unique review artifact; retain any artifacts above.\n' >&2
      exit 2
    fi
    if ! python3 - "$review_artifact" <<'PY'
import json
import sys
from pathlib import Path

try:
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    stats = payload.get("stats") if isinstance(payload, dict) else None
    complete = (
        isinstance(payload, dict)
        and payload.get("status") == "ok"
        and payload.get("warnings") == []
        and isinstance(stats, dict)
        and type(stats.get("parse_errors")) is int
        and stats["parse_errors"] == 0
    )
except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError):
    complete = False
raise SystemExit(0 if complete else 2)
PY
    then
      printf 'DiffGuard clean result is invalid or incomplete; inspect and retain the artifacts above.\n' >&2
      exit 2
    fi
    exit 0
    ;;
  1) printf 'DiffGuard findings require resolution; retain the artifacts above.\n' >&2; exit 2 ;;
  2) printf 'DiffGuard tool/resource error; inspect and retain the artifacts above.\n' >&2; exit 2 ;;
  *) printf 'Unexpected closeout-runner exit %s; retain any artifacts above.\n' "$rc" >&2; exit 2 ;;
esac
```

Install/trust this wrapper as repository code and pin the DiffGuard
version/source used by the agent. It uses Python, already required by
DiffGuard, rather than making loop prevention depend on an optional JSON
utility or GNU `timeout`. A TaskCompleted hook does not provide
`stop_hook_active`; the `False` fallback leaves its first failure blocking as
intended. Even after runner exit `0`, the wrapper fails closed unless the
retained JSON has `status: "ok"`, no warnings, and an integer
`stats.parse_errors` of zero. The bounded runner retains private artifacts on
clean, findings, error, timeout, output overflow, and interruption paths. The
inspecting agent owns cleanup after the completion decision or handoff is
acknowledged.

## GitHub Action

Pin the Action to an immutable reviewed SHA. The composite Action installs from `${{ github.action_path }}`, so it cannot drift to an unrelated PyPI version. Each run uses and then removes a unique temporary virtual environment, including on persistent self-hosted runners. Runtime dependencies and the complete PEP 517 backend closure are exact-constrained separately; the local checkout is built without a second isolated dependency resolution.

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

Set `post-comment: "true"` only when PR comments are intended and `pull-requests: write` is granted. A `pull_request` workflow must keep it false for public-fork and Dependabot runs, whose `GITHUB_TOKEN` is read-only; the example workflow uses a same-repository, non-Dependabot expression so those runs still analyze the change without attempting a comment API write. Its `id: diffguard` exposes the no-comment fallback as `steps.diffguard.outputs.findings`, `steps.diffguard.outputs.findings-truncated`, `steps.diffguard.outputs.analysis-incomplete`, and `steps.diffguard.outputs.exit-code`; downstream policy can surface or gate on those outputs without any API mutation. Require `analysis-incomplete` to be `false` before treating exit `0` as a clean review: structured JSON warnings, parse errors, invalid JSON, and the text format's analysis-warning stream all make the result incomplete. Oversized output is bounded for GitHub's per-job output limit while the full protected output remains in the step log. When comments are enabled, an incomplete run creates or updates the owned comment with an explicit warning instead of deleting prior findings.

On a `pull_request` run with `post-comment: "true"`, omit `ref-range`. The Action rejects a custom range before invoking DiffGuard so the visible comment and its hidden base/head identity always describe the range actually reviewed. Custom ranges remain available when comments are disabled or outside a pull-request event.

For a comment-enabled PR workflow, queue per-PR jobs as defense in depth and to preserve final ordering; cancellation is not an API-write fence. The Action's correctness boundary is its non-overlapping hidden v2 identity, versioned by the analyzed base and head SHAs. It snapshots, adopts, updates, and deletes only comments for that exact state, then rechecks freshness after a findings write and rolls back only its own state if stale. Because GitHub does not document compare-and-swap for comment writes, the Action never mutates cross-state, future-version, or legacy comments. Historical-state comments can therefore remain; each v2 comment visibly labels its analyzed base and head so a base-only update cannot make an older result look current.

```yaml
jobs:
  diffguard:
    concurrency:
      group: ${{ github.workflow }}-diffguard-${{ github.event.pull_request.number || github.run_id }}
      cancel-in-progress: false
```

## Evidence boundary

DiffGuard references are AST-context name matches. Use `kind`, `confidence`, `resolution`, and `evidence`; independently inspect imports/modules before assigning ownership. Run the target project's compiler, type checker, tests, and required checks after DiffGuard.
