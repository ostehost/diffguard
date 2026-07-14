# Historical Contract-Change Scenarios

These historical open-source changes motivated DiffGuard's rules. They are useful examples, not the reproducible validation corpus and not proof of exact dependency ownership.

## Flask default change

[Flask PR #5898](https://github.com/pallets/flask/pull/5898) changed `redirect(..., code=302, ...)` to `code=303`. DiffGuard's current Python rule reports `DG105 / default_changed`, `breaking: false`, plus a gap explaining that omitted-argument behavior can change even though the call shape remains valid.

Earlier project notes claimed exact affected caller counts. Those counts came from name-only reference matching and are withdrawn. Current output reports unresolved syntactic import/call/reference evidence instead.

## httpx annotation narrowing

[httpx PR #3378](https://github.com/encode/httpx/pull/3378) narrowed a Python annotation from `str | bytes` to `str`. Current DiffGuard reports `DG107 / parameter_annotation_changed` with `breaking: null`: the syntax changed, but runtime and type-checker compatibility are not proven by an annotation diff alone.

## Pydantic symbol removal

[Pydantic PR #5331](https://github.com/pydantic/pydantic/pull/5331) removed/renamed serializer declarations. Current DiffGuard reports removed declarations (`DG201`) and retains import/call/reference name evidence, all with ownership unresolved. It does not claim those matches necessarily target a specific overload or module.

## Recovered false negative

An earlier manual Django scenario changed `UniqueConstraint(name=None)` to `UniqueConstraint(name)` and produced no finding. The body-hash ordering bug and default-removal classification caused that miss. `DG104 / default_removed` is now covered by parser→classifier and end-to-end regressions plus the local labeled corpus.

## What is actually measured

The canonical network-free corpus is described in [Validation](validation.md). Re-running historical upstream repositories would require network state and dependency pinning, so it is deferred rather than represented as current reproducible proof.
