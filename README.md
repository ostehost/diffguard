[![PyPI](https://img.shields.io/pypi/v/diffguard)](https://pypi.org/project/diffguard/)
[![License](https://img.shields.io/badge/license-BSL%201.1-blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://pypi.org/project/diffguard/)

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
  src/flask/helpers.py:241 — 7 callers rely on the default

  DEFAULT VALUE CHANGED: App.redirect(self, location, code=302) → App.redirect(self, location, code=303)
  src/flask/sansio/app.py:935 — 7 callers rely on the default
```

Tree-sitter AST analysis. No LLM. No network calls. Runs in seconds.

## What it catches

Function signature changes, removed/renamed symbols, default value changes — and shows you every caller affected.

## What it doesn't catch

Logic bugs, behavioral changes beyond signatures, performance issues, security vulnerabilities. DiffGuard detects **structural breaks**, not all bugs.

When there's nothing structural to report, it stays silent (exit code 0, no output).

## Quick Start

```bash
pip install diffguard
diffguard review main..feature
```

Exit codes: `0` = nothing noteworthy, `1` = findings, `2` = error.

## How It Works

1. **Parses the diff** using tree-sitter AST analysis (not regex)
2. **Extracts symbols** — functions, classes, signatures
3. **Detects high-signal changes** — signature changes, removed symbols, default value changes
4. **Scans for callers** — finds every file that references changed symbols
5. **Outputs actionable context** — or stays silent if nothing matters

## Agent Integration

Works with **Claude Code**, **Cursor**, **GitHub Actions**, or any agent that can run a CLI command.

Add one line to your agent config — DiffGuard is silent when nothing matters.

See the full [Agent Integration Guide](docs/agent-integration.md) for hooks, CI patterns, and snippets for [Claude Code](docs/claude-md-snippet.md) and [Cursor](docs/cursor-rule-snippet.md).

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

## Languages

- **Python** (most mature — extensive real-world validation)
- TypeScript / JavaScript
- Go
- More planned (Rust, Java, C#)

## Philosophy

1. **Silence is a feature.** No findings? No output. Most diffs don't need structural analysis.
2. **Local-first.** Your code never leaves your machine. No SaaS, no API keys, no accounts.
3. **Agent-native.** CLI + JSON output. `pip install` and go.
4. **Precision over recall.** We'd rather miss a minor issue than cry wolf on every PR.

## License

BSL 1.1 — see [LICENSE](LICENSE) for details.
