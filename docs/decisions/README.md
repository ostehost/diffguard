# Architecture Decision Records

This directory contains Architecture Decision Records (ADRs) for DiffGuard.

## What are ADRs?

ADRs document significant architectural decisions. Each record captures the context, the decision made, and its consequences. They're immutable once accepted — if a decision is reversed, write a new ADR that supersedes it.

## How to add one

1. Copy the template below into a new file: `NNN-short-title.md`
2. Fill in each section
3. Add it to the index below
4. Submit via PR

## Template

```markdown
# NNN. Short title

**Status:** Proposed | Accepted | Deprecated | Superseded by [NNN]

## Context

What is the issue? What forces are at play?

## Decision

What did we decide?

## Consequences

What becomes easier or harder? What are the trade-offs?
```

## Index

| ADR | Title | Status |
|-----|-------|--------|
| [001](001-cli-first.md) | CLI-first, not MCP/GUI/web | Accepted |
| [002](002-selective-trigger.md) | Selective trigger over always-on | Accepted |
| [003](003-bsl-1-1-license.md) | BSL 1.1 license | Accepted |
| [004](004-local-first.md) | Local-first, no SaaS | Accepted |
