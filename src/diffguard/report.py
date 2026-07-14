"""Presentation layer — render findings as text or JSON.

Pure formatting: takes the :class:`~diffguard.engine.findings.Finding` objects
produced by the domain layer and turns them into the strings the CLI prints.
No git access, no trigger logic, no analysis.
"""

from __future__ import annotations

import json
import re
from typing import Any

from diffguard.engine._types import Reference
from diffguard.engine.findings import Finding
from diffguard.engine.summarizer import build_summary, build_tiered_summary
from diffguard.schema import (
    DiffStats,
    DiffGuardOutput,
    FileChange,
    Meta,
    ReviewEnvelope,
    ReviewError,
    ReviewEvidence,
    ReviewFinding,
    ReviewMode,
    ReviewReference,
    ReviewStats,
    ReviewWarning,
    SymbolChange,
)

_REFERENCE_LIST_CAP = 5
_JSON_REFERENCE_CAP = 20
_TERMINAL_ESCAPES: dict[str, str] = {
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}

_PATH_WARNING_SUFFIXES: tuple[tuple[str, str], ...] = (
    (": content unavailable at ref — symbol analysis skipped", "analysis_gap"),
    (": parse gap — symbol analysis skipped", "parse_gap"),
    (": reference scan has a parse gap", "parse_gap"),
)
_UNREADABLE_REFERENCE_DELIMITER = ": reference candidate at snapshot "
_UNREADABLE_REFERENCE_SUFFIX = " is unreadable — reference analysis incomplete"

_REVIEW_HINTS: dict[str, str] = {
    "PARAMETER REMOVED": "Review calls that still pass the removed parameter",
    "PARAMETER ADDED (REQUIRED)": "Update calls that do not provide the required parameter",
    "PARAMETER ADDED": "Check language-specific compatibility with the new parameter",
    "PARAMETERS REORDERED": "Review positional call sites for changed argument meaning",
    "PARAMETERS CHANGED": "Run the language compiler/type checker for compatibility",
    "PARAMETER RENAMED": "Review keyword calls; positional calls may remain compatible",
    "PARAMETER ANNOTATION CHANGED": "Run the configured type checker; syntax alone is not proof",
    "RETURN ANNOTATION CHANGED": "Run the configured type checker; syntax alone is not proof",
    "RETURN TYPE CHANGED": "Run the language compiler/type checker for compatibility",
    "DEFAULT REMOVED": "Update calls that omit the now-required parameter",
    "DEFAULT ADDED": "Verify the new omitted-argument behavior is intended",
    "DEFAULT VALUE CHANGED": "Verify omitted-argument behavior against requirements",
    "OPTIONAL PARAMETER ADDED": "Verify the expanded call shape is intended",
    "SIGNATURE CHANGED": "Review compatibility; bounded syntax rules were inconclusive",
    "SYMBOL REMOVED": "Check unresolved references and public API expectations",
    "POSSIBLE SYMBOL MOVE": "Confirm identity, then review import/export behavior",
}


def review_hint(category: str) -> str:
    """Return a one-line review hint for a finding category."""
    return _REVIEW_HINTS.get(category, "Review this change")


def terminal_safe_text(value: str) -> str:
    """Render one untrusted field without terminal or line-control effects.

    Source text, symbol names, Git paths, and analysis evidence can all be
    repository-controlled. Human output must keep those values on the line
    chosen by the renderer and must not pass ANSI/OSC, C0/C1, bidi, or lone
    surrogate controls through to a terminal or CI log. Printable Unicode is
    preserved; non-printable code points use deterministic visible escapes.

    This is a presentation-only boundary. Structured JSON continues to carry
    the original model values (apart from its established surrogate display
    conversion), and callers must not use this result as an operational path.
    """
    chars: list[str] = []
    for char in value:
        escaped = _TERMINAL_ESCAPES.get(char)
        if escaped is not None:
            chars.append(escaped)
            continue

        codepoint = ord(char)
        if 0xDC80 <= codepoint <= 0xDCFF:
            chars.append(f"\\x{codepoint - 0xDC00:02x}")
        elif char.isprintable():
            chars.append(char)
        elif codepoint <= 0xFF:
            chars.append(f"\\x{codepoint:02x}")
        elif codepoint <= 0xFFFF:
            chars.append(f"\\u{codepoint:04x}")
        else:
            chars.append(f"\\U{codepoint:08x}")
    return "".join(chars)


