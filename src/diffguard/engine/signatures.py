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


# ---------------------------------------------------------------------------
# Shared parameter-diff predicates
#
# classify_signature_change (label) and is_breaking_change (bool) ask the same
# questions about how the parameter lists differ. These helpers hold each
# question once so the two public functions stay declarative and cannot drift.
# ---------------------------------------------------------------------------


def _kw_name(param: str) -> str:
    """Bare name of a keyword-only parameter (strip type annotation and default)."""
    return _strip_default(param).split(":")[0].strip()


def _positional_removed(old_pos: list[str], new_pos: list[str]) -> bool:
    """A positional parameter was dropped."""
    return len(new_pos) < len(old_pos)


def _positional_added_without_default(old_pos: list[str], new_pos: list[str]) -> bool:
    """A new positional parameter was added without a default value."""
    if len(new_pos) <= len(old_pos):
        return False
    added = new_pos[len(old_pos) :]
    return any(not _param_has_default(p) for p in added)


def _kwonly_removed(old_kw: list[str], new_kw: list[str]) -> bool:
    """An existing keyword-only parameter was removed."""
    return bool({_kw_name(k) for k in old_kw} - {_kw_name(k) for k in new_kw})


def _kwonly_added_without_default(old_kw: list[str], new_kw: list[str]) -> bool:
    """A new keyword-only parameter was added without a default value."""
    old_names = {_kw_name(k) for k in old_kw}
    return any(_kw_name(k) not in old_names and not _param_has_default(k) for k in new_kw)


def _existing_positional_changed(old_pos: list[str], new_pos: list[str]) -> bool:
    """An existing positional parameter changed name/type or default value."""
    for old_p, new_p in zip(old_pos, new_pos, strict=False):
        if _strip_default(old_p) != _strip_default(new_p):
            return True
        old_def = _param_default_value(old_p)
        new_def = _param_default_value(new_p)
        if old_def != new_def and old_def is not None and new_def is not None:
            return True
    return False


def _existing_kwonly_changed(old_kw: list[str], new_kw: list[str]) -> bool:
    """An existing keyword-only parameter changed name/type or default value."""
    old_kw_map = {_kw_name(k): k for k in old_kw}
    for new_k in new_kw:
        old_k = old_kw_map.get(_kw_name(new_k))
        if old_k is None:
            continue
        if _strip_default(old_k) != _strip_default(new_k):
            return True
        old_def = _param_default_value(old_k)
        new_def = _param_default_value(new_k)
        if old_def != new_def and old_def is not None and new_def is not None:
            return True
    return False


def _return_type_changed(old_signature: str, new_signature: str) -> bool:
    """Return type annotation changed, with both sides annotated."""
    old_ret = _extract_return_type(old_signature)
    new_ret = _extract_return_type(new_signature)
    return old_ret != new_ret and old_ret is not None and new_ret is not None


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

    old_pos, old_kw = _split_positional_and_kwonly(extract_params(old_signature))
    new_pos, new_kw = _split_positional_and_kwonly(extract_params(new_signature))

    if _positional_removed(old_pos, new_pos) or _kwonly_removed(old_kw, new_kw):
        return "PARAMETER REMOVED"
    if _positional_added_without_default(old_pos, new_pos) or _kwonly_added_without_default(
        old_kw, new_kw
    ):
        return "PARAMETER ADDED (BREAKING)"
    if is_default_value_change(old_signature, new_signature):
        return "DEFAULT VALUE CHANGED"
    if _return_type_changed(old_signature, new_signature):
        return "RETURN TYPE CHANGED"
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

    old_pos, old_kw = _split_positional_and_kwonly(extract_params(old_signature))
    new_pos, new_kw = _split_positional_and_kwonly(extract_params(new_signature))

    return (
        _positional_removed(old_pos, new_pos)
        or _existing_positional_changed(old_pos, new_pos)
        or _positional_added_without_default(old_pos, new_pos)
        or _existing_kwonly_changed(old_kw, new_kw)
        or _kwonly_removed(old_kw, new_kw)
        or _kwonly_added_without_default(old_kw, new_kw)
        or _return_type_changed(old_signature, new_signature)
    )
