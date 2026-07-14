# Architecture

## Ownership boundaries

| Surface | Responsibility |
|---|---|
| `git.py` | All Git subprocesses and committed/index/worktree snapshot reads. |
| `diff.py` | Unified-diff parsing only. |
| `languages/{python,typescript,go}` | Tree-sitter grammar and symbol extraction. |
| `engine/parser.py` | Parse source and delegate extraction. |
| `engine/matcher.py` | Pair old/new symbols and bounded cross-file move candidates. |
| `engine/signatures.py` | Compare signature strings and return bounded compatibility assessments. |
| `engine/classifier.py` | Convert matches plus an injected assessment into `SymbolChange`. |
| `engine/deps.py` | Scan snapshot files for AST-context name references; all Git access delegates to `git.py`. |
| `engine/findings.py` | Decide high-signal findings and attach references. |
| `engine/summarizer.py` | Generate summary tiers. |
| `engine/pipeline.py` | Orchestrate parse→match→assess→classify→summarize. |
| `schema.py` | Pydantic contracts for summarize and review JSON. |
| `report.py` | Render text or serialize schema models; no hand-built JSON authority. |
| `cli.py` | Select mode/snapshots, orchestrate engine calls, and enforce exits. |

Core engine ownership modules do not import each other horizontally. `pipeline.py` and `cli.py` are the orchestration seams; shared dataclasses live in `engine/_types.py`.

## Snapshot modes

- committed: existing Git ref/range behavior, including preserved bare-ref semantics;
- staged: `HEAD..:index`;
- worktree: merge base of `--against` and `HEAD` versus `:worktree`, including staged, unstaged, tracked deletions, staged additions, and untracked non-ignored additions.

Untracked additions are converted to unified diffs in `git.py` without modifying the index.

## Signature invariant

Classifier ordering is signature comparison first, body equality second. Exact or parsed structural
signature equivalence falls through to body comparison; assessed default, parameter, annotation,
and return-only edits survive even when the extracted body text is identical.

Python compatibility is bounded to call-shape syntax. Extracted TypeScript/JavaScript function,
arrow-function, class, and class-method declarations and extracted Go function/method declarations
emit syntax evidence and explicit analysis gaps. TypeScript overload/interface signatures and Go
interface methods are outside the current extractor boundary. A tri-state `breaking` field prevents
unknown compatibility from being serialized as false certainty.

## Reference invariant

The candidate filter may be textual, but emitted evidence must pass an AST-context check. Declaration/binding nodes are excluded. Calls are only identifiers on the callable side of a call expression; other names become non-call `reference`. Imports remain separate. Changed files are scanned.

No module/import/type ownership resolver exists. Every output reference is therefore `resolution: unresolved` with low ownership confidence.

## Gap invariant

If a supported changed file has a parse error on either snapshot side, the pipeline records a parse-gap warning and emits no symbol changes for that file. Missing content behaves similarly. Reference-scan parse gaps also become warnings.

## Review schema invariant

`ReviewEnvelope` is the only review JSON authority. After Click successfully parses the command,
populated, empty, partial, and tool-error paths serialize Pydantic models. Click usage errors remain
plain CLI diagnostics. Exits remain `0` no findings, `1` findings, `2` tool error.
