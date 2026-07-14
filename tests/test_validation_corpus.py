"""Tests for the reproducible, network-free labeled validation corpus."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.validate_corpus import DEFAULT_CORPUS, main, validate


def test_contract_corpus_has_no_misses_or_false_positives() -> None:
    report = validate(DEFAULT_CORPUS)
    assert report["sample_size"] == 16
    assert report["misses"] == 0
    assert report["false_positive"] == 0
    assert report["parse_gap_metrics"] == {
        "expected": 1,
        "observed": 1,
        "matched": 1,
        "unexpected": 0,
        "missing": 0,
    }
    assert report["expected_parse_gaps"] == ["py-parse-gap"]
    assert report["parse_gaps"] == ["py-parse-gap"]
    assert report["unexpected_parse_gaps"] == []
    assert report["missing_expected_parse_gaps"] == []


def test_duplicate_expected_rule_is_counted_as_false_positive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = tmp_path / "duplicate-rule.json"
    corpus.write_text(
        json.dumps(
            [
                {
                    "id": "duplicate-default-removal",
                    "language": "python",
                    "old": ("def first(a=1):\n    return a\n\ndef second(a=1):\n    return a\n"),
                    "new": ("def first(a):\n    return a\n\ndef second(a):\n    return a\n"),
                    "expected_rule_id": "DG104",
                }
            ]
        ),
        encoding="utf-8",
    )

    report = validate(corpus)

    assert report["true_positive"] == 1
    assert report["false_positive"] == 1
    assert report["precision"] == 0.5
    assert report["false_positive_cases"] == ["duplicate-default-removal"]
    assert report["per_rule"]["DG104"] == {
        "expected": 1,
        "predicted": 2,
        "true_positive": 1,
        "false_positive": 1,
        "miss": 0,
        "precision": 0.5,
        "recall": 1.0,
    }
    monkeypatch.setattr("sys.argv", ["validate_corpus.py", "--corpus", str(corpus), "--check"])
    assert main() == 1


def test_check_still_fails_for_a_missed_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = tmp_path / "missed-rule.json"
    corpus.write_text(
        json.dumps(
            [
                {
                    "id": "missing-default-removal",
                    "language": "python",
                    "old": "def contract(a=1):\n    return a\n",
                    "new": "def contract(a=1):\n    return a\n",
                    "expected_rule_id": "DG104",
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.argv", ["validate_corpus.py", "--corpus", str(corpus), "--check"])

    report = validate(corpus)
    assert report["misses"] == 1
    assert report["missed_cases"] == ["missing-default-removal"]
    assert main() == 1


def test_expected_parse_gap_that_disappears_is_reported(tmp_path: Path) -> None:
    corpus = tmp_path / "missing-parse-gap.json"
    corpus.write_text(
        json.dumps(
            [
                {
                    "id": "expected-gap-is-now-valid",
                    "language": "python",
                    "old": "def contract(a):\n    return a\n",
                    "new": "def contract(a):\n    return a + 1\n",
                    "expected_rule_id": None,
                    "expected_parse_gap": True,
                }
            ]
        ),
        encoding="utf-8",
    )

    report = validate(corpus)

    assert report["parse_gap_metrics"] == {
        "expected": 1,
        "observed": 0,
        "matched": 0,
        "unexpected": 0,
        "missing": 1,
    }
    assert report["expected_parse_gaps"] == ["expected-gap-is-now-valid"]
    assert report["parse_gaps"] == []
    assert report["missing_expected_parse_gaps"] == ["expected-gap-is-now-valid"]


def test_check_fails_when_expected_parse_gap_disappears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = tmp_path / "missing-parse-gap.json"
    corpus.write_text(
        json.dumps(
            [
                {
                    "id": "expected-gap-is-now-valid",
                    "language": "python",
                    "old": "def contract(a):\n    return a\n",
                    "new": "def contract(a):\n    return a\n",
                    "expected_rule_id": None,
                    "expected_parse_gap": True,
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.argv", ["validate_corpus.py", "--corpus", str(corpus), "--check"])

    assert main() == 1


def test_check_accepts_a_matching_expected_parse_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = tmp_path / "matching-parse-gap.json"
    corpus.write_text(
        json.dumps(
            [
                {
                    "id": "expected-gap-still-invalid",
                    "language": "python",
                    "old": "def contract(a):\n    return a\n",
                    "new": "def contract(a:\n    return a\n",
                    "expected_rule_id": None,
                    "expected_parse_gap": True,
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.argv", ["validate_corpus.py", "--corpus", str(corpus), "--check"])

    assert main() == 0


def test_check_still_fails_for_an_unexpected_parse_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = tmp_path / "unexpected-parse-gap.json"
    corpus.write_text(
        json.dumps(
            [
                {
                    "id": "unlabeled-gap",
                    "language": "python",
                    "old": "def contract(a):\n    return a\n",
                    "new": "def contract(a:\n    return a\n",
                    "expected_rule_id": None,
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.argv", ["validate_corpus.py", "--corpus", str(corpus), "--check"])

    report = validate(corpus)
    assert report["parse_gap_metrics"] == {
        "expected": 0,
        "observed": 1,
        "matched": 0,
        "unexpected": 1,
        "missing": 0,
    }
    assert report["unexpected_parse_gaps"] == ["unlabeled-gap"]
    assert main() == 1
