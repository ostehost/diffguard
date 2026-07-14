# Quick Start

## Install the `0.2.0` contract

The commands below require DiffGuard `0.2.0` or newer. Install a compatible published release when
available:

```bash
python -m pip install "diffguard>=0.2.0,<0.3"
```

Or use the repository checkout:

```bash
uv sync --locked --group dev --group docs
uv run diffguard --version
```

## Review modes

```bash
# Existing committed-range semantics
uv run diffguard review HEAD~1..HEAD
uv run diffguard review main..feature --format json

# Existing index-only semantics
uv run diffguard review --staged --format json

# Agent closeout: base merge-base versus current worktree
uv run diffguard review --against origin/main --worktree --format json
```

`--worktree` includes staged and unstaged tracked changes plus added/deleted files. It cannot be combined with a positional ref range; use `--against`. Without `--against`, its base defaults to `HEAD`.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | No findings. Inspect warnings and parse-gap statistics before treating the run as complete. |
| `1` | Findings exist in stdout. |
| `2` | Tool error. JSON remains schema-valid when `--format json` was requested. |

## Review JSON shape

An empty worktree result has this shape:

```json
{
  "version": "1.1.0",
  "status": "ok",
  "mode": "worktree",
  "ref_range": "abc123..:worktree",
  "findings": [],
  "warnings": [],
  "stats": {
    "files_analyzed": 0,
    "symbols_changed": 0,
    "parse_errors": 0,
    "reference_count": 0,
    "silence_reason": "no changes in diff"
  },
  "error": null
}
```

Findings add stable `rule_id`/`category_id`, factual `breaking`, `confidence`, `evidence`,
`analysis_gaps`, and unresolved syntactic `references`. Move findings also set optional
`source_file` evidence while `file` remains the destination. See the
[Schema Reference](schema.md).

## Summaries

`summarize` emits the `schema_version: "2.0"` contract. The version changed because
`SymbolChange.breaking` is now tri-state (`true`, `false`, or `null`):

```bash
# HEAD versus staged, unstaged, and untracked worktree state (default)
uv run diffguard summarize --format json

# Explicit committed range
uv run diffguard summarize HEAD~1..HEAD --format json
```

Repository-backed commands resolve the top-level worktree first, so running either command
from a nested directory preserves repository-relative paths and includes top-level changes.

Use `review` for a selective verifier closeout and `summarize` when an agent needs the broader structural map.
An empty diff makes `summarize` exit `3`; with `--format json`, it still emits a valid
`schema_version: "2.0"` envelope with zero files and changes.
