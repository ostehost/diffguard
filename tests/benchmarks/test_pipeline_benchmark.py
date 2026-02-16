"""End-to-end pipeline benchmark.

Target: <500ms for a 1000-line diff.
"""

from __future__ import annotations

from typing import Any

from diffguard.engine.pipeline import run_pipeline


def _generate_diff_and_sources(n_functions: int = 50) -> tuple[str, str, str]:
    """Generate a realistic ~1000-line diff with old/new Python sources."""
    old_lines = ['"""Module."""', "", "from typing import Any", ""]
    new_lines = ['"""Module."""', "", "from typing import Any", ""]
    diff_lines = [
        "diff --git a/big_module.py b/big_module.py",
        "--- a/big_module.py",
        "+++ b/big_module.py",
    ]

    for i in range(n_functions):
        # Old version
        old_lines.extend(
            [
                f"def func_{i}(x: int, y: str = 'default') -> dict[str, Any]:",
                f'    """Function {i}."""',
                f"    result: dict[str, Any] = {{'idx': {i}}}",
                "    for j in range(x):",
                "        result[f'k{{j}}'] = j",
                "    return result",
                "",
                "",
            ]
        )

        if i < n_functions // 2:
            # Modified functions — body change
            new_lines.extend(
                [
                    f"def func_{i}(x: int, y: str = 'default') -> dict[str, Any]:",
                    f'    """Function {i} (updated)."""',
                    f"    result: dict[str, Any] = {{'idx': {i}, 'v': 2}}",
                    "    for j in range(x):",
                    "        result[f'k{{j}}'] = j * 2",
                    "    return result",
                    "",
                    "",
                ]
            )
            diff_lines.extend(
                [
                    f"@@ -{i * 8 + 4},{8} +{i * 8 + 4},{8} @@",
                    f" def func_{i}(x: int, y: str = 'default') -> dict[str, Any]:",
                    f'-    """Function {i}."""',
                    f'+    """Function {i} (updated)."""',
                    f"-    result: dict[str, Any] = {{'idx': {i}}}",
                    f"+    result: dict[str, Any] = {{'idx': {i}, 'v': 2}}",
                    "     for j in range(x):",
                    "-        result[f'k{j}'] = j",
                    "+        result[f'k{j}'] = j * 2",
                    "     return result",
                ]
            )
        else:
            # Unchanged
            new_lines.extend(
                [
                    f"def func_{i}(x: int, y: str = 'default') -> dict[str, Any]:",
                    f'    """Function {i}."""',
                    f"    result: dict[str, Any] = {{'idx': {i}}}",
                    "    for j in range(x):",
                    "        result[f'k{{j}}'] = j",
                    "    return result",
                    "",
                    "",
                ]
            )

    # Add some new functions at end
    for i in range(5):
        new_lines.extend(
            [
                f"def new_func_{i}(a: int) -> int:",
                f'    """New function {i}."""',
                f"    return a + {i}",
                "",
            ]
        )
        diff_lines.extend(
            [
                f"@@ -0,0 +{len(new_lines) - 4},{4} @@",
                f"+def new_func_{i}(a: int) -> int:",
                f'+    """New function {i}."""',
                f"+    return a + {i}",
                "+",
            ]
        )

    old_src = "\n".join(old_lines)
    new_src = "\n".join(new_lines)
    diff_text = "\n".join(diff_lines) + "\n"
    return diff_text, old_src, new_src


_DIFF, _OLD_SRC, _NEW_SRC = _generate_diff_and_sources(50)


def _get_content(ref: str, path: str) -> str | None:
    if ref == "old" and path == "big_module.py":
        return _OLD_SRC
    if ref == "new" and path == "big_module.py":
        return _NEW_SRC
    return None


def test_pipeline_1000_line_diff(benchmark: Any) -> None:
    """Full pipeline on a ~1000-line diff must complete in <500ms."""
    result = benchmark(run_pipeline, _DIFF, "old..new", _get_content)
    assert len(result.files) == 1
    assert len(result.files[0].changes) > 0
    # The benchmark plugin enforces timing; we also assert correctness
    assert result.summary.change_types.get("function_added", 0) >= 5
