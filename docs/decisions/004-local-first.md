# 004. Local-first, no SaaS

**Status:** Accepted

## Context

The primary competitor (CodeRabbit) requires sending code to their servers for analysis. For many teams — especially in regulated industries, defense, finance, healthcare — this is a non-starter. Privacy and compliance are the competitive wedge.

## Decision

All processing runs locally. No telemetry, no cloud calls, no accounts, no sign-up.

Code never leaves the developer's machine.

## Consequences

- **Zero setup friction** — `pip install diffguard && diffguard review` works immediately
- **Privacy by architecture** — not just a policy, structurally impossible to leak code
- **Compliance-friendly** — no data processing agreements needed
- **Trade-off:** No usage analytics — harder to understand adoption patterns
- **Trade-off:** Harder to monetize — no SaaS revenue model, must find alternative (support, enterprise features, etc.)
