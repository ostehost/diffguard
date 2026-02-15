# AGENTS.md — DiffGuard v2

## Module Boundaries
- `engine/parser.py` — tree-sitter parsing ONLY. No git logic, no matching.
- `engine/matcher.py` — symbol matching ONLY. Takes old+new symbol lists, returns matches.
- `engine/classifier.py` — change classification ONLY. Takes matches, returns classified changes.
- `engine/signatures.py` — signature comparison ONLY. Takes old+new signatures, detects breaking.
- `engine/summarizer.py` — summary generation ONLY. Takes classified changes, produces tiered text.
- `schema.py` — Pydantic models. THE contract. Changes require migration notes in PR.
- `git.py` — all git subprocess calls live here. Nothing else touches git.
- `languages/{lang}/` — per-language tree-sitter config + queries.scm.

## Conventions
- Type hints everywhere. `mypy --strict` must pass.
- Tests mirror source: `test_parser.py` tests `parser.py`, etc.
- Fixtures: synthetic for unit tests, real-world for integration tests. Both live in `tests/fixtures/`.
- All JSON output must validate against schema.py models.
- No print statements — use structured logging (logging module).
- Functions over classes unless state is genuinely needed.

## What NOT to Do
- Don't import across engine modules horizontally (parser doesn't import matcher).
- Don't shell out to git anywhere except `git.py`.
- Don't add dependencies without justification in PR description.
- Don't modify schema.py without migration notes.
- Don't write tests that depend on network or external repos — use fixtures.
