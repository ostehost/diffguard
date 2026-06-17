"""Shared path-classification helpers for the DiffGuard engine."""

from __future__ import annotations

import re

# ---- Test file detection ----
_TEST_PATH_RE = re.compile(
    r"(^|/)(tests?/|spec/|__tests__/)"
    r"|"
    r"(^|/)test_[^/]*\.py$"
    r"|"
    r"(^|/)[^/]*_test\.py$"
    r"|"
    r"(^|/)[^/]*[._]spec\.(ts|js|tsx|jsx)$"
    r"|"
    r"(^|/)[^/]*[._]test\.(ts|js|tsx|jsx)$",
    re.IGNORECASE,
)


def is_test_file(path: str) -> bool:
    """Return True if *path* looks like a test file."""
    return _TEST_PATH_RE.search(path) is not None
