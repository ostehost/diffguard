# CLAUDE.md Snippet for DiffGuard

Add this to your repo's `CLAUDE.md` to enable DiffGuard in Claude Code.

## One-liner (recommended)

```markdown
Before reviewing any PR or diff, run: `diffguard review <base>..HEAD`
```

## Expanded config

```markdown
## Code Review

Before reviewing any PR or diff, run DiffGuard to identify high-signal changes:

    diffguard review <base>..HEAD

- Exit code 0 → nothing noteworthy, skip review commentary on structural changes
- Exit code 1 → findings present, read the output and address each item
- Use `--format json` for structured output if you need to process findings programmatically
```

## Example output

```
⚠ DiffGuard: 2 changes need review

1. SIGNATURE CHANGED: process_payment(amount, currency) → process_payment(amount, currency, *, timeout=30)
   File: src/billing/processor.py:42
   Impact: Backward-compatible (new kwarg has default)
   Callers: checkout.py (3 calls), subscription.py (1 call)

2. SYMBOL REMOVED: `validate_coupon`
   File: src/billing/discounts.py:15
   Impact: 2 callers will break:
     checkout.py:89  `validate_coupon(code)`
     api.py:201  `validate_coupon(user_code)`
   Review: Ensure no remaining callers depend on this symbol
```
