# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] - 2026-07-14

### Fixed
- Detect pure signature changes independently of body hashes, including default removal.
- Classify dependency evidence by AST context and stop claiming unresolved name matches are exact callers.
- Make the composite Action install code from its selected Action checkout.
- Preserve Python and TypeScript callable modifiers, compound rename/default breaks, and duplicate-symbol removals during move reconciliation.
- Reject missing Action comparison context, bound declared outputs, and reconcile only Action-owned paginated PR comments.
- Preserve generic/type-parameter syntax across Python, TypeScript, and Go, including host-independent Python PEP 695 comparison on Python 3.11, nested TypeScript/Go callable constraints, and TypeScript optional and `override` methods, without miscounting generic type commas or constraint parentheses as parameters.
- Exclude declarations, Python destructuring bindings, and syntax-only labels from reference evidence, retain changed return annotations in text findings, and decode mixed Unicode/escaped Git paths correctly.
- Refuse worktree reads through symlinked path components so reference evidence cannot escape the repository.
- Fail closed on option-like Action ref ranges, reject custom ranges for comment-enabled pull requests so comment identity matches the reviewed event range, order untrusted log output on one protected stream, pass comment payloads by file, reject stale PR runs, scope non-overlapping PR comment identity to the analyzed base/head pair with post-write stale rollback, visibly label historical-state comments, and use immutable Node 24 Action dependencies.
- Preserve Action PR review state when exit `0` includes warnings, parse gaps, or invalid structured output, and expose incomplete analysis separately from the CLI exit code.
- Fence Action comment mutation to an anchored base/head identity, recheck freshness after writes, and queue comment-enabled PR jobs so stale clean runs cannot erase newer findings.
- Normalize nested-directory invocations to the repository root and make no-argument `summarize` analyze staged, unstaged, and untracked changes against `HEAD`.
- Treat formatting-only Python declarations as structurally equivalent while still reporting an accompanying body change.
- Treat formatting-only TypeScript, JavaScript, and Go signatures as lexically equivalent while preserving literal, template, regex, and operator changes.
- Prioritize removed defaults on retained Python parameters when the same edit also adds or replaces parameters.
- Account for positional rebinding and `*args`/`**kwargs` capture before classifying permissive Python parameter-kind transitions as nonbreaking.
- Treat defaulted positional additions ahead of `*args` as breaking call-shape changes instead of optional compatible additions.
- Coalesce unambiguous same-path delete/add worktree records globally so recreated files cannot fabricate symbol removals.
- Preserve both source and destination paths as structured JSON evidence for cross-file move findings.
- Match cross-file moves through mutually unique signature-and-body evidence tiers so duplicate symbols cannot fabricate a source path or breaking signature change.
- Classify TypeScript/JavaScript class heritage expressions before callable parameter logic so mixin-call arguments cannot become fabricated parameter changes.
- Keep commas inside TypeScript instantiation-expression defaults within the containing parameter.
- Keep TypeScript function-type arrows from prematurely closing an enclosing generic parameter group.
- Exclude Python lambda parameters and structural-pattern captures from dependency evidence while retaining lambda-body, class-pattern, and qualified value-pattern references.
- Retain evaluated Python dictionary and explicit Go composite keys, distinguish definitive zero-match grep results from unavailable discovery, and scan dependency evidence only for surfaced findings.
- Enforce labeled corpus parse gaps bidirectionally so both unexpected and unexpectedly resolved gaps fail validation.
- Create closeout review artifacts at private per-run temporary paths with explicit retention and cleanup ownership instead of sharing a fixed filename.
- Preserve colon-, marker-, and newline-containing paths when attributing structured analysis warnings.
- Emit schema-valid summarize JSON for empty diffs while retaining the documented no-changes exit
  code.
- Fetch complete default-branch ancestry for new-branch CI self-reviews and reject schema-valid
  results whose warnings or parse-error statistics show incomplete analysis.
- Render control, bidi, zero-width, and line-separator code points as visible escapes in Action PR
  comments without modifying the raw structured output files.
- Serialize review and summarize JSON with ASCII Unicode escapes for terminal and CI-log safety;
  JSON parsing preserves the original Unicode string values, so the schema versions are unchanged.
- Install the composite Action into a per-run temporary virtual environment and remove it during
  closeout so persistent self-hosted runners cannot be contaminated by or race concurrent runs.

### Added
- Base-to-worktree review mode covering staged, unstaged, added, and deleted files.
- Pydantic review schema (introduced at `1.0.0`, now `1.1.0`) with stable IDs, confidence, evidence, gaps, references, warnings, statistics, and JSON tool errors.
- Repository closeout skill and current AGENTS.md, CLAUDE.md, and GitHub Copilot snippets.
- A network-free labeled regression corpus with per-rule metrics.

### Changed
- GitHub workflows deny token permissions by default, scope required grants per job, avoid
  persisting checkout credentials, and require release tags to be reachable from the repository's
  default branch (`origin/main` for this project).
- CI and release validation retain Python 3.11 as the minimum and cover Python 3.14 as the current
  upper feature-series target.
- The composite Action installs its selected checkout against the exact cross-platform runtime
  dependency versions exported from `uv.lock`, and its complete PEP 517 backend closure is pinned
  separately so build isolation cannot resolve drifting transitive versions.
- Project workflows and the composite Action use `actions/setup-python` v6.3.0 on Node 24.
  Self-hosted Action
  consumers require GitHub Actions Runner v2.327.1 or newer; GitHub-hosted runners are managed by
  GitHub.
- Review JSON migrates from published `0.1.0` (and the unreleased `0.2.0` intermediate)
  to `1.0.0`, then additively to `1.1.0`; caller-impact fields are replaced by unresolved
  syntactic references and move findings gain optional `source_file` evidence. See
  `docs/schema.md`.
- Summarize JSON migrates from published `schema_version: "1.1"` to `"2.0"` because
  `SymbolChange.breaking` is now tri-state (`true`, `false`, or `null`); consumers that assumed a
  boolean must accept `null` for compatibility that DiffGuard cannot prove.
- Review and summarize JSON render invalid UTF-8 path bytes as display-only `\xNN` text while
  internal Git scanning retains the exact bytes. This text may collide with a literal filename
  containing the same backslash sequence and must not be used as an operational path locator.
- Package version advances to `0.2.0`.

## [0.1.2] - 2026-02-15

### Fixed
- Updated package description to match current positioning
- Changed license field format for hatchling compatibility

### Added
- GitHub Action for CI integration

## [0.1.1] - 2026-02-11

### Fixed
- Fixed fabricated README example

### Added
- Added content verification to release process

## [0.1.0] - 2026-02-11

### Added
- Initial PyPI release
- `review` and `summarize` commands
- Python, TypeScript, and Go support via tree-sitter
