# How It Works

## The approach: selective trigger

Most diffs don't contain structural breaks. New functions, body-only changes, formatting — none of these affect callers. DiffGuard's core design: **stay silent unless the change is structurally significant.**

This means DiffGuard only reports when it finds:

| Trigger | What it means |
|---------|---------------|
| **Signature changed** | Function contract changed — callers may pass wrong arguments |
| **Default value changed** | Callers relying on the default get different behavior silently |
| **Symbol removed** | Dependents will break |
| **Symbol moved** | Imports need updating |

Body-only changes (same signature, different implementation) are internal refactors. They don't affect callers. DiffGuard ignores them.

## The pipeline

```
git diff → parse → extract → match → classify → scan callers → output (or silence)
```

1. **Parse the diff** — tree-sitter builds ASTs for before/after versions of each changed file. Not regex — full syntax trees.
2. **Extract symbols** — functions, classes, methods with full signatures, line numbers, and scope.
3. **Match old ↔ new** — O(n) dict-based name matching. No fuzzy rename detection (accuracy over comprehensiveness).
4. **Classify changes** — labels each symbol: added, removed, modified, moved, signature_changed. Sets `breaking` flag where applicable.
5. **Scan for callers** — two-stage: `git grep` pre-filters for speed, then tree-sitter confirms references in non-diff files.
6. **Apply selective trigger** — only produce output if high-signal changes exist AND have external callers.

Typical timing: ~200ms for a 1000-line diff.

## Why tree-sitter

Tree-sitter provides C-speed parsing with pre-built binaries for 40+ languages. It gives DiffGuard real syntax trees instead of regex-based guesses. Adding a new language is mechanical: grammar + query patterns.

Currently supported: Python (most mature), TypeScript/JavaScript, Go. More planned.

## Precision over recall

We tested three iterations before landing on the current design.

Early versions tried to report on every structural change in a diff. A/B testing against 12 real commits from Flask, FastAPI, Pydantic, and httpx showed that most PRs don't benefit from structural analysis — the reviewer can read the diff fine on their own.

The selective trigger changed the results:

| Metric | Result |
|--------|--------|
| **Precision** | 100% — when it spoke, it was right |
| **Silence rate** | 58% — stayed quiet on 7 of 12 PRs |
| **False positives** | 0 |

The key insight: making silence the default turned a marginally useful tool into a precision instrument. A tool that says "`redirect()` default changed from 302 to 303, 7 callers affected" is always right. A tool that comments on every PR trains you to ignore it.

## What agents get

Without DiffGuard, an AI reviewing a PR sees:
```
-def redirect(location, code=302, Response=None):
+def redirect(location, code=303, Response=None):
```

With DiffGuard:
```
DEFAULT VALUE CHANGED: redirect(location, code=302) → redirect(location, code=303)
Impact: 5 callers rely on the default:
  auth.py:25  `return redirect(url_for("auth.login"))`
  auth.py:77  `return redirect(url_for("index"))`
  blog.py:81  `return redirect(url_for("blog.index"))`
Review: Verify callers expect HTTP 303 instead of 302
```

The difference: "I see a number changed" vs. "I see a behavioral change that affects 5 call sites across 3 files."

## Limitations

DiffGuard's value scales with PR size:

| PR size | Value |
|---------|-------|
| Small (<100 lines, 1-2 files) | **Minimal.** The reviewer can read the whole diff. |
| Medium (200-500 lines) | **Moderate.** Structural overview saves time. |
| Large (500+ lines, multiple files) | **Significant.** Linear reading of 1000+ lines misses structural patterns. |

DiffGuard is not magic. On small, focused PRs, you don't need it.

For detailed internals, see [Architecture](architecture.md).