def _terminal_safe_value(value: Any) -> Any:
    """Recursively copy model data through the human-display boundary."""
    if isinstance(value, str):
        return terminal_safe_text(value)
    if isinstance(value, list):
        return [_terminal_safe_value(item) for item in value]
    if isinstance(value, tuple):
        return [_terminal_safe_value(item) for item in value]
    if isinstance(value, dict):
        return {
            terminal_safe_text(key) if isinstance(key, str) else key: _terminal_safe_value(item)
            for key, item in value.items()
        }
    return value


def render_summary_text(
    output: DiffGuardOutput,
    tier: str,
    *,
    include_tests: bool = False,
    show_skipped: bool = False,
) -> str:
    """Rebuild a human summary from a presentation-safe copy of raw data.

    Sanitizing the already-rendered tier would be too late: a newline supplied
    by a path or symbol would be indistinguishable from layout newlines. The
    structured model remains untouched for JSON serialization.
    """
    files = [
        FileChange.model_validate(_terminal_safe_value(file.model_dump(mode="python")))
        for file in output.files
    ]
    tiered = build_tiered_summary(
        files,
        build_summary(files),
        include_tests=include_tests,
        show_skipped=show_skipped,
    )
    if tier == "oneliner":
        return tiered.oneliner
    if tier == "short":
        return tiered.short
    if tier == "detailed":
        return tiered.detailed
    raise ValueError(f"Unknown summary tier: {terminal_safe_text(tier)}")


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


def _strip_keyword(sig: str, *, strip_return: bool = True) -> str:
    """Strip a leading declaration keyword and optionally its return annotation."""
    for kw in ("def ", "class ", "func ", "function "):
        if sig.startswith(kw):
            sig = sig[len(kw) :]
            break
    if strip_return:
        sig = re.sub(r"\)\s*->.*$", ")", sig)
    return sig.rstrip(":")


def signature_display(sc: SymbolChange) -> str:
    """Format a symbol change's signature, compact and one-line."""
    if sc.before_signature and sc.after_signature:
        # Before/after evidence must retain return annotations: for a pure
        # annotation edit, stripping them renders two identical signatures.
        before = _strip_keyword(_compact_sig(sc.before_signature), strip_return=False)
        after = _strip_keyword(_compact_sig(sc.after_signature), strip_return=False)
        return f"{terminal_safe_text(before)} → {terminal_safe_text(after)}"
    if sc.signature:
        return terminal_safe_text(_strip_keyword(_compact_sig(sc.signature)))
    return f"`{terminal_safe_text(sc.name)}`"


# ---------------------------------------------------------------------------
# Text report
# ---------------------------------------------------------------------------


def _plural(n: int, word: str) -> str:
    if n == 1:
        return f"{n} {word}"
    suffix = "es" if word.endswith(("s", "x", "ch", "sh")) else "s"
    return f"{n} {word}{suffix}"


def _references_by_file(refs: list[Reference]) -> list[str]:
    """Group syntactic references by filename."""
    by_file: dict[str, int] = {}
    for r in refs:
        fname = r.file_path.rsplit("/", 1)[-1]
        by_file[fname] = by_file.get(fname, 0) + 1
    return [f"{terminal_safe_text(f)} ({_plural(n, 'reference')})" for f, n in by_file.items()]


def _reference_lines(refs: list[Reference]) -> list[str]:
    """Format bounded syntactic-reference evidence."""
    lines = []
    for r in refs[:_REFERENCE_LIST_CAP]:
        short_path = r.file_path.rsplit("/", 1)[-1]
        lines.append(
            f"     {terminal_safe_text(short_path)}:{r.line} "
            f"[{terminal_safe_text(r.context)}] `{terminal_safe_text(r.source_line)}`"
        )
    return lines


