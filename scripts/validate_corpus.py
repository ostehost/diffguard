#!/usr/bin/env python3
"""Run the local labeled contract-rule corpus and emit measurable JSON."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from diffguard.engine._types import SignatureComparison
from diffguard.engine.classifier import classify_changes
from diffguard.engine.matcher import match_symbols
from diffguard.engine.parser import parse_file
from diffguard.engine.signatures import compare_signatures

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = ROOT / "tests" / "fixtures" / "corpus" / "contract_cases.json"


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def validate(corpus_path: Path) -> dict[str, Any]:
    """Evaluate expected rule IDs, false positives, misses, and parse gaps."""
    cases: list[dict[str, Any]] = json.loads(corpus_path.read_text(encoding="utf-8"))
    expected_counts: dict[str, int] = defaultdict(int)
    predicted_counts: dict[str, int] = defaultdict(int)
    true_counts: dict[str, int] = defaultdict(int)
    false_counts: dict[str, int] = defaultdict(int)
    missed_counts: dict[str, int] = defaultdict(int)
    findings: list[str] = []
    misses: list[str] = []
    false_positives: list[str] = []
    expected_parse_gaps: list[str] = []
    parse_gaps: list[str] = []
    unexpected_parse_gaps: list[str] = []
    missing_expected_parse_gaps: list[str] = []

    for case in cases:
        case_id = str(case["id"])
        language = str(case["language"])
        expected = case.get("expected_rule_id")
        expected_parse_gap = bool(case.get("expected_parse_gap", False))
        if expected_parse_gap:
            expected_parse_gaps.append(case_id)
        old_result = parse_file(str(case["old"]), language)
        new_result = parse_file(str(case["new"]), language)
        parse_gap = old_result.parse_error or new_result.parse_error
        if parse_gap:
            parse_gaps.append(case_id)
            if not expected_parse_gap:
                unexpected_parse_gaps.append(case_id)
            actual_rules: list[str] = []
        else:
            if expected_parse_gap:
                missing_expected_parse_gaps.append(case_id)
            matches = match_symbols(old_result.symbols, new_result.symbols)

            def compare(old: str, new: str) -> SignatureComparison:
                return compare_signatures(old, new, language)

            changes = classify_changes(
                matches,
                compare,
            )
            actual_rules = [change.rule_id for change in changes if change.rule_id is not None]

        expected_rules = Counter([str(expected)]) if expected is not None else Counter()
        actual_rule_counts = Counter(actual_rules)

        for rule_id, expected_count in expected_rules.items():
            actual_count = actual_rule_counts[rule_id]
            true_count = min(expected_count, actual_count)
            missed_count = expected_count - true_count
            expected_counts[rule_id] += expected_count
            true_counts[rule_id] += true_count
            missed_counts[rule_id] += missed_count
            findings.extend([case_id] * true_count)
            misses.extend([case_id] * missed_count)

        for rule_id, predicted_count in actual_rule_counts.items():
            expected_count = expected_rules[rule_id]
            false_count = max(predicted_count - expected_count, 0)
            predicted_counts[rule_id] += predicted_count
            false_counts[rule_id] += false_count
            false_positives.extend([case_id] * false_count)

    rule_ids = sorted(set(expected_counts) | set(predicted_counts))
    per_rule = {
        rule_id: {
            "expected": expected_counts[rule_id],
            "predicted": predicted_counts[rule_id],
            "true_positive": true_counts[rule_id],
            "false_positive": false_counts[rule_id],
            "miss": missed_counts[rule_id],
            "precision": _ratio(true_counts[rule_id], predicted_counts[rule_id]),
            "recall": _ratio(true_counts[rule_id], expected_counts[rule_id]),
        }
        for rule_id in rule_ids
    }
    true_total = sum(true_counts.values())
    predicted_total = sum(predicted_counts.values())
    expected_total = sum(expected_counts.values())
    return {
        "sample_size": len(cases),
        "labeled_findings": expected_total,
        "true_positive": true_total,
        "false_positive": len(false_positives),
        "misses": len(misses),
        "precision": _ratio(true_total, predicted_total),
        "recall": _ratio(true_total, expected_total),
        "findings": findings,
        "missed_cases": misses,
        "false_positive_cases": false_positives,
        "parse_gap_metrics": {
            "expected": len(expected_parse_gaps),
            "observed": len(parse_gaps),
            "matched": len(expected_parse_gaps) - len(missing_expected_parse_gaps),
            "unexpected": len(unexpected_parse_gaps),
            "missing": len(missing_expected_parse_gaps),
        },
        "expected_parse_gaps": expected_parse_gaps,
        "parse_gaps": parse_gaps,
        "unexpected_parse_gaps": unexpected_parse_gaps,
        "missing_expected_parse_gaps": missing_expected_parse_gaps,
        "per_rule": per_rule,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    result = validate(args.corpus)
    sys.stdout.write(json.dumps(result, indent=2) + "\n")
    if args.check and (
        result["misses"]
        or result["false_positive"]
        or result["unexpected_parse_gaps"]
        or result["missing_expected_parse_gaps"]
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
