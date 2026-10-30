"""Bias-corrected failure prevalence for a monitoring period."""

from __future__ import annotations

from typing import Any, Sequence

import numpy as np


def _rogan_gladen(raw: float, sensitivity: float, specificity: float) -> float | None:
    """Correct a raw flag rate for judge error; None when it cannot be done."""
    denominator = sensitivity + specificity - 1
    # A judge no better than chance (or worse) carries no usable correction.
    if denominator <= 0:
        return None
    return (raw + specificity - 1) / denominator


def _rates(labels: np.ndarray, preds: np.ndarray) -> tuple[float, float] | None:
    """Failure sensitivity and pass specificity; None if a class is absent."""
    failures, passes = labels == 1, labels == 0
    if not failures.any() or not passes.any():
        return None
    return float(preds[failures].mean()), float((preds[passes] == 0).mean())


def corrected_mode_prevalence(
    sample_preds: Sequence[int],
    test_labels: Sequence[int],
    test_preds: Sequence[int],
    confidence: float = 0.95,
    bootstrap_iterations: int = 20000,
    seed: int | None = 7,
) -> dict[str, Any]:
    """Bias-corrected live prevalence for one mode from sampled verdicts.

    The contract, precisely:

      1. ``raw`` is the uncorrected flag rate: ``mean(sample_preds)``.
      2. Compute the frozen judge's failure sensitivity and pass specificity
         from ``test_labels`` and ``test_preds``. Both use the monitoring
         convention that 1 means a failure is present. Failure sensitivity is
         the flagged fraction of human-labeled failures. Pass specificity is
         the unflagged fraction of human-labeled passes.
      3. Compute the Rogan-Gladen point estimate, then resample the held-out
         records and sampled predictions to obtain a percentile-bootstrap
         interval. Use a seeded NumPy generator so the committed result is
         reproducible.
      4. Resample the monitoring predictions and the paired held-out records
         independently with replacement. Keep their original sample sizes.
         Discard a draw if the correction cannot be computed. Clamp each
         retained estimate to [0, 1], then take the percentile interval.
         Raise ``ValueError`` if no replicate is valid.

    Args:
        sample_preds: the judge's 0/1 verdicts over the UNIFORM BASE sample
            only (never the risk strata; they are biased toward failure by
            design).
        test_labels: human labels for the frozen Homework 5 judge's test
            split.
        test_preds: the frozen judge's predictions on that test split.
        confidence: interval confidence level.
        bootstrap_iterations: number of percentile-bootstrap replicates.
        seed: numpy seed for a reproducible interval; None leaves the RNG
            untouched.

    Returns:
        {"raw", "corrected", "ci_low", "ci_high", "confidence",
         "failure_sensitivity", "pass_specificity", "n_sample"}
        with "corrected" clamped to [0, 1] and rates rounded to 4 places.

    Raises:
        ValueError: if an input is empty, the held-out inputs have different
            lengths, a value is not 0 or 1, a class is absent, the judge is
            missing a usable correction, or no bootstrap replicate is valid.
    """
    sample = np.asarray(sample_preds, dtype=int)
    labels = np.asarray(test_labels, dtype=int)
    preds = np.asarray(test_preds, dtype=int)
    if sample.size == 0 or labels.size == 0:
        raise ValueError("sample and held-out inputs must be nonempty")
    if labels.size != preds.size:
        raise ValueError("held-out labels and predictions differ in length")
    for values in (sample, labels, preds):
        if not np.isin(values, (0, 1)).all():
            raise ValueError("verdicts and labels must be 0 or 1")

    rates = _rates(labels, preds)
    if rates is None:
        raise ValueError("held-out labels need both failures and passes")
    sensitivity, specificity = rates
    raw = float(sample.mean())
    point = _rogan_gladen(raw, sensitivity, specificity)
    if point is None:
        raise ValueError("judge is no better than chance; no usable correction")

    # Resample the monitoring verdicts and the held-out pairs independently.
    rng = np.random.default_rng(seed)
    estimates: list[float] = []
    for _ in range(bootstrap_iterations):
        raw_b = float(sample[rng.integers(0, sample.size, sample.size)].mean())
        idx = rng.integers(0, labels.size, labels.size)
        rates_b = _rates(labels[idx], preds[idx])
        if rates_b is None:
            continue
        estimate = _rogan_gladen(raw_b, *rates_b)
        if estimate is not None:
            estimates.append(min(1.0, max(0.0, estimate)))
    if not estimates:
        raise ValueError("no bootstrap replicate produced a usable correction")

    tail = (1 - confidence) / 2 * 100
    ci_low, ci_high = np.percentile(estimates, [tail, 100 - tail])
    return {
        "raw": round(raw, 4),
        "corrected": round(min(1.0, max(0.0, point)), 4),
        "ci_low": round(float(ci_low), 4),
        "ci_high": round(float(ci_high), 4),
        "confidence": confidence,
        "failure_sensitivity": round(sensitivity, 4),
        "pass_specificity": round(specificity, 4),
        "n_sample": int(sample.size),
    }