def _impact_lines(f: Finding) -> list[str]:
    """Build compatibility and syntactic-reference lines for one finding."""
    sc = f.change
    prod = f.prod_references
    test = f.test_references
    references = prod + test
    lines: list[str] = []

    if sc.breaking is True:
        lines.append("   Compatibility: bounded syntax rule indicates a breaking call shape")
    elif sc.breaking is False:
        lines.append("   Compatibility: no breaking call shape detected by the bounded rule")
    else:
        lines.append("   Compatibility: unknown — compiler/type resolution not performed")
    if references:
        lines.append(
            f"   Syntactic references: {_plural(len(references), 'name match')} "
            "(ownership unresolved):"
        )
        lines.extend(_reference_lines(references))
    lines.extend(f"   Gap: {terminal_safe_text(gap)}" for gap in sc.analysis_gaps)

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
        lines.append(f"{idx}. {terminal_safe_text(f.category)}: {signature_display(sc)}")
        lines.append(f"   File: {terminal_safe_text(f.path)}{line_ref}")
        lines.extend(_impact_lines(f))
        if f.test_references:
            lines.append(f"   Test evidence: {', '.join(_references_by_file(f.test_references))}")
        lines.append(f"   Review: {review_hint(f.category)}")
        lines.append("")

    return "\n".join(lines).rstrip()


# ---------------------------------------------------------------------------
# JSON report
# ---------------------------------------------------------------------------


def _json_safe_text(value: str) -> str:
    """Return a deterministic display value with no lone Unicode surrogates.

    Git paths are decoded with ``surrogateescape`` so an undecodable byte in
    the range 0x80-0xff is represented internally as U+DC80-U+DCFF. JSON
    cannot carry those code points safely. Render them as ``\\xNN`` without
    mutating the source model; use ``\\uNNNN`` for any other lone surrogate.
    This text is display-only and may match a literal ``\\xNN`` filename, so
    consumers must not treat it as an operational path identifier.
    """
    chars: list[str] = []
    for char in value:
        codepoint = ord(char)
        if 0xDC80 <= codepoint <= 0xDCFF:
            chars.append(f"\\x{codepoint - 0xDC00:02x}")
        elif 0xD800 <= codepoint <= 0xDFFF:
            chars.append(f"\\u{codepoint:04x}")
        else:
            chars.append(char)
    return "".join(chars)


def _json_safe_value(value: Any) -> Any:
    """Recursively convert string values and keys to JSON-safe display text."""
    if isinstance(value, str):
        return _json_safe_text(value)
    if isinstance(value, list):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, dict):
        return {
            _json_safe_text(key) if isinstance(key, str) else key: _json_safe_value(item)
            for key, item in value.items()
        }
    return value


def _serialize_json(model: DiffGuardOutput | ReviewEnvelope) -> str:
    """Serialize a validated model after display-only surrogate conversion."""
    data = _json_safe_value(model.model_dump(mode="python"))
    safe_model: DiffGuardOutput | ReviewEnvelope
    if isinstance(model, DiffGuardOutput):
        safe_model = DiffGuardOutput.model_validate(data)
    else:
        safe_model = ReviewEnvelope.model_validate(data)
    # Pydantic 2.0 does not accept ``ensure_ascii`` on ``model_dump_json``.
    # Let Pydantic apply the model's established JSON encoders first (including
    # its non-finite-float handling), then make the serialized representation
    # safe to relay through terminals and CI logs. Parsing restores the exact
    # Unicode values, while bidi/C1 controls cannot affect the JSON bytes as
    # displayed.
    encoded = json.loads(safe_model.model_dump_json())
    return json.dumps(encoded, ensure_ascii=True, indent=2, allow_nan=False)


def render_summary_json(output: DiffGuardOutput) -> str:
    """Render summarize JSON without exposing filesystem surrogate code points."""
    return _serialize_json(output)


def render_empty_summary_json(ref_range: str) -> str:
    """Render a schema-valid summarize result for an empty diff."""
    return _serialize_json(
        DiffGuardOutput(
            meta=Meta(
                ref_range=ref_range,
                stats=DiffStats(files=0, additions=0, deletions=0),
            )
        )
    )


