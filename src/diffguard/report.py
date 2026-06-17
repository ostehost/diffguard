"""Presentation layer — render findings as text or JSON.

Pure formatting: takes the :class:`~diffguard.engine.findings.Finding` objects
produced by the domain layer and turns them into the strings the CLI prints.
No git access, no trigger logic, no analysis.
"""

from __future__ import annotations

import json
import re

from diffguard.engine.deps import Reference
from diffguard.engine.findings import Finding
from diffguard.schema import DiffGuardOutput, SymbolChange

_VERSION = "0.2.0"
_CALLER_LIST_CAP = 5
_JSON_CALLER_CAP = 10

_REVIEW_HINTS: dict[str, str] = {
    "PARAMETER REMOVED": "These callers will break — removed parameter no longer accepted",
    "PARAMETER ADDED (BREAKING)": "These callers will break — missing required argument",
    "RETURN TYPE CHANGED": "Callers depending on the return type may break",
    "DEFAULT VALUE CHANGED": "Verify callers expect the new default value",
    "BREAKING SIGNATURE CHANGE": "Check all callers handle the new signature",
    "SIGNATURE CHANGED": "Review the signature change for compatibility",
    "SYMBOL REMOVED": "Ensure no remaining callers depend on this symbol",
    "SYMBOL MOVED": "Update imports in dependent files",
}


def review_hint(category: str) -> str:
    """Return a one-line review hint for a finding category."""
    return _REVIEW_HINTS.get(category, "Review this change")


# ---------------------------------------------------------------------------
# Signature display
# ---------------------------------------------------------------------------


def _compact_sig(sig: str) -> str:
    """Extract just the def/class line and collapse it to one line."""
    for line in sig.split("\n"):
        stripped = line.strip()
        if stripped.startswith(("def ", "class ", "func ", "function ")):
            # If multi-line params, collapse them
            if "(" in stripped and ")" not in stripped:
                start = sig.index(stripped)
                rest = sig[start:]
                paren_depth = 0
                result_chars = []
                for ch in rest:
                    if ch == "(":
                        paren_depth += 1
                    elif ch == ")":
                        paren_depth -= 1
                    if ch == "\n":
                        ch = " "
                    result_chars.append(ch)
                    if paren_depth == 0 and ch == ")":
                        remaining = rest[len("".join(result_chars)) :]
                        arrow = remaining.split("\n")[0].strip()
                        if arrow.startswith("->"):
                            result_chars.append(f" {arrow}")
                        break
                return " ".join("".join(result_chars).split())
            return stripped
    # Fallback: collapse whole thing
    return " ".join(sig.split())


def _strip_keyword(sig: str) -> str:
    """Strip leading def/class/func keyword and return-type annotation."""
    for kw in ("def ", "class ", "func ", "function "):
        if sig.startswith(kw):
            sig = sig[len(kw) :]
            break
    sig = re.sub(r"\)\s*->.*$", ")", sig)
    return sig.rstrip(":")


def signature_display(sc: SymbolChange) -> str:
    """Format a symbol change's signature, compact and one-line."""
    if sc.before_signature and sc.after_signature:
        before = _strip_keyword(_compact_sig(sc.before_signature))
        after = _strip_keyword(_compact_sig(sc.after_signature))
        return f"{before} → {after}"
    if sc.signature:
        return _strip_keyword(_compact_sig(sc.signature))
    return f"`{sc.name}`"


# ---------------------------------------------------------------------------
# Text report
# ---------------------------------------------------------------------------


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n != 1 else ''}"


def _callers_by_file(refs: list[Reference]) -> list[str]:
    """Group call references by filename into '<file> (N calls)' parts."""
    by_file: dict[str, int] = {}
    for r in refs:
        fname = r.file_path.rsplit("/", 1)[-1]
        by_file[fname] = by_file.get(fname, 0) + 1
    return [f"{f} ({_plural(n, 'call')})" for f, n in by_file.items()]


