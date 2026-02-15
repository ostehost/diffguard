# Validation

DiffGuard has two commands, validated in different ways.

---

## `diffguard review` — real-world catch validation

The `review` command's detection capabilities are validated against real commits in real open-source projects. See [Real-World Catches](real-world-catches.md) for full details.

**Summary of results:**

- **Flask:** Caught `redirect()` default change from 302→303, identified 7 affected callers
- **httpx:** Caught `Request(method=)` type narrowing from `str | bytes` to `str`
- **Pydantic:** Caught `@serializer` removal and 19 affected callers

**Known false negative:** Django `UniqueConstraint(name=None)` → `UniqueConstraint(name)` — a parameter changed from optional to required. DiffGuard returned exit 0. Filed as a potential improvement.

**Precision:** Zero false positives in validation testing. DiffGuard prioritizes precision over recall — it would rather miss a minor issue than report a false positive.

---

## `diffguard summarize` — A/B test validation

We ran a controlled test to measure whether `summarize` output helps AI reviewers find more issues.

!!! note "Feature context"
    This A/B test validates the `summarize` command's structural output as a review pre-pass. It does not test the `review` command's selective detection.

### Setup

- **Commit:** 7ae6492 — 944 lines changed across 3 files
- **Baseline:** AI reviewer with raw diff only (974 lines of diff text)
- **Assisted:** AI reviewer with DiffGuard `summarize` JSON output + the same raw diff

### Results

| Metric | Baseline | Assisted |
|--------|----------|----------|
| Total findings | 19 | **26 (+37%)** |
| Design-level issues | 4 | **6** |
| Specific missing test coverage | 1 (generic) | **6 (named functions)** |
| Bugs found | 5 | 5 |

### What the assisted reviewer caught that the baseline missed

1. **`_is_high_impact` dual-set case matching bug** — a real logic error where `"If_Statement"` silently fails
2. **`_get_ref_content` can't distinguish "file missing" from "git error"** — error handling gap
3. **`reshape_engine_output` mutates input dict** — callers don't expect this
4. **Specific untested functions** — named 6 functions with no test coverage

### What the baseline caught that the assisted missed

1. Type annotation `str` with default `None` (should be `str | None`)
2. `ref~1` doesn't exist for initial commit
3. `_risk_level_value` returns -1 for unknown

The baseline's unique findings were surface-level. The assisted reviewer's unique findings were structural.

---

## Why it works

The `summarize` output gives the reviewer a **map** of all changed symbols before it reads the diff. Instead of scanning 974 lines linearly, it knows the shape of the change and allocates attention to the important parts.

The `review` command goes further — it filters to only the high-signal changes and traces caller impact, so the reviewer can focus on what actually matters.

## Honest assessment

DiffGuard's value scales with PR size:

| PR size | Value added |
|---------|------------|
| Small (<100 lines, 1-2 files) | **Minimal.** The reviewer can read the whole diff easily. |
| Medium (200-500 lines) | **Moderate.** Structural overview saves the reviewer from getting lost. |
| Large (500+ lines, multiple files) | **Significant.** Linear reading of 1000+ lines of diff misses structural patterns. |

DiffGuard is not magic. On small, focused PRs, you don't need it. On large, multi-file changes — the kind where reviewers most often miss design issues — it earns its keep.

## Philosophy: facts, not opinions

DiffGuard reports structural facts: what functions changed, what signatures broke, what moved. It does **not** assess risk, rate severity, or guess intent.

A tool that says "this change is high risk" and is wrong erodes trust fast. A tool that says "`redirect()` default changed from 302 to 303, 7 callers affected" is always right — and the reviewer draws their own conclusions.