def _finding_model(f: Finding) -> ReviewFinding:
    sc = f.change
    references = [
        ReviewReference(
            file=ref.file_path,
            line=ref.line,
            symbol=ref.symbol_name,
            kind=ref.context,
            source=ref.source_line,
            confidence=ref.confidence,
            evidence=ref.evidence,
        )
        for ref in (f.prod_references + f.test_references)[:_JSON_REFERENCE_CAP]
    ]
    evidence = [ReviewEvidence(kind="syntax", message=item) for item in sc.evidence]
    evidence.extend(ReviewEvidence(kind="analysis_gap", message=item) for item in sc.analysis_gaps)
    return ReviewFinding(
        rule_id=sc.rule_id or "DG000",
        category_id=sc.category_id or "changed",
        category=f.category,
        symbol=sc.name,
        file=f.path,
        source_file=sc.file_from,
        line=sc.line,
        language=f.file.language,
        before_signature=sc.before_signature.strip() if sc.before_signature else None,
        after_signature=sc.after_signature.strip() if sc.after_signature else None,
        breaking=sc.breaking,
        confidence=sc.confidence or "low",
        evidence=evidence,
        references=references,
        analysis_gaps=list(sc.analysis_gaps),
        review_hint=review_hint(f.category),
    )


def _envelope(
    ref_range: str,
    mode: ReviewMode,
    findings: list[ReviewFinding],
    warnings: list[ReviewWarning],
    *,
    files_analyzed: int,
    symbols_changed: int,
    parse_errors: int,
    reference_count: int,
    silence_reason: str | None,
    error: ReviewError | None = None,
) -> str:
    """Validate and serialize the single review JSON authority."""
    envelope = ReviewEnvelope(
        status="error" if error else "ok",
        mode=mode,
        ref_range=ref_range,
        findings=findings,
        warnings=warnings,
        stats=ReviewStats(
            files_analyzed=files_analyzed,
            symbols_changed=symbols_changed,
            parse_errors=parse_errors,
            reference_count=reference_count,
            silence_reason=silence_reason,
        ),
        error=error,
    )
    return _serialize_json(envelope)


def _warning_model(message: str) -> ReviewWarning:
    """Convert an engine warning into a structured review warning."""
    for suffix, code in _PATH_WARNING_SUFFIXES:
        if message.endswith(suffix):
            return ReviewWarning(
                code=code,
                message=message,
                file=message[: -len(suffix)],
            )
    if message.endswith(_UNREADABLE_REFERENCE_SUFFIX):
        prefix = message[: -len(_UNREADABLE_REFERENCE_SUFFIX)]
        file, delimiter, snapshot = prefix.rpartition(_UNREADABLE_REFERENCE_DELIMITER)
        if delimiter and file and snapshot:
            return ReviewWarning(
                code="analysis_gap",
                message=message,
                file=file,
            )
    return ReviewWarning(code="analysis_gap", message=message)


def render_json(
    output: DiffGuardOutput,
    ref_range: str,
    mode: ReviewMode,
    findings: list[Finding],
) -> str:
    """Render findings as the structured JSON contract for the review command."""
    return _envelope(
        ref_range,
        mode,
        [_finding_model(f) for f in findings],
        [_warning_model(message) for message in output.meta.warnings],
        files_analyzed=len(output.files),
        symbols_changed=sum(len(fc.changes) for fc in output.files),
        parse_errors=sum(1 for fc in output.files if fc.parse_error),
        reference_count=sum(
            len(finding.prod_references) + len(finding.test_references) for finding in findings
        ),
        silence_reason=None if findings else "no high-signal changes",
    )


def render_empty_json(ref_range: str, mode: ReviewMode, silence_reason: str) -> str:
    """Render the JSON emitted when there is nothing to analyze."""
    return _envelope(
        ref_range,
        mode,
        [],
        [],
        files_analyzed=0,
        symbols_changed=0,
        parse_errors=0,
        reference_count=0,
        silence_reason=silence_reason,
    )


def render_error_json(ref_range: str, mode: ReviewMode, message: str) -> str:
    """Render a schema-valid JSON tool error."""
    return _envelope(
        ref_range,
        mode,
        [],
        [],
        files_analyzed=0,
        symbols_changed=0,
        parse_errors=0,
        reference_count=0,
        silence_reason="tool error",
        error=ReviewError(code="tool_error", message=message),
    )