def _caller_lines(refs: list[Reference]) -> list[str]:
    """Format the first few callers as indented 'file:line  `source`' lines."""
    lines = []
    for r in refs[:_CALLER_LIST_CAP]:
        short_path = r.file_path.rsplit("/", 1)[-1]
        lines.append(f"     {short_path}:{r.line}  `{r.source_line}`")
    return lines


def _impact_lines(f: Finding) -> list[str]:
    """Build the 'Impact' / 'Callers' lines for one finding."""
    sc = f.change
    prod = f.prod_callers
    lines: list[str] = []

    if sc.breaking:
        if prod:
            lines.append(f"   Impact: {_plural(len(prod), 'caller')} rely on the default:")
            lines.extend(_caller_lines(prod))
        else:
            lines.append("   Impact: Breaking change")
    elif sc.before_signature and sc.after_signature:
        lines.append("   Impact: Backward-compatible (new kwarg has default)")
        if prod:
            lines.append(f"   Callers: {', '.join(_callers_by_file(prod))}")
    elif sc.kind.endswith("_removed"):
        if prod:
            lines.append(f"   Impact: {_plural(len(prod), 'caller')} will break:")
            lines.extend(_caller_lines(prod))
        else:
            lines.append("   Impact: Symbol removed")

    return lines


def render_text(findings: list[Finding]) -> str:
    """Render findings as an actionable, human-readable review block."""
    if not findings:
        return ""

    n = len(findings)
    lines: list[str] = [
        f"⚠ DiffGuard: {n} change{'s' if n != 1 else ''} need{'s' if n == 1 else ''} review",
        "",
    ]

    for idx, f in enumerate(findings, 1):
        sc = f.change
        line_ref = f":{sc.line}" if sc.line else ""
        lines.append(f"{idx}. {f.category}: {signature_display(sc)}")
        lines.append(f"   File: {f.path}{line_ref}")
        lines.extend(_impact_lines(f))
        if f.test_callers:
            lines.append(f"   Callers: {', '.join(_callers_by_file(f.test_callers))}")
        lines.append(f"   Review: {review_hint(f.category)}")
        lines.append("")

    return "\n".join(lines).rstrip()


# ---------------------------------------------------------------------------
# JSON report
# ---------------------------------------------------------------------------


def _finding_json(f: Finding) -> dict[str, object]:
    sc = f.change
    callers = [
        {"file": r.file_path, "line": r.line, "source": r.source_line}
        for r in (f.prod_callers + f.test_callers)[:_JSON_CALLER_CAP]
    ]

    finding: dict[str, object] = {
        "category": f.category.replace(" ", "_"),
        "symbol": sc.name,
        "file": f.path,
        "line": sc.line,
    }
    if sc.before_signature:
        finding["before_signature"] = sc.before_signature.strip()
    if sc.after_signature:
        finding["after_signature"] = sc.after_signature.strip()
    finding["impact"] = {
        "production_callers": len(f.prod_callers),
        "test_callers": len(f.test_callers),
        "callers": callers,
    }
    finding["review_hint"] = review_hint(f.category)
    return finding


def render_json(
    output: DiffGuardOutput,
    ref_range: str,
    findings: list[Finding],
) -> str:
    """Render findings as the structured JSON contract for the review command."""
    symbols_changed = sum(len(fc.changes) for fc in output.files)
    parse_errors = sum(1 for fc in output.files if fc.parse_error)
    result = {
        "version": _VERSION,
        "ref_range": ref_range,
        "findings": [_finding_json(f) for f in findings],
        "stats": {
            "files_analyzed": len(output.files),
            "symbols_changed": symbols_changed,
            "parse_errors": parse_errors,
            "silence_reason": None if findings else "no high-signal changes",
        },
    }
    return json.dumps(result, indent=2)


def render_empty_json(ref_range: str, silence_reason: str) -> str:
    """Render the JSON emitted when there is nothing to analyze."""
    result = {
        "version": _VERSION,
        "ref_range": ref_range,
        "findings": [],
        "stats": {
            "files_analyzed": 0,
            "symbols_changed": 0,
            "parse_errors": 0,
            "silence_reason": silence_reason,
        },
    }
    return json.dumps(result, indent=2)
