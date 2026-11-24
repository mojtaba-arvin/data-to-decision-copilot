import json
from pathlib import Path
from typing import Any

import pytest

from d2d.core.semantic_evaluation import evaluate_predictions


CORPUS_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "semantic_verification"
    / "v1.json"
)


def test_bilingual_corpus_is_versioned_and_explicitly_non_release():
    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    cases = corpus["cases"]
    assert corpus["version"] == "1.0.0"
    assert corpus["status"] == "development_fixture"
    assert corpus["review_status"] == "not_human_reviewed"
    assert corpus["release_eligible"] is False
    assert {case["language"] for case in cases} == {"en", "fa"}
    assert {case["gold_label"] for case in cases} == {
        "supported", "contradicted", "unknown"
    }
    assert {case["split"] for case in cases} == {"train", "dev", "test"}
    assert len({case["id"] for case in cases}) == len(cases)


def test_evaluator_reports_overall_language_category_and_calibration_slices():
    cases = [
        {"id": "en-1", "language": "en", "category": "numeric", "gold_label": "supported"},
        {"id": "fa-1", "language": "fa", "category": "negation", "gold_label": "contradicted"},
        {"id": "fa-2", "language": "fa", "category": "negation", "gold_label": "unknown"},
    ]
    predictions = {
        "en-1": {"label": "supported", "confidence": 0.9},
        "fa-1": {"label": "supported", "confidence": 0.8},
        "fa-2": {"label": "unknown", "confidence": 0.6},
    }

    report = evaluate_predictions(cases, predictions, bins=5)

    assert report["overall"]["count"] == 3
    assert report["overall"]["accuracy"] == pytest.approx(2 / 3)
    assert report["overall"]["confusion_matrix"]["contradicted"]["supported"] == 1
    assert set(report["per_language"]) == {"en", "fa"}
    assert set(report["per_category"]) == {"numeric", "negation"}
    assert report["per_language"]["fa"]["count"] == 2
    assert report["overall"]["expected_calibration_error"] >= 0


@pytest.mark.parametrize(
    ("predictions", "message"),
    [
        ({}, "prediction IDs mismatch"),
        (
            {"x": {"label": "entails", "confidence": 0.9}},
            "invalid predicted label",
        ),
        (
            {"x": {"label": "supported", "confidence": 1.1}},
            "confidence out of range",
        ),
    ],
)
def test_evaluator_rejects_incomplete_or_invalid_predictions(
    predictions: dict[str, dict[str, Any]], message: str
):
    cases = [{"id": "x", "language": "en", "category": "numeric", "gold_label": "supported"}]
    with pytest.raises(ValueError, match=message):
        evaluate_predictions(cases, predictions)


def test_evaluator_rejects_duplicate_case_ids():
    case = {"id": "x", "language": "en", "category": "numeric", "gold_label": "supported"}
    with pytest.raises(ValueError, match="duplicate case IDs"):
        evaluate_predictions(
            [case, case],
            {"x": {"label": "supported", "confidence": 0.9}},
        )
