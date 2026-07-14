# Schema Reference

`schema.py` is the authority for both command JSON contracts.

## Path display boundary

Git paths can contain bytes that are not valid UTF-8. DiffGuard retains those exact bytes
internally through Python's `surrogateescape` handling, but JSON cannot safely carry the
resulting lone Unicode surrogates. In review and summarize JSON, each invalid byte is therefore
rendered as display-only `\xNN` text. For example, raw path bytes `b"pkg/\xff.py"` appear in a
parsed `file` string as `pkg/\xff.py` (the JSON source contains `"pkg/\\xff.py"`).

This display form is not a unique or operational path identifier: it can collide with a literal
POSIX filename containing the same backslash sequence. Agents and other consumers must not feed
these rendered values back into Git or filesystem operations. Use the original Git snapshot/path
surface when exact path identity is required. This is a serialization limitation only; it does
not change either JSON schema version or DiffGuard's internal byte-preserving path handling.

After that invalid-byte display conversion, review and summarize JSON are emitted as ASCII bytes;
non-ASCII Unicode code points use JSON escape sequences. This prevents raw C1, bidirectional, and
other source-controlled Unicode characters from affecting terminals or CI logs. A conforming JSON
parser reconstructs the model's exact Unicode string values, so this byte-level hardening does not
change parsed semantics and does not require a schema-version bump.

## Review output (`1.1.0`)

```bash
diffguard review --against origin/main --worktree --format json
```

`ReviewEnvelope` validates populated findings, empty results, partial analysis, and tool errors.

### Envelope

| Field | Type | Meaning |
|---|---|---|
| `version` | literal `"1.1.0"` | Review schema version. Other values are rejected. |
| `status` | `"ok" | "error"` | Whether analysis completed. |
| `mode` | `"committed" | "staged" | "worktree"` | Snapshot mode. |
| `ref_range` | `str` | Effective comparison; worktree uses `<merge-base>..:worktree`. |
| `findings` | `list[ReviewFinding]` | Stable contract findings. |
| `warnings` | `list[ReviewWarning]` | Structured non-fatal analysis gaps. |
| `stats` | `ReviewStats` | Analysis counts and silence reason. |
| `error` | `ReviewError | null` | Structured tool failure. |

### Finding

| Field | Type | Meaning |
|---|---|---|
| `rule_id` | `str` | Stable rule ID such as `DG104`. |
| `category_id` | `str` | Stable machine category such as `default_removed`. |
| `category` | `str` | Human-readable category. |
| `symbol`, `file`, `line`, `language` | scalar/null | Finding display location; path strings follow the path display boundary above. |
| `source_file` | `str | null` | Source path for a possible cross-file move; `null` for other findings. `file` remains the destination path. |
| `before_signature`, `after_signature` | `str | null` | Signature evidence when applicable. |
| `breaking` | `bool | null` | `true`: bounded incompatible call shape; `false`: bounded rule found no call-shape break; `null`: not proven. |
| `confidence` | `high | medium | low` | Confidence in the finding statement, not a probability. |
| `evidence` | `list[ReviewEvidence]` | Factual syntax/reference/gap statements. |
| `references` | `list[ReviewReference]` | Up to 20 unresolved syntactic references. |
| `analysis_gaps` | `list[str]` | Missing compiler/type/ownership proof. |
| `review_hint` | `str` | Next verification action. |

### Reference

| Field | Type | Meaning |
|---|---|---|
| `file`, `line`, `symbol`, `source` | scalar | Source display location and line; path strings follow the path display boundary above. |
| `kind` | `import | call | reference` | AST context. |
| `confidence` | `high | medium | low` | Ownership confidence; currently low. |
| `resolution` | `"unresolved"` | Explicitly no ownership proof. |
| `evidence` | `str` | Why the name was emitted. |

### Statistics and warnings

`ReviewStats` contains `files_analyzed`, `symbols_changed`, `parse_errors`, `reference_count`, and nullable `silence_reason`. `ReviewWarning` contains stable `code`, `message`, and optional display-only `file`. `ReviewError` contains `code` and `message`.

### Populated example

