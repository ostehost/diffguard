# Quick Start

## Install

```bash
pip install diffguard
```

## `diffguard review` — the primary command

Surfaces high-signal structural changes. Silent when nothing is noteworthy.

```bash
# Review last commit
diffguard review HEAD~1..HEAD

# Review a PR branch
diffguard review main..feature-branch
```

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | No high-signal findings (no output) |
| 1 | Findings present — read stdout |
| 2 | Error |

### Example output (text)

```
⚠ DiffGuard: 2 changes need review

1. DEFAULT VALUE CHANGED: redirect(location, code=302, Response) → redirect(location, code=303, Response)
   File: src/flask/helpers.py:241
   Impact: 7 callers rely on the default:
     auth.py:25   `return redirect(url_for("auth.login"))`
     auth.py:77   `return redirect(url_for("auth.login"))`
     auth.py:105  `return redirect(url_for("index"))`
     blog.py:81   `return redirect(url_for("blog.index"))`
   Review: Verify callers expect the new default value

2. DEFAULT VALUE CHANGED: App.redirect(self, location, code=302) → App.redirect(self, location, code=303)
   File: src/flask/sansio/app.py:935
   Impact: 7 callers rely on the default
   Review: Verify callers expect the new default value
```

*Real output from DiffGuard run against Flask commit `eca5fd1d`.*

### Example output (JSON)

```bash
diffguard review HEAD~1..HEAD --format json
```

When there are no findings:

```json
{
  "version": "0.1.0",
  "ref_range": "HEAD~1..HEAD",
  "findings": [],
  "stats": {
    "files_analyzed": 1,
    "symbols_changed": 0,
    "silence_reason": "no high-signal changes"
  }
}
```

See [Schema Reference](schema.md#review-output) for the full schema.

---

## `diffguard summarize` — full structural summary

Always produces output. Gives a complete map of what changed structurally — useful for agents that want the full picture, not just the high-signal items.

```bash
# Summarize last commit
diffguard summarize HEAD~1..HEAD

# Choose output tier
diffguard summarize HEAD~1..HEAD --format oneliner
diffguard summarize HEAD~1..HEAD --format short
diffguard summarize HEAD~1..HEAD --format json
```

### Example output (JSON)

```json
{
  "schema_version": "1.1",
  "meta": {
    "ref_range": "abc1234..def5678",
    "stats": { "files": 3, "additions": 340, "deletions": 89 },
    "warnings": [],
    "timing_ms": 187.4
  },
  "files": [
    {
      "path": "src/auth/client.ts",
      "language": "typescript",
      "change_type": "modified",
      "changes": [
        {
          "kind": "function_removed",
          "name": "authenticate",
          "signature": "authenticate(apiKey: string): Promise<Session>",
          "line": 45,
          "breaking": true
        }
      ]
    }
  ],
  "summary": {
    "change_types": { "feature": 1, "refactor": 2 },
    "breaking_changes": [...],
    "focus": ["authenticate() removed — callers need migration"]
  },
  "tiered": {
    "oneliner": "Replace API key auth with OAuth2 PKCE; 2 breaking changes",
    "short": "Removes authenticate(apiKey), adds authenticateOAuth(config)...",
    "detailed": "..."
  }
}
```

!!! note "Illustrative example"
    The JSON above is illustrative of the schema structure. Field names and types match the real schema — see [Schema Reference](schema.md#summarize-output) for details.

See [Schema Reference](schema.md) for the full output format.

---

## When to use which

| Scenario | Command |
|----------|---------|
| CI gate / pre-review check | `diffguard review` |
| Agent needs full structural map | `diffguard summarize` |
| Quick "anything breaking?" check | `diffguard review` |
| Feeding context to an AI reviewer | `diffguard summarize --format json` |

## What DiffGuard tells you

DiffGuard reports **structural facts**: which functions changed, what signatures broke, what was removed, which callers are affected.

It does **not** tell you *why* something changed, whether the logic is correct, or whether it's a good idea. That's the reviewer's job.

**Scope:** Signatures, removed symbols, default value changes. Not logic, security, or performance.
