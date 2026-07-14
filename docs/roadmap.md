# Roadmap

*Updated for the `0.2.0` recovery release.*

## Position

DiffGuard is a deterministic local contract-change verifier for coding agents. It is not a general
reviewer and does not claim compiler-grade dependency resolution. The bounded recovery does not by
itself establish public adoption; that remains an experiment to measure after `0.2.0`.

## Recovery release (`0.2.0`)

The bounded release gate is:

- detect pure signature edits independently of bodies;
- classify Python default removal consistently;
- emit honest AST-context references with unresolved ownership;
- compare a selected base merge-base with the full worktree;
- validate populated, empty, partial, and tool-error review JSON through `schema.py` after Click
  successfully parses the command;
- provide closeout-oriented agent guidance;
- make the composite Action install from its selected checkout;
- publish a small reproducible corpus and qualify historical claims.

Release operations require explicit owner authorization after final review. The presence of a
release agent does not grant commit, tag, push, or publish authority.

## Deferred until evidence warrants it

- compiler/type-checker integrations or sound module ownership resolution;
- broader real-world labeled corpora;
- grouping/deduplication for large refactors;
- additional languages;
- configurable policy/severity.

## Explicit non-goals

- no MCP server, daemon/watch mode, web service, GUI, or general AI reviewer;
- no invented risk score;
- no claim of universal precision, every caller, or unique market position;
- no product retirement, rename, relicense, archive, or yank decision in the bounded `0.2.0` recovery.

## Continue/stop evidence

Following publication of `0.2.0`, measure installs, repeat use, issue quality, and external
contributions before expanding scope. A larger compiler-grade resolver or new language should
require concrete user demand plus a labeled corpus that can prove its invariants.