```json
{
  "version": "1.1.0",
  "status": "ok",
  "mode": "worktree",
  "ref_range": "abc123..:worktree",
  "findings": [
    {
      "rule_id": "DG104",
      "category_id": "default_removed",
      "category": "DEFAULT REMOVED",
      "symbol": "helper",
      "file": "lib.py",
      "source_file": null,
      "line": 1,
      "language": "python",
      "before_signature": "def helper(a=1)",
      "after_signature": "def helper(a)",
      "breaking": true,
      "confidence": "high",
      "evidence": [{"kind": "syntax", "message": "Default removed from Python parameter 'a'"}],
      "references": [
        {
          "file": "main.py",
          "line": 2,
          "symbol": "helper",
          "kind": "call",
          "source": "value = helper()",
          "confidence": "low",
          "resolution": "unresolved",
          "evidence": "AST name match; symbol ownership unresolved"
        }
      ],
      "analysis_gaps": [],
      "review_hint": "Update calls that omit the now-required parameter"
    }
  ],
  "warnings": [],
  "stats": {
    "files_analyzed": 1,
    "symbols_changed": 1,
    "parse_errors": 0,
    "reference_count": 1,
    "silence_reason": null
  },
  "error": null
}
```

### Rule IDs

| ID | Category |
|---|---|
| `DG101` | parameter removed |
| `DG102` | parameter added/required parameter added |
| `DG103` | parameters reordered |
| `DG104` | default removed |
| `DG105` | default value changed |
| `DG106` | default/optional parameter added |
| `DG107` | Python parameter annotation changed |
| `DG108` | return annotation/type syntax changed |
| `DG109` | Python parameter renamed |
| `DG110` | other bounded signature syntax change |
| `DG201` | symbol removed |
| `DG202` | possible cross-file symbol move |

### Migration from published review `0.1.0`

This is intentionally a breaking schema migration:

- PyPI `0.1.3` emits review schema `0.1.0`; unreleased main briefly used `0.2.0`;
- both predecessors migrate to review schema `1.0.0` as part of `0.2.0`;
- review JSON is now constructed and serialized by Pydantic models in `schema.py`;
- `status`, `mode`, `error`, `rule_id`, `category_id`, `breaking`, `confidence`, `evidence`, `analysis_gaps`, and `reference_count` are added;
- string warnings become structured warning objects;
- hand-built `impact.production_callers`, `impact.test_callers`, and `impact.callers` are removed because name-only matching did not prove ownership;
- `references` replaces caller claims and explicitly carries syntactic context and unresolved resolution;
- JSON-requested tool errors now emit the same envelope with `status: "error"` and exit `2`.

Consumers must migrate by schema version; do not silently treat review references as exact callers.

### Migration from review `1.0.0` to `1.1.0`

Review schema `1.1.0` additively introduces nullable `ReviewFinding.source_file`.
For `DG202` possible-move findings, `source_file` is the prior path and `file` is the
destination path. It is `null` for non-move findings, so consumers that ignore unknown
fields remain compatible. Consumers that validate the top-level version must accept
`1.1.0` before ingesting this output.

```json
{
  "category_id": "possible_symbol_move",
  "symbol": "helper",
  "source_file": "src/old_module.py",
  "file": "src/new_module.py"
}
```

## Summarize output (`2.0`)

`DiffGuardOutput` emits `schema_version: "2.0"` with `meta`, `files`, `summary`, and `tiered`. `SymbolChange` adds nullable/empty-default `rule_id`, `category_id`, `category`, `confidence`, `evidence`, and `analysis_gaps`; `breaking` is tri-state (`bool | null`). These additions let the review model preserve evidence without a competing authority.

Summarize path fields and path text embedded in warnings or tiered summaries use the same
display-only invalid-byte rendering described in the path display boundary above.

When the input diff is empty, `summarize --format json` exits `3` and emits the same validated
`2.0` envelope with zero files, additions, deletions, and symbol changes. Consumers may therefore
parse JSON before interpreting the no-changes exit code.

The `1.1` to `2.0` version bump records that consumers assuming `breaking` is always boolean must now accept `null` for unproven compatibility.
