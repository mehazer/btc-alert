"""Tests for measurement primitives and the banded evaluator.

Run from the repository root:
    python -m unittest discover -s tests -p 'test_*.py'
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from intelligence_v2 import metrics
from intelligence_v2.contracts import DecisionRecord, RiskAssessment
from intelligence_v2.evaluation import evaluate_decision


def _series(prices):
    return [float(value) for value in prices]


class NeutralBandTests(unittest.TestCase):
    def test_band_scales_with_square_root_of_horizon(self):
        one = metrics.neutral_band_pct(1.0, 1, 0.5)
        four = metrics.neutral_band_pct(1.0, 4, 0.5)
        self.assertAlmostEqual(one, 0.5, places=6)
        self.assertAlmostEqual(four, 1.0, places=6)

    def test_band_is_none_without_usable_volatility(self):
        self.assertIsNone(metrics.neutral_band_pct(None, 3, 0.5))
        self.assertIsNone(metrics.neutral_band_pct(0.0, 3, 0.5))
        self.assertIsNone(metrics.neutral_band_pct(1.0, 0, 0.5))

    def test_volatility_is_positive_for_moving_series(self):
        sigma = metrics.volatility_pct(_series([100, 101, 99, 102, 100, 101]))
        self.assertIsNotNone(sigma)
        self.assertGreater(sigma, 0)

    def test_volatility_needs_enough_bars(self):
        self.assertIsNone(metrics.volatility_pct(_series([100, 101])))


class LabelTests(unittest.TestCase):
    def test_move_inside_band_is_neutral(self):
        self.assertEqual(metrics.label_return(0.4, 1.0), metrics.NEUTRAL_LABEL)
        self.assertEqual(metrics.label_return(-0.4, 1.0), metrics.NEUTRAL_LABEL)

    def test_move_outside_band_is_directional(self):
        self.assertEqual(metrics.label_return(1.2, 1.0), "up")
        self.assertEqual(metrics.label_return(-1.2, 1.0), "down")

    def test_missing_band_falls_back_to_sign(self):
        self.assertEqual(metrics.label_return(0.01, None), "up")
        self.assertEqual(metrics.label_return(-0.01, None), "down")
        self.assertEqual(metrics.label_return(0.0, None), metrics.NEUTRAL_LABEL)

    def test_no_return_has_no_label(self):
        self.assertIsNone(metrics.label_return(None, 1.0))

    def test_neutral_truth_is_not_scored_against_a_directional_call(self):
        self.assertIsNone(metrics.direction_correct("up", metrics.NEUTRAL_LABEL))
        self.assertTrue(metrics.direction_correct("up", "up"))
        self.assertFalse(metrics.direction_correct("up", "down"))
        self.assertIsNone(metrics.direction_correct("flat", "up"))


class AccuracyTests(unittest.TestCase):
    def test_accuracy_base_rate_and_balanced_accuracy(self):
        labels = ["up", "up", "up", "down", "down"]
        predictions = ["up", "up", "down", "down", "up"]
        stats = metrics.accuracy_stats(labels, predictions)
        self.assertEqual(stats["n"], 5)
        self.assertEqual(stats["hits"], 3)
        self.assertEqual(stats["accuracy_pct"], 60.0)
        self.assertEqual(stats["base_rate_up_pct"], 60.0)
        # recall_up = 2/3, recall_down = 1/2
        self.assertAlmostEqual(stats["recall_up_pct"], 66.67, places=2)
        self.assertEqual(stats["recall_down_pct"], 50.0)
        self.assertAlmostEqual(stats["balanced_accuracy_pct"], 58.33, places=2)

    def test_neutral_truths_are_reported_not_dropped_silently(self):
        stats = metrics.accuracy_stats(["up", metrics.NEUTRAL_LABEL, "down"], ["up", "up", "down"])
        self.assertEqual(stats["directional"], 2)
        self.assertEqual(stats["neutral"], 1)
        self.assertEqual(stats["accuracy_pct"], 100.0)
        self.assertAlmostEqual(stats["coverage_pct"], 66.67, places=2)
        self.assertAlmostEqual(stats["neutral_rate_pct"], 33.33, places=2)

    def test_always_predictions_are_scored_on_the_same_rows(self):
        labels = ["up", "down", "down", "down"]
        self.assertEqual(metrics.always_up_stats(labels)["accuracy_pct"], 25.0)
        self.assertEqual(metrics.always_down_stats(labels)["accuracy_pct"], 75.0)

    def test_skill_score_is_relative_to_the_room_left_by_the_baseline(self):
        self.assertEqual(metrics.skill_score(75.0, 50.0), 50.0)
        self.assertEqual(metrics.skill_score(50.0, 50.0), 0.0)
        self.assertEqual(metrics.skill_score(0.0, 50.0), -100.0)
        self.assertIsNone(metrics.skill_score(None, 50.0))
        self.assertIsNone(metrics.skill_score(50.0, 100.0))


class NaiveBaselineTests(unittest.TestCase):
    def test_persistence_follows_the_last_bar(self):
        self.assertEqual(metrics.naive_directions(_series([100, 101]))["persistence"], "up")
        self.assertEqual(metrics.naive_directions(_series([101, 100]))["persistence"], "down")

    def test_ema_slope_follows_the_average(self):
        rising = metrics.naive_directions(_series(list(range(100, 130))))
        falling = metrics.naive_directions(_series(list(range(130, 100, -1))))
        self.assertEqual(rising["ema_slope"], "up")
        self.assertEqual(falling["ema_slope"], "down")

    def test_short_series_yields_no_prediction(self):
        self.assertIsNone(metrics.naive_directions(_series([100]))["persistence"])


class RegimeTests(unittest.TestCase):
    def test_steady_decline_is_trend_down(self):
        summary = metrics.regime_summary(_series([100 - i * 0.5 for i in range(40)]))
        self.assertEqual(summary["label"], "trend_down")
        self.assertLess(summary["drift_z"], 0)

    def test_steady_rise_is_trend_up(self):
        summary = metrics.regime_summary(_series([100 + i * 0.5 for i in range(40)]))
        self.assertEqual(summary["label"], "trend_up")

    def test_noise_without_drift_is_range(self):
        summary = metrics.regime_summary(_series([100, 101, 99, 100, 101, 99, 100, 101, 99, 100]))
        self.assertEqual(summary["label"], "range")


class ReliabilityTests(unittest.TestCase):
    def test_well_calibrated_probabilities_show_no_gap(self):
        pairs = [(0.6, value < 6) for value in range(10)]
        table = metrics.reliability_table(pairs)
        self.assertEqual(table["n"], 10)
        self.assertAlmostEqual(table["observed_rate_pct"], 60.0, places=2)
        self.assertAlmostEqual(table["ece"], 0.0, places=4)
        self.assertAlmostEqual(table["brier_score"], 0.24, places=4)

    def test_confidence_that_does_not_track_outcomes_is_visible(self):
        pairs = [(0.9, False), (0.9, False), (0.9, False), (0.9, True),
                 (0.1, False), (0.1, False), (0.1, False), (0.1, False)]
        table = metrics.reliability_table(pairs)
        self.assertGreater(table["ece"], 0.3)
        self.assertGreater(table["brier_score"], 0.25)

    def test_empty_input_is_explicit(self):
        table = metrics.reliability_table([])
        self.assertEqual(table["n"], 0)
        self.assertIsNone(table["brier_score"])


def _write_history(directory, symbol, prices, start=None):
    start = start or datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [
        {"date": (start + timedelta(hours=4 * index)).isoformat(), "price": price}
        for index, price in enumerate(prices)
    ]
    with open(os.path.join(directory, f"{symbol}.json"), "w", encoding="utf-8") as handle:
        json.dump(rows, handle)
    return rows


def _decision(symbol, evaluated_at, direction="up"):
    return DecisionRecord(
        agent="decision-synthesizer", run_id="run-1", symbol=symbol, timeframe="4h",
        decision="ALERT", approval_required=True, execution_allowed=False, gate="paper-only",
        thesis="", signal_direction=direction, paper_conditions=[], invalidation=[],
        blocking_reasons=[], contributing_agents=["test"], confidence=60,
        evidence_ids=[], evaluated_at=evaluated_at, model_version="test",
    )


def _risk(symbol):
    return RiskAssessment(
        agent="risk", run_id="run-1", symbol=symbol, timeframe="4h", overall_level="low",
        downside_risk="low", squeeze_risk="low", data_risk="low", veto=False, flags=[],
        stress_scenarios=[], confidence=60, evidence_ids=[], evaluated_at="", model_version="test",
    )


class EvaluatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = self.temp.name
        # Bounded noise, then a final move of about 0.05%: well inside a k=3 band.
        prices = [100 + (0.1 if index % 2 else 0) for index in range(30)] + [100.05]
        self.rows = _write_history(self.dir, "BTCUSDT", prices)
        self.decision_at = self.rows[29]["date"]

    def tearDown(self):
        self.temp.cleanup()

    def test_flat_move_is_neutral_and_not_scored(self):
        record = evaluate_decision(_decision("BTCUSDT", self.decision_at), _risk("BTCUSDT"), self.dir, 1,
                                   neutral_band_k=3.0)
        self.assertEqual(record.status, "settled")
        self.assertIsNotNone(record.neutral_band_pct)
        self.assertEqual(record.outcome_label, metrics.NEUTRAL_LABEL)
        self.assertIsNone(record.direction_correct)
        self.assertTrue(any("neutral band" in note for note in record.notes))

    def test_large_move_is_directional(self):
        prices = [100] * 30 + [104.0]
        self.rows = _write_history(self.dir, "BTCUSDT", prices)
        record = evaluate_decision(_decision("BTCUSDT", self.rows[29]["date"], "up"),
                                   _risk("BTCUSDT"), self.dir, 1, neutral_band_k=0.5)
        self.assertEqual(record.outcome_label, "up")
        self.assertTrue(record.direction_correct)

    def test_wrong_direction_is_recorded_as_wrong(self):
        prices = [100] * 30 + [96.0]
        self.rows = _write_history(self.dir, "BTCUSDT", prices)
        record = evaluate_decision(_decision("BTCUSDT", self.rows[29]["date"], "up"),
                                   _risk("BTCUSDT"), self.dir, 1, neutral_band_k=0.5)
        self.assertEqual(record.outcome_label, "down")
        self.assertFalse(record.direction_correct)

    def test_pending_horizon_is_not_scored(self):
        record = evaluate_decision(_decision("BTCUSDT", self.decision_at), _risk("BTCUSDT"), self.dir, 24)
        self.assertEqual(record.status, "pending")
        self.assertIsNone(record.direction_correct)


if __name__ == "__main__":
    unittest.main()
