# DiffGuard

DiffGuard provides deterministic contract-change and blast-radius evidence for coding agents. It runs locally, parses supported languages with tree-sitter, and emits a validated review envelope instead of an opinionated risk score.

## Primary closeout command

```bash
uv run diffguard review --against origin/main --worktree --format json
```

Worktree mode compares the merge base of the selected base and `HEAD` with staged, unstaged, added, and deleted files. Existing committed-range and `--staged` modes remain available.

## What the evidence means

- Signature, removal, and move findings are syntactic facts from the two parsed snapshots.
- Python has bounded call-shape compatibility rules. Annotation changes remain compatibility-unknown.
- Extracted TypeScript/JavaScript function, arrow-function, class, and class-method declarations and
  Go function/method declarations are reported as syntax; compiler/type compatibility is not
  claimed. TypeScript overload/interface signatures and Go interface methods are not extracted.
- Imports, calls, and non-call references are AST-context name matches with `resolution: "unresolved"` and low ownership confidence.
- Parse gaps and unavailable content become warnings; affected files do not produce fabricated symbol findings.

## Version boundary

This guide documents the `0.2.0` contract. Earlier releases do not include worktree review or the
current review schema. The composite Action installs code from its selected Action checkout and
should be pinned to an immutable commit SHA.

## Measured validation

The network-free corpus reports its current checked-in case counts, misses, false positives, parse
gaps, and per-rule metrics. These figures are corpus-local and do not establish general real-world
precision.

Continue with the [Quickstart](quickstart.md), [Agent Integration](agent-integration.md), [Schema Reference](schema.md), and [Validation](validation.md).
