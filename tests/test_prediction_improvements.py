import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import alert_bot
from alert_bot import prediction_history_lines
from intelligence_v2.contracts import ForecastHorizonBand, ForecastRecord
from intelligence_v2.forecast_eval import build_forecast_scorecard, evaluate_forecast
from intelligence_v2.trend_volume_candidate import make_candidate, process_candidates


class CompletedCandleTests(unittest.TestCase):
    def test_fetch_binance_excludes_unfinished_candle(self):
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        interval_ms = 4 * 60 * 60 * 1000
        closed = [now_ms - 2 * interval_ms, "0", "0", "0", "101", "12", now_ms - 1]
        open_bar = [now_ms, "0", "0", "0", "102", "99", now_ms + interval_ms - 1]

        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return [closed, open_bar]

        with patch.object(alert_bot.requests, "get", return_value=Response()):
            history, closes = alert_bot.fetch_binance("BTCUSDT", limit=2)
            card_history, card_closes = alert_bot.fetch_binance("BTCUSDT", limit=2, include_partial=True)
        self.assertEqual(closes, [101.0])
        self.assertEqual(len(history), 1)
        self.assertTrue(history[0]["bar_complete"])
        self.assertEqual(history[0]["bar_close_ts"], now_ms - 1)
        self.assertEqual(card_closes, [101.0, 102.0])
        self.assertFalse(card_history[-1]["bar_complete"])

    def test_rich_history_upserts_by_bar_close_not_wall_clock(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(alert_bot, "HISTORY_DIR", Path(temp)):
                ts = int(datetime.now(timezone.utc).timestamp() * 1000)
                first = alert_bot.update_rich_history(
                    "BTCUSDT", 100.0, 10.0, None, None, 50.0, None, 1.0,
                    bar_open_ts=ts - 4 * 3600_000, bar_close_ts=ts, captured_at_ms=ts + 1000,
                )
                second = alert_bot.update_rich_history(
                    "BTCUSDT", 101.0, 11.0, 3.0, 0.0, 51.0, None, 1.0,
                    bar_open_ts=ts - 4 * 3600_000, bar_close_ts=ts, captured_at_ms=ts + 2000,
                )
            rows = json.loads((Path(temp) / "BTCUSDT.json").read_text())
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0]["bar_complete"])
            self.assertEqual(rows[0]["price"], 101.0)
            self.assertFalse(first["updated_in_place"])
            self.assertTrue(second["updated_in_place"])

    def test_missing_okx_fields_remain_none(self):
        class Response:
            ok = True

            def __init__(self, data):
                self.data = data

            def json(self):
                return {"data": [self.data]}

        with patch.object(alert_bot.requests, "get", side_effect=[Response({"oi": ""}), Response({"fundingRate": None})]):
            self.assertEqual(alert_bot.fetch_okx("BTCUSDT"), (None, None))

    def test_process_formats_latest_closed_bar_not_forming_bar(self):
        captured = []
        base = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        closed_rows = [
            {"price": 1000.0, "volume": 10.0, "oi": None, "bar_open_ts": base,
             "bar_close_ts": base + 4 * 3600_000 - 1, "captured_at_ms": base + 4 * 3600_000,
             "bar_complete": True},
            {"price": 1010.0, "volume": 20.0, "oi": None, "bar_open_ts": base + 4 * 3600_000,
             "bar_close_ts": base + 8 * 3600_000 - 1, "captured_at_ms": base + 8 * 3600_000,
             "bar_complete": True},
        ]
        forming = {"price": 9999.0, "volume": 999.0, "oi": None,
                   "bar_open_ts": base + 8 * 3600_000,
                   "bar_close_ts": base + 12 * 3600_000 - 1,
                   "captured_at_ms": base + 9 * 3600_000, "bar_complete": False}
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(alert_bot, "HISTORY_DIR", Path(temp)), \
                 patch.object(alert_bot, "fetch_binance", return_value=(closed_rows + [forming], [1000.0, 1010.0, 9999.0])), \
                 patch.object(alert_bot, "fetch_okx", return_value=(500.0, 0.0)), \
                 patch.object(alert_bot, "send_telegram", side_effect=lambda text: captured.append(text) or True):
                alert_bot.process("BTCUSDT", None, None)
        self.assertEqual(len(captured), 1)
        self.assertIn("BTCUSDT · 4h close · 2026-01-01 08:00 UTC", captured[0])
        self.assertIn("Price 1,010.00 (+1.00%)", captured[0])
        self.assertIn("Vol 2.00x", captured[0])
        self.assertNotIn("9,999.00", captured[0])


