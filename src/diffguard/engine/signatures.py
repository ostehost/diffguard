"""Signature comparison for breaking change detection.

Standalone module — no engine imports, just string analysis.
Conservative: when in doubt, return False (don't cry wolf).
"""

from __future__ import annotations

import re


def _extract_balanced_params(signature: str) -> str | None:
    """Extract content between first balanced parentheses in signature."""
    start = signature.find("(")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(signature)):
        ch = signature[i]
        if ch in ("(", "["):
            depth += 1
        elif ch in (")", "]"):
            depth -= 1
            if depth == 0:
                return signature[start + 1 : i]
    return None


def extract_params(signature: str) -> list[str]:
    """Extract parameter list from a signature string.

    Handles signatures like:
        def foo(a: int, b: str = "x") -> bool
        def bar(a: Callable[[int], str], b: int) -> None
        fn foo(a: i32, b: &str) -> bool
    """
    params_str = _extract_balanced_params(signature)
    if params_str is None or not params_str.strip():
        return []

    # Split by comma respecting bracket/paren depth
    params: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in params_str:
        if ch in ("(", "[", "{"):
            depth += 1
        elif ch in (")", "]", "}"):
            depth -= 1
        if ch == "," and depth == 0:
            params.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    last = "".join(current).strip()
    if last:
        params.append(last)

    return [p for p in params if p and p not in ("self", "cls")]


def _extract_return_type(signature: str) -> str | None:
    """Extract return type annotation from signature."""
    match = re.search(r"\)\s*->\s*(.+)$", signature)
    if match:
        return match.group(1).strip()
    return None


def _param_has_default(param: str) -> bool:
    """Check if a parameter has a default value."""
    return "=" in param


def _strip_default(param: str) -> str:
    """Strip default value from parameter, keeping name and type."""
    return param.split("=")[0].strip()


def _split_positional_and_kwonly(params: list[str]) -> tuple[list[str], list[str]]:
    """Split params into positional and keyword-only (after bare *)."""
    positional: list[str] = []
    kwonly: list[str] = []
    seen_star = False
    for p in params:
        if p == "*":
            seen_star = True
            continue
        if seen_star:
            kwonly.append(p)
        else:
            positional.append(p)
    return positional, kwonly


def _param_default_value(param: str) -> str | None:
    """Extract default value from a parameter, or None if no default."""
    if "=" not in param:
        return None
    return param.split("=", 1)[1].strip()


def is_default_value_change(old_signature: str, new_signature: str) -> bool:
    """Check if the ONLY difference is a default value change on existing params."""
    old_params = extract_params(old_signature)
    new_params = extract_params(new_signature)
    if len(old_params) != len(new_params):
        return False
    for old_p, new_p in zip(old_params, new_params, strict=False):
        if _strip_default(old_p) != _strip_default(new_p):
            return False
        old_def = _param_default_value(old_p)
        new_def = _param_default_value(new_p)
        if old_def != new_def and old_def is not None and new_def is not None:
            return True
    return False


def classify_signature_change(old_signature: str, new_signature: str) -> str:
    """Return a specific category label for a signature change.

    Returns one of:
        "PARAMETER REMOVED"
        "PARAMETER ADDED (BREAKING)"
        "RETURN TYPE CHANGED"
        "DEFAULT VALUE CHANGED"
        "BREAKING SIGNATURE CHANGE"
        "SIGNATURE CHANGED"
    """
    if old_signature == new_signature:
        return "SIGNATURE CHANGED"

    old_params = extract_params(old_signature)
    new_params = extract_params(new_signature)
    old_pos, old_kw = _split_positional_and_kwonly(old_params)
    new_pos, new_kw = _split_positional_and_kwonly(new_params)

    # Positional parameter removed
    if len(new_pos) < len(old_pos):
        return "PARAMETER REMOVED"

    # Keyword-only parameter removed
    old_kw_names = {_strip_default(k).split(":")[0].strip() for k in old_kw}
    new_kw_names = {_strip_default(k).split(":")[0].strip() for k in new_kw}
    if old_kw_names - new_kw_names:
        return "PARAMETER REMOVED"

    # New positional params without defaults
    if len(new_pos) > len(old_pos):
        added = new_pos[len(old_pos) :]
        if any(not _param_has_default(p) for p in added):
            return "PARAMETER ADDED (BREAKING)"

    # New keyword-only params without defaults
    old_kw_map = {_strip_default(k).split(":")[0].strip(): k for k in old_kw}
    for new_k in new_kw:
        name = _strip_default(new_k).split(":")[0].strip()
        if name not in old_kw_map and not _param_has_default(new_k):
            return "PARAMETER ADDED (BREAKING)"

    # Default value change check
    if is_default_value_change(old_signature, new_signature):
        return "DEFAULT VALUE CHANGED"

    # Return type change
    old_ret = _extract_return_type(old_signature)
    new_ret = _extract_return_type(new_signature)
    if old_ret != new_ret and old_ret is not None and new_ret is not None:
        return "RETURN TYPE CHANGED"

    # Check for other breaking changes (type changes on existing params, etc.)
    if is_breaking_change(old_signature, new_signature):
        return "BREAKING SIGNATURE CHANGE"

    return "SIGNATURE CHANGED"


def is_breaking_change(old_signature: str, new_signature: str) -> bool:
    """Determine if a signature change is breaking.

    Breaking = parameters removed, reordered, type changed, default value changed,
               new positional arg without default.
    Non-breaking = new keyword-only args with defaults (after *).
    Conservative: if unsure, return False.
    """
    if old_signature == new_signature:
        return False

    old_params = extract_params(old_signature)
    new_params = extract_params(new_signature)

    # Split into positional and keyword-only
    old_pos, old_kw = _split_positional_and_kwonly(old_params)
    new_pos, new_kw = _split_positional_and_kwonly(new_params)

    # Positional parameter removed → breaking
    if len(new_pos) < len(old_pos):
        return True

    # Check existing positional params changed (name/type or default value)
    for old_p, new_p in zip(old_pos, new_pos, strict=False):
        # Name/type changed
        if _strip_default(old_p) != _strip_default(new_p):
            return True
        # Default value changed on existing param
        old_def = _param_default_value(old_p)
        new_def = _param_default_value(new_p)
        if old_def != new_def and old_def is not None and new_def is not None:
            return True

    # New positional params added — breaking only if they lack defaults
    if len(new_pos) > len(old_pos):
        added = new_pos[len(old_pos) :]
        if any(not _param_has_default(p) for p in added):
            return True

    # Check existing keyword-only params changed
    old_kw_map = {_strip_default(k).split(":")[0].strip(): k for k in old_kw}
    for new_k in new_kw:
        name = _strip_default(new_k).split(":")[0].strip()
        if name in old_kw_map:
            old_k = old_kw_map[name]
            if _strip_default(old_k) != _strip_default(new_k):
                return True
            old_def = _param_default_value(old_k)
            new_def = _param_default_value(new_k)
            if old_def != new_def and old_def is not None and new_def is not None:
                return True

    # Existing keyword-only param removed → breaking
    new_kw_names = {_strip_default(k).split(":")[0].strip() for k in new_kw}
    for name in old_kw_map:
        if name not in new_kw_names:
            return True

    # New keyword-only params without defaults → breaking
    for new_k in new_kw:
        name = _strip_default(new_k).split(":")[0].strip()
        if name not in old_kw_map and not _param_has_default(new_k):
            return True

    # Return type change
    old_ret = _extract_return_type(old_signature)
    new_ret = _extract_return_type(new_signature)
    if old_ret != new_ret and old_ret is not None and new_ret is not None:
        return True

    return False
