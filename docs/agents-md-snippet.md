# AGENTS.md Snippet for DiffGuard

```markdown
## Closeout contract verification

Near task completion, run `diffguard review --against origin/main --worktree --format json` once. Exit 1 means findings, not tool failure: resolve each finding or explain it with evidence. Exit 2 is an error. Inspect warnings/parse gaps, then run the repository's required checks. Treat references as unresolved syntactic evidence, not exact callers.
```
