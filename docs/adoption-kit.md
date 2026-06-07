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
uv build
uv run diffguard review origin/main..HEAD --format json || test "$?" = 1
```

## Repo-local rules

- `uv.lock` is committed and checked with `uv lock --check`; CI installs through `uv sync --group dev --group docs` or an equivalent locked sync.
- `ruff`, `mypy --strict`, and `pytest` are the required quality gate via `just ci`.
- Generated artifacts and local tool state stay ignored: caches, virtualenvs, coverage output, `dist/`, `build/`, `site/`, and `.clawpatch/`.
- `.clawpatch/` is intentionally local-only because its project state includes machine-specific absolute paths.
- DiffGuard reports package version from one source (`diffguard.__version__`) for CLI version output; JSON `version` fields describe DiffGuard review output schema compatibility and must change only with schema semantics.
- Preserve checks must re-resolve live: use `just preserve-verify`, `just preserve-verify-checksum`, or `just preserve-verify-bundle` rather than relying on stale notes.

## Do not generalize

- DiffGuard currently supports Python 3.11+, matching its packaging and CI reality; do not force a Python-version bump just because the shared standard prefers newer projects on 3.12+.
- The tracked `.vscode/settings.json` is project metadata only. Do not add machine-local interpreter paths or user-specific editor settings there.
- Release artifacts in `dist/` are build outputs, not source preservation. Rebuild them from source during release gates.
- DiffGuard review exit code `1` means high-signal findings were present, not a tool failure; gates that run `diffguard review` should explicitly tolerate exit `1` when the JSON/text output is valid.
