"""Tests for language detection and registry."""

from diffguard.languages import SUPPORTED_LANGUAGES, detect_language


def test_supported_languages() -> None:
    assert SUPPORTED_LANGUAGES == {"python", "typescript", "javascript", "go"}


def test_detect_python() -> None:
    assert detect_language("foo.py") == "python"


def test_detect_javascript() -> None:
    assert detect_language("app.js") == "javascript"
    assert detect_language("component.jsx") == "javascript"


def test_detect_typescript() -> None:
    assert detect_language("app.ts") == "typescript"
    assert detect_language("component.tsx") == "typescript"


def test_detect_go() -> None:
    assert detect_language("main.go") == "go"


def test_detect_unknown() -> None:
    assert detect_language("file.rs") is None
    assert detect_language("file.c") is None
    assert detect_language("README.md") is None
