[![PyPI](https://img.shields.io/pypi/v/diffguard)](https://pypi.org/project/diffguard/)
[![License](https://img.shields.io/badge/license-BSL%201.1-blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://pypi.org/project/diffguard/)

# DiffGuard

**Catches the structural breaks that pass code review.**

## The catch

Flask changed `redirect()`'s default from 302 to 303 ([PR #5898](https://github.com/pallets/flask/pull/5898)). A reviewer sees a one-line diff. DiffGuard sees 7 callers that silently change behavior:

```
$ diffguard review eca5fd1d~1..eca5fd1d

⚠ DiffGuard: 2 changes need review

1. DEFAULT VALUE CHANGED: redirect(location, code=302, Response) → redirect(location, code=303, Response)
   File: src/flask/helpers.py:241
   Impact: 7 callers rely on the default:
     auth.py:25   `return redirect(url_for("auth.login"))`
     auth.py:77   `return redirect(url_for("auth.login"))`
     auth.py:105  `return redirect(url_for("index"))`
     auth.py:116  `return redirect(url_for("index"))`
     blog.py:81   `return redirect(url_for("blog.index"))`
   Callers: test_helpers.py (5 calls), test_regression.py (1 call), test_signals.py (1 call), test_testing.py (1 call)
   Review: Verify callers expect the new default value

2. DEFAULT VALUE CHANGED: App.redirect(self, location, code=302) → App.redirect(self, location, code=303)
   File: src/flask/sansio/app.py:935
   Impact: 7 callers rely on the default:
     auth.py:25   `return redirect(url_for("auth.login"))`
     auth.py:77   `return redirect(url_for("auth.login"))`
     auth.py:105  `return redirect(url_for("index"))`
     auth.py:116  `return redirect(url_for("index"))`
     blog.py:81   `return redirect(url_for("blog.index"))`
   Callers: test_helpers.py (5 calls), test_regression.py (1 call), test_signals.py (1 call), test_testing.py (1 call)
   Review: Verify callers expect the new default value
```

Based on real output from DiffGuard run against Flask commit `eca5fd1d`. Signature display simplified for readability — run the command yourself to see parameter type annotations.

## What DiffGuard is

DiffGuard is a **verification layer** for code changes. Not a review tool — reviews give opinions, DiffGuard gives facts. It uses tree-sitter AST analysis to detect structural changes in git diffs and traces their impact through your codebase.

**What it catches:** Function signature changes, removed/renamed symbols, default value changes — and shows you every caller affected.

**What it doesn't catch:** Logic bugs, behavioral changes beyond signatures, performance issues, security vulnerabilities.

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
4. **Scans for callers** — finds files that reference changed symbols
5. **Outputs actionable context** — or stays silent if nothing matters

## Why not X?

**vs CodeRabbit** — CodeRabbit reviews code on their servers for $15–30/seat/month. DiffGuard runs locally, is free, and does a different kind of analysis: structural verification rather than LLM-powered review. They're complementary — CodeRabbit reviews intent, DiffGuard verifies structure.

**vs GitHub Copilot / claude-code-action** — Complementary, not competitive. Run DiffGuard first as cheap, instant structural triage. Then let the expensive model focus on what actually matters.

**vs Aider repo-map** — Aider's repo-map uses tree-sitter too, but it's locked inside Aider. DiffGuard works with any agent, any workflow, any CI pipeline.

## Agent Integration

Add one line to your agent config — DiffGuard is silent when nothing matters.

**Claude Code** — Add to `CLAUDE.md` or wire as a [hook](docs/agent-integration.md#claude-code-hook). See [snippet](docs/claude-md-snippet.md).

**Cursor** — Add `.cursor/rules/diffguard.mdc`. See [snippet](docs/cursor-rule-snippet.md).

**Any agent** — One instruction: `Before reviewing diffs, run: diffguard review <base>..HEAD`

Exit codes: `0` = silent, `1` = findings (read stdout), `2` = error. Use `--format json` for structured output.

See the full [Agent Integration Guide](docs/agent-integration.md) for hooks, CI patterns, and examples.

## GitHub Action

Add DiffGuard to your CI pipeline — it reviews PRs and posts findings as comments.

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

When findings exist, DiffGuard posts a PR comment with the structural changes. When there's nothing noteworthy, it stays silent.

## Languages

- **Python** (most mature — extensive real-world validation)
- TypeScript / JavaScript
- Go
- More planned (Rust, Java, C#)

## Philosophy

1. **Silence is a feature.** No findings? No output. Most diffs don't need structural analysis.
2. **Local-first.** Your code never leaves your machine. No SaaS, no API keys, no accounts.
3. **Agent-native.** CLI + JSON output. `pip install` and go. Works with any agent or workflow.
4. **Precision over recall.** We'd rather miss a minor issue than cry wolf on every PR.

## Roadmap

- **v0.2** — GitHub Action, TypeScript/JS improvements
- **v0.3** — Persistent symbol graph (`.diffguard/` directory)
- **v0.4** — `diffguard callers`, `diffguard deps`, `diffguard impact` queries
- **Future** — 8 language support, review rules engine

## License

BSL 1.1 — see [LICENSE](LICENSE) for details.
