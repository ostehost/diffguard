# CLAUDE.md Snippet for DiffGuard

```markdown
## Closeout contract verification

Near task completion—not after every edit—run:

    diffguard review --against origin/main --worktree --format json

- Exit 0: no findings; still inspect warnings and parse gaps.
- Exit 1: resolve each structured finding or explain it with evidence.
- Exit 2: fix/report the tool error; do not continue as if clean.
- Treat `references` as unresolved syntactic imports/calls/non-call uses, not exact callers.
- After resolution, run the repository's required tests, lint, type, docs, and build checks.
```
