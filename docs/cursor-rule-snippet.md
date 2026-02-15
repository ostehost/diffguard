# Cursor Rule for DiffGuard

Add this as `.cursor/rules/diffguard.mdc` in your repo.

## One-liner

```mdc
---
type: auto
description: Run DiffGuard structural analysis on code changes
globs: ["**/*.py", "**/*.ts", "**/*.js", "**/*.go"]
---
Before reviewing diffs or PRs, run `diffguard review <base>..HEAD` and address any findings.
```

## Expanded

```mdc
---
type: auto
description: Run DiffGuard structural analysis on code changes
globs: ["**/*.py", "**/*.ts", "**/*.js", "**/*.go"]
---
## Code Review with DiffGuard

When reviewing changes or PRs, first run:

    diffguard review <base>..HEAD

- Exit 0 = nothing noteworthy (stay silent on structural issues)
- Exit 1 = findings to address (read and act on each)
- Exit 2 = error (report it)
- Use `--format json` for structured output
```
