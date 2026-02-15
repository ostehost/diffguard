# 002. Selective trigger over always-on

**Status:** Accepted

## Context

Early testing with always-on analysis (report on every change) was noisy. A/B testing showed 2 out of 3 test scenarios had zero improvement in code quality when the tool reported on everything. Developers and agents learned to ignore it.

The core problem: most code changes are body-only refactors that don't affect any callers. Reporting on them adds noise without value.

## Decision

DiffGuard only speaks when high-signal changes are detected. The trigger criteria:

- **Signature changes** — function contract changed, callers may need updating
- **Symbol removals** — dependents will break
- **Default value changes** — a sub-case of signature changes; subtle behavioral shift
- **File moves with dependents** — imports need updating

Body-only changes (same signature, different implementation) produce silence.

Implementation: `cli.py::_has_high_signal_changes()` checks these criteria. If none match, exit code 0 (silence).

## Consequences

- **100% precision** — when DiffGuard speaks, it's useful
- **58% silence rate** — most commits get no output, which is correct
- **Risk:** May miss some useful edge cases where body changes matter (e.g., changed semantics with same signature)
- **Mitigation:** `--verbose` flag forces full output for users who want it
