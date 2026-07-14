# GitHub Copilot Instructions Snippet

Add to `.github/copilot-instructions.md`:

```markdown
Before declaring a coding task complete, run `diffguard review --against origin/main --worktree --format json`. Treat exit 1 as structured findings and exit 2 as failure. Read warnings and analysis gaps, resolve or explain every finding, and never describe unresolved syntactic references as proven callers. Then run the repository's required checks.
```
