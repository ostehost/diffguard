# Cursor Rule for DiffGuard

```mdc
---
type: auto
description: Verify contract changes at task closeout
globs: ["**/*.py", "**/*.ts", "**/*.js", "**/*.go"]
---
Near task completion, run `diffguard review --against origin/main --worktree --format json`. Handle exits 0/1/2 explicitly, resolve or explain findings and warnings, and treat references as unresolved syntactic evidence. Do not run the scan after every edit.
```
