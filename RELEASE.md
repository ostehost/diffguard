# Release Process

## Roles

**Release owner:** Oste (manager). Tags, pushes, monitors CI, verifies install.
When a release agent exists, it takes over this role.

## Release Blockers

A release MUST NOT ship if any of these are true:
- Tests fail (`pytest`)
- Linting fails (`ruff check src/ tests/`)
- Version not bumped in `pyproject.toml`
- README.md is stale or misaligned with current positioning

## Pre-release Checklist

- [ ] All tests pass (`pytest`)
- [ ] Linting passes (`ruff check src/ tests/`)
- [ ] Version bumped in `pyproject.toml`
- [ ] README.md examples verified against real tool output (run every example command, diff against what's written)
- [ ] Docs examples verified against real tool output
- [ ] CHANGELOG updated (if maintained)

## Cutting a Release

```bash
# 1. Bump version in pyproject.toml
#    e.g. version = "0.2.0"

# 2. Commit the version bump
git commit -am "release: v0.2.0"

# 3. Create the tag
git tag v0.2.0

# 4. Push commit and tag
git push origin main --tags

# 5. GitHub Actions handles:
#    build & test → TestPyPI → PyPI → smoke test

# 6. Verify the release
pip install diffguard && diffguard --version
```

The workflow uses [Trusted Publishing](https://docs.pypi.org/trusted-publishers/) (OIDC) — no API tokens needed.

## Post-release Checklist

- [ ] Verify package on [pypi.org/project/diffguard](https://pypi.org/project/diffguard/)
- [ ] Test install in a clean venv:
  ```bash
  python -m venv /tmp/test-diffguard && source /tmp/test-diffguard/bin/activate
  pip install diffguard
  diffguard --version
  deactivate && rm -rf /tmp/test-diffguard
  ```
- [ ] Create a [GitHub Release](https://github.com/ostehost/diffguard/releases/new) from the tag (optional)

## Rollback

PyPI does not allow re-uploading the same version. If a broken package ships:

1. **Yank the version:** `pip install diffguard` won't grab yanked versions by default
2. **Bump to patch version** (e.g., v0.1.0 → v0.1.1) with the fix
3. **Cut a patch release** following the same process above