class ForecastBaselineTests(unittest.TestCase):
    def test_abstention_keeps_market_outcome_for_baselines(self):
        with tempfile.TemporaryDirectory() as temp:
            base = datetime(2026, 1, 1, tzinfo=timezone.utc)
            rows = [
                {"date": (base + timedelta(hours=4 * i)).isoformat(), "price": price}
                for i, price in enumerate([100, 101, 102, 99, 98])
            ]
            Path(temp, "BTCUSDT.json").write_text(json.dumps(rows))
            issued = (base + timedelta(hours=8, minutes=1)).isoformat()
            band = ForecastHorizonBand(1, "abstain", 0.45, 0.55, None, None, "abstain")
            fc = ForecastRecord(
                agent="forecast-engine", run_id="test", symbol="BTCUSDT", timeframe="4h",
                forecast_id="abstain-test", issued_at=issued, stance="ABSTAIN",
                primary_direction="abstain", horizons=[band], confidence=20,
                probability_band="abstain", drivers=[], invalidation=[], abstain_reasons=["test"],
                evidence_ids=[], model_version="test", calibration_notes=[], execution_allowed=False,
            )
            ev = evaluate_forecast(fc, temp, 1)
            self.assertEqual(ev.status, "abstained")
            self.assertIsNone(ev.direction_correct)
            self.assertIsNotNone(ev.forward_return_pct)
            self.assertEqual(ev.baseline_directions["persistence"], "up")

    def test_scorecard_reports_same_outcome_baselines_and_coverage(self):
        forecasts = [
            {"forecast_id": "a", "stance": "FORECAST", "probability_band": "medium"},
            {"forecast_id": "b", "stance": "ABSTAIN", "probability_band": "abstain"},
        ]
        evaluations = [
            {"forecast_id": "a", "horizon_bars": 1, "status": "settled", "direction": "up", "direction_correct": True, "forward_return_pct": 1.0, "baseline_directions": {"persistence": "down"}},
            {"forecast_id": "b", "horizon_bars": 1, "status": "abstained", "direction": "abstain", "direction_correct": None, "forward_return_pct": -1.0, "baseline_directions": {"persistence": "up"}},
        ]
        score = build_forecast_scorecard(forecasts, evaluations)
        h = score.by_horizon["1"]
        self.assertEqual(h["directional"], 1)
        self.assertEqual(h["outcomes_including_abstentions"], 2)
        self.assertEqual(h["fired_coverage_pct"], 50.0)
        self.assertEqual(h["gross_policy_return_mean_pct"], 0.5)
        self.assertEqual(score.benchmarks_by_horizon["1"]["always_up"]["accuracy_pct"], 50.0)


