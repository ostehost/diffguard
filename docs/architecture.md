# Architecture

## Pipeline

```
git diff ──→ parse ──→ extract ──→ match ──→ classify ──→ summarize ──→ JSON
             │         │           │         │            │
             │         │           │         │            └─ tiered summaries
             │         │           │         └─ added/removed/modified/moved
             │         │           └─ name-match old↔new symbols (O(n) dict)
             │         └─ tree-sitter queries → functions, classes, methods
             └─ py-tree-sitter parses old + new file versions
```

**Typical timing:** ~200ms for a 1000-line diff.

## Modules

Each module has a single responsibility. No horizontal imports between engine modules.

| Module | Input | Output | Responsibility |
|--------|-------|--------|---------------|
| `cli.py` | CLI args | exit code + JSON/text | Click CLI entry point. Commands: `review`, `summarize`, `context` (hidden alias for review), `install-hook`. Orchestrates pipeline and determines output. |
| `git.py` | ref range | changed files + old/new content | All git subprocess calls. Nothing else touches git. |
| `engine/_types.py` | — | — | Shared type aliases and dataclasses (`Symbol`, `ParseResult`, `compute_body_hash`). |
| `engine/parser.py` | source file | syntax tree | Tree-sitter parsing. No git logic, no matching. |
| `engine/matcher.py` | old symbols + new symbols | matched pairs | Name-based symbol matching. O(n) dict lookup. |
| `engine/classifier.py` | matched pairs | classified changes | Labels: added, removed, modified, moved, signature_changed. Sets `breaking` flag. |
| `engine/signatures.py` | old + new signatures | breaking change flags + category labels | Signature comparison. Detects parameter changes, return type changes, default value changes. |
| `engine/deps.py` | symbol names + git ref | external references | Dependency/caller detection. Uses `git grep` to pre-filter, then tree-sitter to confirm references in non-diff files. |
| `engine/summarizer.py` | classified changes | tiered text | Generates oneliner, short, detailed summaries. |
| `engine/pipeline.py` | ref range + content provider | `DiffGuardOutput` | Orchestrates parse → match → classify → summarize for all files. |
| `schema.py` | — | — | Pydantic models. The contract. |

## Language plugin system

The `languages/` package provides per-language tree-sitter support. Each language module (e.g., `languages/python/__init__.py`) exports:

| Function | Purpose |
|----------|---------|
| `get_language()` | Returns the `tree_sitter.Language` object |
| `extract_symbols(tree, source)` | Walks the parsed tree and returns `list[Symbol]` |

The top-level `languages/__init__.py` provides:

- `SUPPORTED_LANGUAGES` — set of supported language names
- `detect_language(filename)` — maps file extensions to language names
- `get_parser(language)` — returns a configured `tree_sitter.Parser`
- `get_language_module(language)` — dynamically imports the language module

`languages/_utils.py` contains shared helpers (e.g., `node_text()` for safe node text extraction).

### Supported languages

| Language | Module | Grammar |
|----------|--------|---------|
| Python | `languages/python/` | tree-sitter-python |
| TypeScript | `languages/typescript/` | tree-sitter-typescript |
| JavaScript | `languages/typescript/` (shared) | tree-sitter-javascript |
| Go | `languages/go/` | tree-sitter-go |

## Symbol extraction

DiffGuard uses tree-sitter to parse source files and walk the AST to extract:

- Function/method definitions with signatures
- Class/struct/interface definitions
- Line numbers and scope
- Body hashes for change detection

For each changed file, DiffGuard parses both the old and new versions, extracts symbols from each, then matches them by name.

## Matching algorithm

1. Build a dict of old symbols keyed by `(name, kind)`
2. Build a dict of new symbols keyed by `(name, kind)`
3. Symbols in both → **modified** (compare bodies/signatures)
4. Symbols only in old → **removed**
5. Symbols only in new → **added**
6. Removed symbol name appears in a different file as added → **moved**

