"""Parser performance benchmarks."""

from __future__ import annotations

from typing import Any

from diffguard.engine.parser import parse_file

SMALL_PYTHON = '''\
"""A small utility module."""

from typing import Optional


def validate_email(email: str) -> bool:
    """Validate an email address."""
    if "@" not in email:
        return False
    parts = email.split("@")
    if len(parts) != 2:
        return False
    return len(parts[1]) > 0


def format_name(first: str, last: str, middle: Optional[str] = None) -> str:
    """Format a full name."""
    if middle:
        return f"{first} {middle} {last}"
    return f"{first} {last}"


class UserValidator:
    """Validates user data."""

    def __init__(self, strict: bool = True) -> None:
        self.strict = strict

    def validate_age(self, age: int) -> bool:
        """Check if age is valid."""
        if self.strict:
            return 0 < age < 150
        return age >= 0

    def validate_username(self, username: str) -> bool:
        """Check if username is valid."""
        if len(username) < 3:
            return False
        if len(username) > 50:
            return False
        return username.isalnum()

    def validate_all(self, username: str, age: int, email: str) -> list[str]:
        """Validate all fields and return errors."""
        errors: list[str] = []
        if not self.validate_username(username):
            errors.append("invalid username")
        if not self.validate_age(age):
            errors.append("invalid age")
        if not validate_email(email):
            errors.append("invalid email")
        return errors
'''


def _generate_large_python() -> str:
    """Generate a ~500 line Python file."""
    lines = ['"""A large module with many functions."""', "", "from typing import Any", ""]
    for i in range(30):
        lines.extend(
            [
                f"def function_{i}(x: int, y: str = 'default') -> dict[str, Any]:",
                f'    """Function number {i}."""',
                f"    result: dict[str, Any] = {{'index': {i}, 'x': x, 'y': y}}",
                "    for j in range(x):",
                "        result[f'item_{{j}}'] = j * 2",
                "    return result",
                "",
                "",
            ]
        )
    lines.extend(
        [
            "class DataProcessor:",
            '    """Processes data with multiple methods."""',
            "",
            "    def __init__(self, name: str) -> None:",
            "        self.name = name",
            "        self.data: list[Any] = []",
            "",
        ]
    )
    for i in range(20):
        lines.extend(
            [
                f"    def process_{i}(self, value: int) -> int:",
                f'        """Process method {i}."""',
                f"        return value * {i + 1} + len(self.data)",
                "",
            ]
        )
    return "\n".join(lines)


LARGE_PYTHON = _generate_large_python()


def test_parse_python_small(benchmark: Any) -> None:
    """Benchmark parsing a small Python file (~50 lines)."""
    result = benchmark(parse_file, SMALL_PYTHON, "python")
    assert not result.parse_error


def test_parse_python_large(benchmark: Any) -> None:
    """Benchmark parsing a large Python file (~500 lines)."""
    result = benchmark(parse_file, LARGE_PYTHON, "python")
    assert not result.parse_error
    assert len(result.symbols) > 0
