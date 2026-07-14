# Validation

## Reproducible local corpus

Run:

```bash
just validate-corpus
```

The command reads `tests/fixtures/corpus/contract_cases.json`, executes parser→matcher→classifier behavior without network access, and emits JSON containing expected findings, misses, false positives, expected/observed/matched/unexpected/missing parse-gap metrics, overall precision/recall, and per-rule metrics. `--check` fails on a miss, false positive, unexpected parse gap, or an expected parse gap that disappears without its corpus label being updated.

The command output is the source of truth for current case and finding totals. Covered labels include
Python parameter/default/annotation/return changes; parameter/return syntax on extracted
TypeScript function declarations and extracted Go function declarations; unchanged/body-only
negatives; and an expected Python parse gap. It does not cover TypeScript overload/interface
signatures or Go interface methods because those declarations are outside the current extractor
boundary.

These are small synthetic regression cases. Their precision/recall values prove only that the
current implementation matches the checked-in labels. They are not a claim of real-world accuracy,
exact dependency resolution, or compiler equivalence.

## Hermetic end-to-end matrix

Temporary local Git repository tests cover:

- clean worktree;
- staged-only and unstaged-only edits;
- mixed staged/unstaged state;
- staged and untracked additions;
- deleted files;
- renamed symbols;
- invalid base refs with schema-valid JSON errors;
- parse gaps with warnings and no fabricated findings;
- dependency/reference scanning against current worktree state.

Committed ranges and staged/index review remain covered separately.

## Historical exploratory examples

Earlier manual runs against Flask, httpx, and Pydantic motivated the contract rules, but their documented exact caller counts were produced by name-only scanning and are not retained as validated ownership evidence. See [Historical Scenarios](real-world-catches.md). They should not be combined with the synthetic corpus metrics.

## Required project checks

```bash
uv lock --check
just ci
just validate-corpus
just docs-build
just build
```

The target repository's compiler, type checker, and tests remain required. DiffGuard deliberately reports `breaking: null` where its bounded syntax analysis cannot prove compatibility.
