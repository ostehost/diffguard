"""Shared test fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

# Exclude cloned repos from test collection — they contain their own test files
# that cause import errors and package conflicts.
collect_ignore_glob = ["ab_repos/**"]

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def load_fixture() -> Any:
    """Load a JSON fixture from tests/fixtures/<subdir>/<name>."""

    def _load(subdir: str, name: str) -> dict[str, Any]:
        path = FIXTURES_DIR / subdir / name
        return json.loads(path.read_text())  # type: ignore[no-any-return]

    return _load
