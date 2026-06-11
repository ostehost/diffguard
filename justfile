# DiffGuard — Development Workflow
#
# Follows the shared preservation + Python standards adoption kits. The five
# canonical recipes:
#
#   check  → read-only validation (format-check + lint + typecheck)
#   fix    → auto-fix formatting and safe lint fixes (modifies files)
#   test   → run all tests
#   ready  → fix + check + test  (one-shot before push)
#   ci     → strict gate; what CI runs
#
# Aliases: t (test), f (fix), r (ready)

alias t := test
alias f := fix
alias r := ready

default:
    @just --list --unsorted

# Install/sync the full local dev environment
install:
    uv sync --group dev --group docs

# Standard recipe: check
#   Read-only validation only. Must not run tests.
check: format-check lint typecheck
    @echo ""
    @echo "✅ Static checks passed"

# Standard recipe: fix
#   Safe, idempotent autofixes only.
fix: format
    @echo ""
    @echo "✅ Auto-fixes applied"

# Standard recipe: ready
#   fix + check + test — canonical pre-push flow.
ready:
    @echo "🚀 ready: fix → check → test"
    @echo ""
    @just fix
    @echo ""
    @just check
    @echo ""
    @just test

# Standard recipe: ci
#   Strict validation + tests. What CI runs.
ci:
    @echo "🚀 ci: strict check + test"
    @echo ""
    @just check
    @echo ""
    @just test
    @echo ""
    @echo "✅ CI checks passed!"

# Re-resolving preservation gates. "Preserved" means these pass now — not that
# a previous note said a ref, checksum, or bundle existed.
preserve-verify repo sha:
    git -C "{{repo}}" branch -r --contains {{sha}}
    @echo "OK: {{sha}} reachable from a remote ref"

preserve-verify-checksum manifest:
    shasum -a 256 -c {{manifest}}
    @echo "OK: checksum manifest re-resolved against on-disk files"

preserve-verify-bundle repo bundle:
    git -C "{{repo}}" bundle verify {{bundle}}

# Run tests (optionally filtered: `just test tests/test_cli.py`)
test *args:
    #!/usr/bin/env bash
    set -euo pipefail
    uv run pytest {{args}}

lint:
    uv run ruff check .

typecheck:
    uv run mypy src/

format-check:
    uv run ruff format --check .

format:
    uv run ruff format .
    uv run ruff check --fix .

build:
    uv build

docs-build:
    uv run mkdocs build --strict

docs-serve:
    uv run mkdocs serve

clean:
    rm -rf .pytest_cache .ruff_cache .mypy_cache build dist site htmlcov .coverage .clawpatch

info:
    python3 -c "import tomllib, pathlib; p = tomllib.loads(pathlib.Path('pyproject.toml').read_text()); print(f\"DiffGuard {p['project']['version']}\"); print(f\"Python >= {p['project']['requires-python']}\"); print(f\"Dependencies: {len(p['project']['dependencies'])}\")"

doctor:
    #!/usr/bin/env bash
    set -euo pipefail
    command -v uv >/dev/null && echo "✅ uv $(uv --version)" || { echo "❌ uv missing"; exit 1; }
    command -v python3 >/dev/null && echo "✅ python3 $(python3 --version)" || { echo "❌ python3 missing"; exit 1; }
    echo "✅ toolchain looks present"
