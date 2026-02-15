# Schema Reference

DiffGuard has two commands with different JSON output schemas.

---

## Review output

`diffguard review <ref-range> --format json`

The review command outputs a flat list of high-signal findings. When there are no findings, `findings` is empty and `silence_reason` explains why.

### Top-level

| Field | Type | Description |
|-------|------|-------------|
| `version` | `str` | Schema version (currently `"0.1.0"`) |
| `ref_range` | `str` | Git ref range analyzed |
| `findings` | `list[Finding]` | High-signal findings (may be empty) |
| `stats` | `ReviewStats` | Analysis statistics |

### `ReviewStats`

| Field | Type | Description |
|-------|------|-------------|
| `files_analyzed` | `int` | Number of files analyzed |
| `symbols_changed` | `int` | Total symbol-level changes detected |
| `silence_reason` | `str | null` | Why no findings were reported (null if findings exist) |

### `Finding`

| Field | Type | Description |
|-------|------|-------------|
| `category` | `str` | One of: `DEFAULT_VALUE_CHANGED`, `SIGNATURE_CHANGED`, `SYMBOL_REMOVED`, `PARAMETER_ADDED`, `PARAMETER_REMOVED`, `MOVED` |
| `symbol` | `str` | Symbol name |
| `file` | `str` | File path |
| `line` | `int | null` | Line number |
| `before_signature` | `str` | Previous signature (when applicable) |
| `after_signature` | `str` | New signature (when applicable) |
| `impact` | `Impact` | Caller impact analysis |
| `review_hint` | `str` | Suggested reviewer action |

### `Impact`

| Field | Type | Description |
|-------|------|-------------|
| `production_callers` | `int` | Number of non-test callers |
| `test_callers` | `int` | Number of test callers |
| `callers` | `list[Caller]` | Up to 10 caller locations |

### `Caller`

| Field | Type | Description |
|-------|------|-------------|
| `file` | `str` | File path |
| `line` | `int` | Line number |
| `source` | `str` | Source line text |

### Example

```json
{
  "version": "0.1.0",
  "ref_range": "eca5fd1d~1..eca5fd1d",
  "findings": [
    {
      "category": "DEFAULT_VALUE_CHANGED",
      "symbol": "redirect",
      "file": "src/flask/helpers.py",
      "line": 241,
      "before_signature": "def redirect(location, code=302, Response=None)",
      "after_signature": "def redirect(location, code=303, Response=None)",
      "impact": {
        "production_callers": 7,
        "test_callers": 2,
        "callers": [
          {"file": "auth.py", "line": 25, "source": "return redirect(url_for(\"auth.login\"))"}
        ]
      },
      "review_hint": "Verify callers expect the new default value"
    }
  ],
  "stats": {
    "files_analyzed": 2,
    "symbols_changed": 2,
    "silence_reason": null
  }
}
```

!!! note "Illustrative"
    This example is based on real DiffGuard output against Flask commit `eca5fd1d`, with some fields simplified for clarity. Field names and types are accurate.

---

## Summarize output

`diffguard summarize <ref-range> --format json`

The summarize command outputs a complete structural map of the diff. Defined by Pydantic v2 models in `src/diffguard/schema.py`.

### `DiffGuardOutput` (top-level)

| Field | Type | Description |
|-------|------|-------------|
| `schema_version` | `str` | Currently `"1.1"` |
| `meta` | `Meta` | Run metadata: ref range, stats, timing |
| `files` | `list[FileChange]` | Per-file semantic changes |
| `summary` | `Summary` | Aggregate: change types, breaking changes, focus areas |
| `tiered` | `TieredSummary` | Human-readable summaries at different token budgets |

### `Meta`

| Field | Type | Description |
|-------|------|-------------|
| `ref_range` | `str` | Git ref range analyzed |
| `stats` | `DiffStats` | `files`, `additions`, `deletions` counts |
| `warnings` | `list[str]` | Parse errors, truncation signals |
| `timing_ms` | `float | None` | Wall-clock time for analysis |

### `FileChange`

| Field | Type | Description |
|-------|------|-------------|
| `path` | `str` | File path relative to repo root |
| `language` | `str | None` | Detected language |
| `change_type` | `"added" | "removed" | "modified" | "renamed"` | File-level change type |
| `generated` | `bool` | Lock files, protobuf output, etc. |
| `binary` | `bool` | Binary file (skipped) |
| `parse_error` | `bool` | Tree-sitter couldn't parse this file |
| `unsupported_language` | `bool` | No grammar available |
| `changes` | `list[SymbolChange]` | Symbol-level changes |

### `SymbolChange`

| Field | Type | Description |
|-------|------|-------------|
| `kind` | `str` | One of: `function_added`, `function_removed`, `function_modified`, `class_added`, `class_removed`, `class_modified`, `signature_changed`, `moved` |
| `name` | `str` | Symbol name |
| `signature` | `str | None` | Full signature (for added/removed) |
| `before_signature` | `str | None` | Old signature (for `signature_changed`) |
| `after_signature` | `str | None` | New signature (for `signature_changed`) |
| `file_from` | `str | None` | Source file (for `moved`) |
| `line` | `int | None` | Line number in new file |
| `breaking` | `bool` | Whether this breaks the public API |
| `detail` | `dict | None` | Language-specific metadata |

### `Summary`

| Field | Type | Description |
|-------|------|-------------|
| `change_types` | `dict[str, int]` | Counts by category |
| `breaking_changes` | `list[SymbolChange]` | All breaking changes |
| `focus` | `list[str]` | Most important items for reviewer attention |

### `TieredSummary`

| Field | Type | Description |
|-------|------|-------------|
| `oneliner` | `str` | ~20 tokens |
| `short` | `str` | ~80 tokens |
| `detailed` | `str` | Full narrative |

### Design principles

- **Semantic change units.** Function/class level with signatures — not line numbers.
- **Breaking changes at top level.** Not buried in file details.
- **No opinions.** Structural facts only.
- **Graceful degradation.** Parse errors and unsupported languages are flagged, never crashes.
