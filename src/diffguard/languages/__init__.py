"""Per-language tree-sitter configurations."""

from __future__ import annotations

import importlib
import os
from types import ModuleType

import tree_sitter

SUPPORTED_LANGUAGES: set[str] = {"python", "typescript", "javascript", "go"}

_EXTENSION_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
}

_LANG_TO_MODULE: dict[str, str] = {
    "python": "diffguard.languages.python",
    "typescript": "diffguard.languages.typescript",
    "javascript": "diffguard.languages.typescript",
    "go": "diffguard.languages.go",
}


def detect_language(filename: str) -> str | None:
    """Detect language from file extension."""
    _, ext = os.path.splitext(filename)
    return _EXTENSION_MAP.get(ext)


def get_language_module(language: str) -> ModuleType:
    """Get the language module for a given language."""
    module_path = _LANG_TO_MODULE.get(language)
    if module_path is None:
        msg = f"Unsupported language: {language}"
        raise ValueError(msg)
    return importlib.import_module(module_path)


def get_parser(language: str) -> tree_sitter.Parser:
    """Get the tree-sitter parser for a language."""
    mod = get_language_module(language)
    if language == "javascript" and hasattr(mod, "get_js_language"):
        lang: tree_sitter.Language = mod.get_js_language()
    else:
        lang = mod.get_language()
    return tree_sitter.Parser(lang)
