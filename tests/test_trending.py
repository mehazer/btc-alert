"""Tests for the CoinGecko trending module. No network calls."""
from __future__ import annotations

import os
import unittest
from unittest import mock

import trending


def _payload(count=6):
    coins = []
    for index in range(count):
        coins.append({
            "item": {
                "id": f"coin-{index}",
                "name": f"Coin {index}",
                "symbol": f"C{index}",
                "market_cap_rank": 100 + index,
                "data": {
                    "price": 2.5 if index == 0 else 0.0000457,
                    "price_change_percentage_24h": {"usd": 33.1123 if index == 0 else -4.26},
                },
            }
        })
    return {"coins": coins, "nfts": [], "categories": []}


class ParseTests(unittest.TestCase):
    def test_nested_payload_is_flattened(self):
        items = trending.parse_payload(_payload(), limit=3)
        self.assertEqual(len(items), 3)
        first = items[0]
        self.assertEqual(first["rank"], 1)
        self.assertEqual(first["symbol"], "C0")
        self.assertEqual(first["name"], "Coin 0")
        self.assertEqual(first["market_cap_rank"], 100)
        self.assertAlmostEqual(first["price_usd"], 2.5)
        self.assertAlmostEqual(first["change_24h_pct"], 33.1123)
        self.assertEqual(items[1]["change_24h_pct"], -4.26)

    def test_limit_is_respected(self):
        self.assertEqual(len(trending.parse_payload(_payload(10), limit=2)), 2)

    def test_empty_payload_is_empty_not_an_error(self):
        self.assertEqual(trending.parse_payload({}), [])
        self.assertEqual(trending.parse_payload({"coins": []}), [])

    def test_missing_fields_do_not_crash(self):
        items = trending.parse_payload({"coins": [{"item": {"name": "X"}}]}, limit=1)
        self.assertEqual(items[0]["symbol"], "?")
        self.assertIsNone(items[0]["price_usd"])


class FormatTests(unittest.TestCase):
    def test_ok_is_one_line(self):
        data = {"status": "ok", "source": "CoinGecko", "items": trending.parse_payload(_payload(), limit=2)}
        line = trending.format_line(data)
        self.assertTrue(line.startswith("Trending (CoinGecko, 24h): "))
        self.assertIn("C0 +33.1%", line)
        self.assertIn("C1 -4.3%", line)
        self.assertIn(" · ", line)
        self.assertNotIn("\n", line)

    def test_symbol_and_change_only(self):
        data = {"status": "ok", "source": "CoinGecko", "items": trending.parse_payload(_payload(), limit=2)}
        line = trending.format_line(data)
        self.assertNotIn("Coin 0", line)
        self.assertNotIn("$", line)

    def test_limit_is_respected(self):
        data = {"status": "ok", "source": "CoinGecko", "items": trending.parse_payload(_payload(), limit=5)}
        line = trending.format_line(data, limit=2)
        self.assertEqual(line.count(" · "), 1)

    def test_line_stays_short(self):
        data = {"status": "ok", "source": "CoinGecko", "items": trending.parse_payload(_payload(20), limit=5)}
        self.assertLess(len(trending.format_line(data)), 200)

    def test_missing_change_is_n_a(self):
        data = {"status": "ok", "source": "CoinGecko",
                "items": [{"rank": 1, "name": "X", "symbol": "XX", "price_usd": None,
                           "change_24h_pct": None, "market_cap_rank": None}]}
        self.assertIn("XX n/a", trending.format_line(data))

    def test_unavailable_is_visible_not_silent(self):
        line = trending.format_line({"status": "unavailable", "source": "CoinGecko", "items": [],
                                     "reason": "rate limited (HTTP 429)"})
        self.assertIn("unavailable", line)
        self.assertIn("429", line)

    def test_disabled_omits_line_entirely(self):
        data = {"status": "disabled", "source": "CoinGecko", "items": [], "reason": "TRENDING_ENABLED=0"}
        self.assertEqual(trending.format_line(data), "")

    def test_none_data_omits_line(self):
        self.assertEqual(trending.format_line(None), "")


class FetchTests(unittest.TestCase):
    def setUp(self):
        trending._CACHE["data"] = None
        trending._CACHE["fetched_at"] = 0.0

    def test_single_request_is_shared_between_calls(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = _payload()
        with mock.patch.object(trending.requests, "get", return_value=response) as get:
            first = trending.fetch_trending()
            second = trending.fetch_trending()
        self.assertEqual(get.call_count, 1)
        self.assertEqual(first["status"], "ok")
        self.assertEqual(second["status"], "ok")

    def test_force_bypasses_cache(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = _payload()
        with mock.patch.object(trending.requests, "get", return_value=response) as get:
            trending.fetch_trending()
            trending.fetch_trending(force=True)
        self.assertEqual(get.call_count, 2)

    def test_rate_limit_degrades_with_reason(self):
        response = mock.Mock(status_code=429)
        with mock.patch.object(trending.requests, "get", return_value=response):
            data = trending.fetch_trending()
        self.assertEqual(data["status"], "unavailable")
        self.assertIn("429", data["reason"])

    def test_network_error_degrades_without_raising(self):
        with mock.patch.object(trending.requests, "get", side_effect=TimeoutError("boom")):
            data = trending.fetch_trending()
        self.assertEqual(data["status"], "unavailable")
        self.assertIn("TimeoutError", data["reason"])

    def test_limit_from_environment(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = _payload()
        with mock.patch.dict(os.environ, {"TRENDING_LIMIT": "3"}), \
                mock.patch.object(trending.requests, "get", return_value=response):
            data = trending.fetch_trending(force=True)
        self.assertEqual(len(data["items"]), 3)

    def test_disabled_skips_network(self):
        with mock.patch.dict(os.environ, {"TRENDING_ENABLED": "0"}), \
                mock.patch.object(trending.requests, "get") as get:
            data = trending.fetch_trending(force=True)
        self.assertEqual(data["status"], "disabled")
        get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
