# Agent Integration Guide

DiffGuard works with any AI agent that can run shell commands.

## Quick setup

```bash
pip install diffguard
```

Add one instruction to your agent's system prompt or config:

```
Before reviewing any diff, run: diffguard review <base>..HEAD
```

That's it. DiffGuard is silent (exit 0) when nothing is noteworthy, so it won't add noise.

## Two commands for agents

### `diffguard review` — selective, high-signal (primary)

```bash
diffguard review main..HEAD --format json
```

Returns only high-signal findings: signature changes, removed symbols, default value changes. Silent when nothing matters. Best for CI gates and "should I look closer?" decisions.

### `diffguard summarize` — full structural map

```bash
diffguard summarize main..HEAD --format json
```

Returns a complete structural summary of the diff (~200-300 tokens). Always produces output. Best when the agent needs a full map before reading the diff.

## Exit codes (review command)

| Code | Meaning | Agent action |
|------|---------|--------------|
| 0 | No high-signal findings | Continue normally |
| 1 | Findings present | Read stdout, address each finding |
| 2 | Error (not a repo, bad ref) | Report the error |

## Example output

### Review (text)

```
⚠ DiffGuard: 2 changes need review

1. DEFAULT VALUE CHANGED: redirect(location, code=302, Response) → redirect(location, code=303, Response)
   File: src/flask/helpers.py:241
   Impact: 7 callers rely on the default:
     auth.py:25   `return redirect(url_for("auth.login"))`
     ...
   Review: Verify callers expect the new default value
```

*Real output from Flask commit `eca5fd1d`.*

### Review (JSON)

```json
{
  "version": "0.1.0",
  "ref_range": "main..HEAD",
  "findings": [
    {
      "category": "SIGNATURE_CHANGED",
      "symbol": "authenticate",
      "file": "src/auth/users.py",
      "line": 34,
      "before_signature": "def authenticate(name, email)",
      "after_signature": "def authenticate(name, email, role=\"viewer\")",
      "impact": {
        "production_callers": 3,
        "test_callers": 2,
        "callers": [...]
      },
      "review_hint": "Check all callers handle the new signature"
    }
  ],
  "stats": {
    "files_analyzed": 5,
    "symbols_changed": 8,
    "silence_reason": null
  }
}
```

!!! note "Illustrative example"
    The JSON above shows the schema structure with realistic field values. See [Schema Reference](schema.md#review-output) for the full specification.

## Claude Code

Add the snippet to your repo's `CLAUDE.md` — see [claude-md-snippet.md](claude-md-snippet.md).

### Claude Code Hook

Wire DiffGuard as a hook that runs automatically after edits:

`.claude/settings.json`:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "command": "diffguard review HEAD~1..HEAD --format text 2>/dev/null || true"
      }
    ]
  }
}
```

Or as a one-shot check when a task completes:

```json
{
  "hooks": {
    "TaskCompleted": [
      {
        "command": "diffguard review $(git merge-base main HEAD)..HEAD"
      }
    ]
  }
}
```

## Cursor

Add a rule file at `.cursor/rules/diffguard.mdc` — see [cursor-rule-snippet.md](cursor-rule-snippet.md).

## Integration patterns

### CI/CD pre-review

```bash
# In your CI pipeline, before AI review
FINDINGS=$(diffguard review $BASE_SHA..HEAD --format json)
if [ $? -eq 1 ]; then
  echo "$FINDINGS" | your-agent-review-command
fi
```

### Git hook

```bash
# .git/hooks/post-commit
diffguard review HEAD~1..HEAD
```

## Scope

DiffGuard catches **structural breaks**: signature changes, removed symbols, default value changes. It does **not** catch logic bugs, security issues, or performance problems. See [What DiffGuard is](index.md#what-diffguard-is).

## Supported languages

- **Python** — most mature, extensive real-world validation
- TypeScript / JavaScript
- Go
- More planned (Rust, Java, C#)
