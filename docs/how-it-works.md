# How It Works

## Pipeline

```text
Git snapshot diff -> parse -> extract -> match -> compare signature/body -> classify
                  -> scan AST-context name references -> validate review envelope
```

1. `git.py` obtains a committed, index, or base-to-worktree diff and file contents.
2. Language modules extract functions, methods, classes, signatures, and body hashes.
3. The matcher pairs symbols by name/kind/parent and detects bounded cross-file moves.
4. Signature comparison runs independently of body comparison, so a pure default or annotation edit cannot disappear behind an equal body hash.
5. The classifier attaches stable rule/category IDs, factual compatibility status, confidence, evidence, and analysis gaps.
6. Reference scanning uses `git grep` as a candidate filter and tree-sitter to label imports, calls, and non-call references. Declarations are excluded; changed files are included.
7. `schema.py` validates populated, empty, partial, and error review envelopes.

## Compatibility policy

| Language | What DiffGuard claims |
|---|---|
| Python | Bounded call-shape facts for parameter addition/removal/reorder and defaults. Annotation changes are syntax with unknown semantic compatibility. |
| TypeScript/JavaScript | Parameter/return syntax changes for extracted function, arrow-function, and class-method declarations, plus class declarations. Overload declarations and interface members are not extracted. Compatibility remains unknown without type and compiler resolution. |
| Go | Parameter/return syntax changes for extracted function and method declarations. Interface methods are not extracted. Compatibility remains unknown without compiler, interface, method-set, and call resolution. |

`breaking: true` means a bounded rule proves an incompatible Python call shape. `false` means that bounded rule found no call-shape break; it does not mean behavior is unchanged. `null` means compatibility was not proven. Removed declarations are findings, but their public/export impact remains unknown.

## Reference policy

A matching AST name can be classified syntactically, but same-named symbols in different modules cannot be assigned exact ownership without resolution. Every emitted reference therefore includes:

- `kind`: `import`, `call`, or `reference`;
- `confidence`: currently `low` for ownership;
- `resolution`: `unresolved`;
- evidence explaining the AST name match.

Import evidence remains useful for moved symbols, but it is not presented as an exact dependent.

## Selective output and gaps

Signature changes, removed symbols, and possible cross-file move candidates trigger findings. Body-only changes and additions remain silent. Dependency references add evidence but do not trigger findings on their own.

If either side of a changed supported-language file has a tree-sitter parse error, DiffGuard reports a parse-gap warning and suppresses symbol findings for that file. Unavailable content is handled the same way. This trades recall for truthful evidence.

## Validation boundary

`just validate-corpus` reports the current local synthetic sample, misses, false positives, parse
gaps, and per-rule precision/recall. It does not establish universal accuracy or replace the target
project's compiler and tests.