This is O(n) and handles the common case well. It deliberately does not attempt fuzzy rename detection — accuracy over comprehensiveness.

## Selective trigger

DiffGuard's core design principle: **stay silent when there's nothing useful to say.**

The `review` command checks for high-signal changes before producing output. If none are found, it exits with code 0 (silence). The logic lives in `cli.py::_has_high_signal_changes()`:

A change is **high-signal** if any of these are true:

| Trigger | What it means |
|---------|---------------|
| Signature changed | `before_signature` and `after_signature` both present — function contract changed |
| Breaking change | `breaking=True` — callers may break |
| Symbol removed | `kind` ends with `_removed` — dependents will break |
| Symbol moved | `kind == "moved"` — imports need updating |

Body-only changes (same signature, different implementation) are **not** high-signal — they're internal refactors that don't affect callers.

Dependency references (`deps.py`) provide context about *who* is affected, but don't independently trigger output. A moved function with 12 importers is high-signal because of the move, not because of the importers.

### Signature change categories

When a signature change is detected, `signatures.py::classify_signature_change()` provides a specific category label:

| Category | Meaning |
|----------|---------|
| `PARAMETER REMOVED` | Positional or keyword-only parameter removed |
| `PARAMETER ADDED (BREAKING)` | New parameter without a default value |
| `RETURN TYPE CHANGED` | Return type annotation changed |
| `DEFAULT VALUE CHANGED` | Only difference is a changed default value on existing params |
| `BREAKING SIGNATURE CHANGE` | Other breaking change (type change, reorder, etc.) |
| `SIGNATURE CHANGED` | Non-breaking signature change |

### Change kinds in schema

The `SymbolChange.kind` field uses these values:

| Kind | Description |
|------|-------------|
| `function_added` | New function |
| `function_removed` | Function deleted |
| `function_modified` | Function body changed (signature intact) |
| `class_added` | New class |
| `class_removed` | Class deleted |
| `class_modified` | Class body changed (signature intact) |
| `signature_changed` | Function/class signature changed (check `breaking` flag) |
| `moved` | Symbol moved to a different file |

## Exit codes

### `review` command

| Code | Meaning |
|------|---------|
| 0 | No high-signal findings — silence. The agent should move on. |
| 1 | Findings present — the agent should read the output. |
| 2 | Error (invalid ref range, git failure, etc.) |

### `summarize` command

| Code | Meaning |
|------|---------|
| 0 | Success |
| 3 | No changes in diff |
| 4 | Partial — parse errors in some files |

## Dependency scanning

`deps.py::find_references()` locates callers of changed symbols in files *outside* the diff:

1. **Pre-filter with `git grep`** — textually search for symbol names across the repo (fast)
2. **Confirm with tree-sitter** — parse candidate files, walk the AST for identifier nodes matching symbol names
3. **Classify context** — each reference is labeled `"import"` or `"call"` based on parent node types

This two-stage approach avoids parsing every file in the repo while maintaining accuracy.

## Graceful degradation

- **Unsupported language:** File included in output with `unsupported_language: true`, line-level stats only.
- **Parse error:** File included with `parse_error: true`, falls back to line-level stats.
- **Binary file:** Skipped with `binary: true`.

DiffGuard never crashes on unsupported input. It always produces valid JSON.

## Stack

- **Python** — fast enough with native tree-sitter bindings
- **py-tree-sitter** — C-speed parsing, pre-built binaries for 40+ languages
- **Pydantic v2** — schema definition and validation
- **Click** — CLI framework
- **difflib** — per-function body comparison (no GumTree, no full AST diff)

### Why not these alternatives

| Alternative | Why not |
|-------------|---------|
| GumTree | O(n³), Java dependency, killed v1 |
| Rust/TypeScript core | Premature optimization. Python + native tree-sitter is fast enough. |
| difftastic | Line-oriented JSON output, not semantic. Great visual tool, wrong abstraction. |
| ast-grep | Pattern search, not a differ. Possible future add-on. |
