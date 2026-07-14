# Release Process

## Roles

**Release owner:** Oste (manager). Authorizes release movement and owns the final decision.
A release agent may execute tagging, pushing, monitoring, and verification only after that explicit
authorization. The presence of a release agent does not grant commit, tag, push, or publish authority.

## Release Blockers

A release MUST NOT ship if any of these are true:
- Tests fail (`pytest`)
- Linting fails (`ruff check src/ tests/`)
- Version not bumped consistently in `pyproject.toml` and `diffguard.__version__`
- README.md is stale or misaligned with current positioning
- Review schema migration notes are missing or stale
- `just validate-corpus` reports a miss, false positive, unexpected parse gap, or missing expected parse gap

## Pre-release Checklist

- [ ] All tests pass (`pytest`)
- [ ] Linting passes (`ruff check src/ tests/`)
- [ ] Version bumped to the same value in `pyproject.toml` and `diffguard.__version__`
- [ ] CI passes on the declared minimum Python 3.11 and the current upper test target
- [ ] README.md examples verified against real tool output (run every example command, diff against what's written)
- [ ] Docs examples verified against real tool output
- [ ] CHANGELOG updated (if maintained)
- [ ] `just validate-corpus` passes and the documented sample size matches output
- [ ] Composite Action installs from `github.action_path`
- [ ] Composite Action runtime and PEP 517 build closures match their exact constraints
- [ ] Self-hosted Action consumers require GitHub Actions Runner v2.327.1 or newer;
      GitHub-hosted runners are managed by GitHub
- [ ] The built wheel installs in an isolated environment and reports the exact tag version before upload
- [ ] The repository default branch (currently `main`) is the protected release branch. Verify the
      live GitHub ruleset/settings: the publish workflow proves tag ancestry from the default branch,
      but workflow code cannot prove that external branch protection is enabled.

## `0.2.0` authorization gate

The `0.2.0` recovery may be committed, tagged, or published only after the final implementation
review accepts the schema migrations, worktree behavior, Action source binding, and validation
evidence, and the release owner explicitly authorizes movement.

### Recovery worktree disposition

Until a release commit is explicitly authorized and created, treat the broad recovery worktree as
one intentional, cohesive `0.2.0` unit. Its source, tests, documentation, Action configuration, and
agent assets are owned by this recovery effort and should be retained together. Do not archive,
delete, or split it solely by file class. Implementation and review agents may correct and validate
the unit, but must leave it uncommitted and unpushed until the release owner explicitly authorizes a
commit or release operation.

## Cutting a Release

```bash
# 1. Bump version in pyproject.toml and src/diffguard/__init__.py
#    e.g. version = "0.2.0"

# 2. After release-owner authorization, stage and inspect the cohesive candidate.
#    `git commit -am` is insufficient because it omits new files.
git add -A
git diff --cached --stat
git diff --cached --check

# 3. Commit the reviewed candidate
git commit -m "release: v0.2.0"

# 4. Create the tag
git tag v0.2.0

# 5. Atomically push only the reviewed main update and its release tag
git push --atomic origin main v0.2.0

# 6. GitHub Actions handles:
#    tests on Python 3.11 and 3.14, an isolated built-wheel smoke test,
#    then optional TestPyPI and production PyPI publish in parallel.
#    Production does not wait for TestPyPI; its exact-version smoke test follows PyPI.

# 7. Independently verify the exact published release in an isolated environment
VERSION=0.2.0
(
  set -e
  verify_dir="$(mktemp -d)"
  trap 'rm -rf "$verify_dir"' EXIT
  python3 -m venv "$verify_dir/venv"
  "$verify_dir/venv/bin/python" -m pip install --no-cache-dir "diffguard==$VERSION"
  test "$("$verify_dir/venv/bin/diffguard" --version)" = "diffguard, version $VERSION"
)
```

The workflow uses [Trusted Publishing](https://docs.pypi.org/trusted-publishers/) (OIDC) — no API tokens needed.

## Post-release Checklist

- [ ] Verify package on [pypi.org/project/diffguard](https://pypi.org/project/diffguard/)
- [ ] Repeat the isolated exact-version verification from step 7 after the workflow smoke test completes.
- [ ] Create a [GitHub Release](https://github.com/ostehost/diffguard/releases/new) from the tag (optional)

## Rollback

PyPI does not allow re-uploading the same version. If a broken package ships:

1. **Yank the version:** `pip install diffguard` won't grab yanked versions by default
2. **Bump to patch version** (e.g., v0.1.0 → v0.1.1) with the fix
3. **Cut a patch release** following the same process above
