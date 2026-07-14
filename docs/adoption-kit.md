# DiffGuard Standards Adoption Kit

DiffGuard adopts the shared preservation and Python standards by reference, then translates them into repo-local commands. The canonical sources stay external:

- [Git Preservation Standards](https://github.com/ostehost/universal8-framework/blob/main/standards-synthesis-20260603/GIT_PRESERVATION_STANDARDS.md)
- [Hermetic Build Environment Standard](https://github.com/ostehost/universal8-framework/blob/main/standards-synthesis-20260603/HERMETIC_BUILD_ENV_STANDARD.md)
- [Python Standards](https://github.com/ostehost/universal8-framework/blob/main/standards-synthesis-20260603/PYTHON_STANDARDS.md)

## 15-minute local checklist

Run these before destructive git operations, before release tags, and before asking another agent or reviewer to trust a branch.

```sh
repo="$(git rev-parse --show-toplevel)"

git -C "$repo" status -sb
git -C "$repo" diff --cached --name-status
git -C "$repo" diff --name-status
git -C "$repo" ls-files --others --exclude-standard
git -C "$repo" status --ignored --short \
  | grep '^!! ' \
  | grep -vE '(^!! )?(\.venv/|venv/|__pycache__/|\.mypy_cache/|\.pytest_cache/|\.ruff_cache/|\.tox/|\.nox/|htmlcov/|\.coverage|dist/|build/|site/|[^ ]*\.egg-info/|\.clawpatch/)' \
  || echo "no suspicious ignored paths"
git -C "$repo" stash list
git -C "$repo" for-each-ref --format='%(refname:short) %(upstream:track)' refs/heads

git -C "$repo" remote -v
git -C "$repo" remote -v | grep -E 'glpat-|ghp_|gho_|//[^/@]+:[^/@]+@' \
  && { echo "REFUSE: credential in remote URL"; exit 1; } \
  || echo "no embedded credentials in remotes"

uv lock --check
just ci
just validate-corpus
just docs-build
uv build
set +e
uv run diffguard review --against origin/main --worktree --format json
rc=$?
set -e
test "$rc" = 0 || test "$rc" = 1
```

## Self-review gate ranges

CI validates `diffguard review --format json` output without failing on exit `1`, because that
status means findings were present rather than the tool failed. Pull request checks use a
three-dot range from the PR base so only the branch's merge-base-relative changes are reviewed.
Push checks compare the previous pushed commit to the new head; new branches fall back to a
three-dot range from the default branch.

## Repo-local rules

- `uv.lock` is committed and checked with `uv lock --check`; CI installs through `uv sync --group dev --group docs` or an equivalent locked sync.
- `ruff`, `mypy --strict`, and `pytest` are the required quality gate via `just ci`.
- Generated artifacts and local tool state stay ignored: caches, virtualenvs, coverage output, `dist/`, `build/`, `site/`, and `.clawpatch/`.
- `.clawpatch/` is intentionally local-only because its project state includes machine-specific absolute paths.
- Package metadata and CLI runtime versions are intentionally duplicated in `pyproject.toml` and
  `diffguard.__version__`; the release workflow refuses a tag unless all three values match. JSON
  `version` fields describe review/summarize schema compatibility and change only with schema
  semantics.
- Preserve checks must re-resolve live: use `just preserve-verify`, `just preserve-verify-checksum`, or `just preserve-verify-bundle` rather than relying on stale notes.

## Do not generalize

- DiffGuard currently supports Python 3.11+, matching its packaging and CI reality; do not force a Python-version bump just because the shared standard prefers newer projects on 3.12+.
- The tracked `.vscode/settings.json` is project metadata only. Do not add machine-local interpreter paths or user-specific editor settings there.
- Release artifacts in `dist/` are build outputs, not source preservation. Rebuild them from source during release gates.
- DiffGuard review exit code `1` means high-signal findings were present, not a tool failure; gates that run `diffguard review` should explicitly tolerate exit `1` when the JSON/text output is valid.
- Closeout guidance uses `--worktree` once near completion. Do not recommend a per-edit `HEAD~1..HEAD` scan, which examines committed history instead of current edits.
