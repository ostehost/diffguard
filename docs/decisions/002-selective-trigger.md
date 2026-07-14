# 002. Selective trigger over always-on

**Status:** Accepted

## Historical context

Early prototype testing suggested that always-on analysis (report on every change) was noisy. The
manual scenarios were exploratory, not a reproducible benchmark, and their exact caller counts are
withdrawn because name-only matching did not prove ownership.

The core problem: most code changes are body-only refactors that don't affect any callers. Reporting on them adds noise without value.

## Decision

DiffGuard only speaks when high-signal changes are detected. The trigger criteria:

- **Signature changes** — function contract changed, callers may need updating
- **Symbol removals** — dependents will break
- **Default value changes** — a sub-case of signature changes; subtle behavioral shift
- **File moves with dependents** — imports need updating

Body-only changes (same signature, different implementation) produce silence.

Implementation: `engine/findings.py::is_high_signal()` checks the current high-signal criteria. If
none match, review mode exits 0 without findings.

## Consequences

- **Lower output volume** — body-only changes do not trigger contract findings
- **No general precision claim** — only the checked-in synthetic corpus has reproducible metrics
- **Risk:** May miss some useful edge cases where body changes matter (e.g., changed semantics with same signature)
- **Mitigation:** target-project compilers, type checkers, tests, and review remain required
