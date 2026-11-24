"""Evaluation utilities for versioned evidence-verification datasets.

This module measures supplied predictions; it does not perform semantic inference
or convert model scores into calibrated probabilities.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

LABELS = ("supported", "contradicted", "unknown")


def _metrics(
    cases: Sequence[Mapping[str, Any]],
    predictions: Mapping[str, Mapping[str, Any]],
    *,
    bins: int,
) -> dict[str, Any]:
    confusion = {gold: {pred: 0 for pred in LABELS} for gold in LABELS}
    correct = 0
    calibration_bins: list[dict[str, int | float]] = [
        {"count": 0, "confidence_sum": 0.0, "accuracy_sum": 0}
        for _ in range(bins)
    ]
    for case in cases:
        case_id = str(case["id"])
        gold = str(case["gold_label"])
        prediction = predictions[case_id]
        predicted = str(prediction["label"])
        confidence = float(prediction["confidence"])
        confusion[gold][predicted] += 1
        matched = gold == predicted
        correct += int(matched)
        index = min(int(confidence * bins), bins - 1)
        bucket = calibration_bins[index]
        bucket["count"] += 1
        bucket["confidence_sum"] += confidence
        bucket["accuracy_sum"] += int(matched)

    total = len(cases)
    per_label: dict[str, dict[str, float]] = {}
    for label in LABELS:
        tp = confusion[label][label]
        fp = sum(confusion[other][label] for other in LABELS if other != label)
        fn = sum(confusion[label][other] for other in LABELS if other != label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_label[label] = {"precision": precision, "recall": recall, "f1": f1}

    ece = 0.0
    rendered_bins: list[dict[str, int | float]] = []
    for bucket in calibration_bins:
        count = bucket["count"]
        if count:
            mean_confidence = bucket["confidence_sum"] / count
            accuracy = bucket["accuracy_sum"] / count
            ece += count / total * abs(mean_confidence - accuracy)
        else:
            mean_confidence = 0.0
            accuracy = 0.0
        rendered_bins.append({
            "count": count,
            "mean_confidence": mean_confidence,
            "accuracy": accuracy,
        })

    return {
        "count": total,
        "accuracy": correct / total if total else 0.0,
        "macro_precision": sum(item["precision"] for item in per_label.values()) / len(LABELS),
        "macro_recall": sum(item["recall"] for item in per_label.values()) / len(LABELS),
        "macro_f1": sum(item["f1"] for item in per_label.values()) / len(LABELS),
        "expected_calibration_error": ece,
        "per_label": per_label,
        "confusion_matrix": confusion,
        "confidence_bins": rendered_bins,
    }


def evaluate_predictions(
    cases: Sequence[Mapping[str, Any]],
    predictions: Mapping[str, Mapping[str, Any]],
    *,
    bins: int = 10,
) -> dict[str, Any]:
    """Evaluate exact, one-to-one predictions and report language/category slices.

    Confidence is treated as the predicted class confidence, not as a calibrated
    probability. ECE is descriptive only and must not be used as a release gate
    without a reviewed, representative holdout corpus.
    """
    if bins < 1:
        raise ValueError("bins must be positive")
    if not cases:
        raise ValueError("evaluation corpus must not be empty")

    ids = [str(case["id"]) for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("evaluation corpus contains duplicate case IDs")
    expected = set(ids)
    actual = set(predictions)
    if expected != actual:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f"prediction IDs mismatch; missing={missing}, extra={extra}")

    for case in cases:
        if case.get("gold_label") not in LABELS:
            raise ValueError(f"invalid gold label for case {case['id']}")
        prediction = predictions[str(case["id"])]
        if prediction.get("label") not in LABELS:
            raise ValueError(f"invalid predicted label for case {case['id']}")
        try:
            confidence = float(prediction["confidence"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid confidence for case {case['id']}") from exc
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"confidence out of range for case {case['id']}")

    languages: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    categories: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for case in cases:
        languages[str(case["language"])].append(case)
        categories[str(case["category"])].append(case)

    return {
        "overall": _metrics(cases, predictions, bins=bins),
        "per_language": {
            key: _metrics(group, predictions, bins=bins)
            for key, group in sorted(languages.items())
        },
        "per_category": {
            key: _metrics(group, predictions, bins=bins)
            for key, group in sorted(categories.items())
        },
    }
