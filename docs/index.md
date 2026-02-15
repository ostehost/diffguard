# DiffGuard

**Catches the structural breaks that pass code review.**

## A real bug, in one line

This diff shipped in Flask ([PR #5898](https://github.com/pallets/flask/pull/5898)):

```diff
-def redirect(location, code=302, ...):
+def redirect(location, code=303, ...):
```

One line. Looks fine. A reviewer approves it.

**The real impact:** 7 endpoints silently change HTTP behavior. POST-to-POST redirects become POST-to-GET. No errors. No warnings. Just broken APIs in production.

**DiffGuard catches it:**

```
$ diffguard review eca5fd1d~1..eca5fd1d

⚠ DiffGuard: 2 changes need review

  DEFAULT VALUE CHANGED: redirect(location, code=302) → redirect(location, code=303)
  src/flask/helpers.py:241
  Impact: 7 callers rely on the default:
    auth.py:25   return redirect(url_for("auth.login"))
    auth.py:77   return redirect(url_for("auth.login"))
    auth.py:105  return redirect(url_for("index"))
    auth.py:116  return redirect(url_for("index"))
    blog.py:81   return redirect(url_for("blog.index"))
    + test_helpers.py (5 calls), test_regression.py (1), test_signals.py (1), test_testing.py (1)

  DEFAULT VALUE CHANGED: App.redirect(self, location, code=302) → App.redirect(self, location, code=303)
  src/flask/sansio/app.py:935 — 7 callers rely on the default
```

Real output from DiffGuard against Flask commit `eca5fd1d`. Tree-sitter AST analysis — no LLM, no network calls, runs in seconds.

See more examples in [Real-World Catches](real-world-catches.md).

## What it catches

Function signature changes, removed/renamed symbols, default value changes — and shows you every caller affected.

## What it doesn't catch

Logic bugs, behavioral changes beyond signatures, performance issues, security vulnerabilities. DiffGuard detects a specific class of **structural breaks**, not all bugs.

When there's nothing structural to report, it stays silent (exit code 0, no output).

## Get started

```bash
pip install diffguard
diffguard review main..feature
```

Exit codes: `0` = nothing noteworthy, `1` = findings, `2` = error.

See the [Quickstart Guide](quickstart.md) for configuration, JSON output, and CI setup.

## How it works

1. **Parses the diff** using tree-sitter AST analysis (not regex)
2. **Extracts symbols** — functions, classes, signatures
3. **Detects high-signal changes** — signature changes, removed symbols, default value changes
4. **Scans for callers** — finds every file that references changed symbols
5. **Outputs actionable context** — or stays silent if nothing matters

See [How It Works](how-it-works.md) for the full technical approach.

## Why not X?

| | DiffGuard | CodeRabbit | Copilot / claude-code-action | Aider repo-map |
|---|---|---|---|---|
| **Setup** | `pip install` (30 seconds) | Account + GitHub app + config | GitHub app + config | Locked inside Aider |
| **Cost** | Free | $15–30/seat/month | Included with Copilot | Free (Aider-only) |
| **Privacy** | Code never leaves your machine | Code on their servers | Code on their servers | Local |
| **Works with any agent** | Yes — CLI + JSON | GitHub PR comments only | GitHub only | Aider only |
| **Output** | Silent when nothing matters | Comments on every PR | Comments on every PR | N/A |
| **Analysis** | Structural verification (AST) | LLM-powered review | LLM-powered review | Tree-sitter map |

These tools are **complementary**. Run DiffGuard first as cheap, instant structural triage. Then let the expensive model focus on what actually matters.

## Agent integration

Add one line to your agent config — DiffGuard is silent when nothing matters.

- **Claude Code** — Add to `CLAUDE.md` or wire as a [hook](agent-integration.md#claude-code-hook). See [snippet](claude-md-snippet.md).
- **Cursor** — Add `.cursor/rules/diffguard.mdc`. See [snippet](cursor-rule-snippet.md).
- **Any agent** — One instruction: `Before reviewing diffs, run: diffguard review <base>..HEAD`

See the full [Agent Integration Guide](agent-integration.md) for hooks, CI patterns, and examples.

## GitHub Action

```yaml
# .github/workflows/diffguard.yml
name: DiffGuard PR Review
on:
  pull_request:
    types: [opened, synchronize, reopened]
permissions:
  contents: read
  pull-requests: write
jobs:
  diffguard:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: ostehost/diffguard@main
```

When findings exist, DiffGuard posts a PR comment. When there's nothing noteworthy, it stays silent.

## Languages

- **Python** — most mature, extensive real-world validation
- TypeScript / JavaScript
- Go
- More planned (Rust, Java, C#)

## Philosophy

1. **Silence is a feature.** No findings? No output. Most diffs don't need structural analysis.
2. **Local-first.** Your code never leaves your machine. No SaaS, no API keys, no accounts.
3. **Agent-native.** CLI + JSON output. `pip install` and go. Works with any agent or workflow.
4. **Precision over recall.** We'd rather miss a minor issue than cry wolf on every PR.