class TelegramPredictionSummaryTests(unittest.TestCase):
    def test_renders_pooled_horizon_baselines_and_shadow_candidate(self):
        with tempfile.TemporaryDirectory() as temp:
            v2 = Path(temp, "intelligence_v2")
            v2.mkdir()
            Path(v2, "forecast_scorecard.json").write_text(json.dumps({
                "evaluated_at": "2026-11-22T17:13:00+00:00",
                "directional_settled": 10,
                "directional_hits": 6,
                "direction_accuracy_pct": 60.0,
                "abstain_count": 5,
                "abstain_rate_pct": 33.3,
                "by_horizon": {"1": {"directional": 4, "hits": 2, "accuracy_pct": 50.0}},
                "benchmarks_by_horizon": {"1": {
                    "always_up": {"n": 6, "accuracy_pct": 66.7},
                    "always_down": {"n": 6, "accuracy_pct": 33.3},
                    "persistence": {"n": 2, "accuracy_pct": 50.0},
                }},
            }))
            Path(v2, "trend_volume_candidate_ledger.json").write_text(json.dumps([
                {"direction": "up"}, {"direction": "abstain"},
            ]))
            Path(v2, "trend_volume_candidate_evaluations.json").write_text(json.dumps([
                {"status": "settled", "direction": "up", "forward_return_pct": 1.0,
                 "direction_correct": True, "baseline_directions": {"persistence": "down"}},
                {"status": "settled", "direction": "abstain", "forward_return_pct": -1.0,
                 "direction_correct": None, "baseline_directions": {"persistence": "up"}},
            ]))
            lines = prediction_history_lines(temp)
            text = "\n".join(lines)
            self.assertIn("History · pooled shadow · abstain 33%", text)
            self.assertIn("4h 50% (2/4) vs U67%(6)/D33%(6)/P50%(2)", text)
            self.assertIn("Trend/volume shadow: 100% (1/1) vs U/D/P 50%/50%/0%", text)
            self.assertIn("calls 1/2", text)

    def test_card_is_compact_and_uses_closed_bar_timestamp(self):
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, "intelligence_v2").mkdir()
            base = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
            history = [{
                "price": 100 + i / 10,
                "volume": 20.0,
                "bar_close_ts": base + (i + 1) * 4 * 3600_000 - 1,
            } for i in range(21)]
            with patch.object(alert_bot, "HISTORY_DIR", Path(temp)):
                card = alert_bot.build_card(
                    "BTCUSDT", history, [row["price"] for row in history],
                    500.0, 0.00001, None, None, prev_oi=500.0,
                )
            self.assertIn("BTCUSDT · 4h close · 2026-01-04 12:00 UTC", card)
            self.assertIn("Vol 1.00x", card)
            self.assertIn("History · pooled shadow", card)
            self.assertNotIn("Figures:", card)
            self.assertNotIn("Why:", card)
            self.assertNotIn("Watch:", card)
            self.assertLess(len(card), 600)


class TrendVolumeCandidateTests(unittest.TestCase):
    def _write_history(self, root, volumes):
        base = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        rows = []
        for i, volume in enumerate(volumes):
            rows.append({
                "bar_open_ts": base + i * 4 * 3600_000,
                "bar_close_ts": base + (i + 1) * 4 * 3600_000,
                "bar_complete": True,
                "captured_at_ms": base + (i + 1) * 4 * 3600_000 + 1000,
                "price": 100 + i,
                "volume": volume,
            })
        Path(root, "BTCUSDT.json").write_text(json.dumps(rows))

    def test_candidate_fires_only_on_trend_alignment_and_volume_confirmation(self):
        with tempfile.TemporaryDirectory() as temp:
            self._write_history(temp, [100.0] * 20 + [200.0])
            candidate = make_candidate(temp, "BTCUSDT", "bullish")
            self.assertEqual(candidate["direction"], "up")
            self.assertEqual(candidate["model_version"], "trend-volume-shadow-v1")
            self.assertFalse(candidate["execution_allowed"])

    def test_candidate_abstains_when_volume_does_not_confirm(self):
        with tempfile.TemporaryDirectory() as temp:
            self._write_history(temp, [100.0] * 21)
            candidate = make_candidate(temp, "BTCUSDT", "bullish")
            self.assertEqual(candidate["direction"], "abstain")

    def test_candidate_ledger_deduplicates_and_settles(self):
        with tempfile.TemporaryDirectory() as temp:
            self._write_history(temp, [100.0] * 20 + [200.0])
            candidate = make_candidate(temp, "BTCUSDT", "bullish")
            first = process_candidates(temp, [candidate])
            second = process_candidates(temp, [candidate])
            self.assertEqual(first["new_predictions"], 1)
            self.assertEqual(second["new_predictions"], 0)
            self.assertEqual(second["predictions_total"], 1)
            history_path = Path(temp, "BTCUSDT.json")
            rows = json.loads(history_path.read_text())
            next_row = dict(rows[-1])
            next_row["bar_open_ts"] = int(next_row["bar_open_ts"]) + 4 * 3600_000
            next_row["bar_close_ts"] = int(next_row["bar_close_ts"]) + 4 * 3600_000
            next_row["price"] = 220.0
            rows.append(next_row)
            history_path.write_text(json.dumps(rows))
            settled = process_candidates(temp, [candidate])
            self.assertEqual(settled["outcome_n_including_abstentions"], 1)
            self.assertEqual(settled["settled_directional"], 1)
            self.assertEqual(settled["direction_accuracy_pct"], 100.0)


if __name__ == "__main__":
    unittest.main()
