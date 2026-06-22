import unittest
from datetime import datetime, timedelta

from app.backtest import run_custom_strategy_backtest


class BacktestTests(unittest.TestCase):
    def _gmma_trend_candles(self):
        start = datetime(2026, 6, 12, 9, 15)
        rows = []
        for i in range(140):
            close = 100 + i * 0.35
            rows.append(
                {
                "date": start + timedelta(minutes=5 * i),
                    "open": close - 0.1,
                    "high": close + 0.7,
                    "low": close - 0.3,
                    "close": close,
                    "volume": 1500 + i * 10,
                }
            )
        return rows

    def test_backtest_uses_pre_range_candles_as_warmup(self) -> None:
        candles = self._gmma_trend_candles()
        result = run_custom_strategy_backtest(
            candles,
            "GMMA_OBV",
            {
                "direction": "BOTH",
                "gmma_require_gc": False,
                "gmma_adx_min": 10,
                "gmma_tp1_mult": 0.5,
                "gmma_tp2_mult": 1.0,
                "gmma_tp3_mult": 1.5,
            },
            symbol="SBIN",
            from_dt=candles[80]["date"],
            to_dt=candles[-1]["date"],
            qty=10,
        )

        self.assertGreaterEqual(result["warmup_candles"], 80)
        self.assertGreater(result["total_trades"], 0)
        self.assertGreater(result["net_pnl"], 0)
        self.assertEqual(result["trades"][0]["strategy_mode"], "GMMA_OBV")
        self.assertIn("parameters", result)
        self.assertIn("adx_min", result["parameters"])
        self.assertGreater(len(result["detail_report"]), 0)
        self.assertTrue(any(row["status"] in {"FAILED", "QUALIFIED", "IN_POSITION", "EXIT"} for row in result["detail_report"]))
        self.assertTrue(any(row.get("indicators", {}).get("gmma_s1_ema3") is not None for row in result["detail_report"]))

    def test_backtest_reports_failed_criteria_when_no_trade_qualifies(self) -> None:
        candles = self._gmma_trend_candles()
        result = run_custom_strategy_backtest(
            candles,
            "GMMA_OBV",
            {
                "direction": "BOTH",
                "gmma_require_gc": True,
                "gmma_adx_min": 10,
            },
            symbol="SBIN",
            from_dt=candles[80]["date"],
            to_dt=candles[-1]["date"],
            qty=10,
        )

        self.assertEqual(result["total_trades"], 0)
        failed = [row for row in result["detail_report"] if row["status"] == "FAILED"]
        self.assertGreater(len(failed), 0)
        self.assertIn("GMMA_ENTRY_CHECK_FAILED", {row["reason"] for row in failed})
        self.assertIn("checks", failed[0])

    def test_backtest_reports_warmup_rows_when_no_candles_are_in_range(self) -> None:
        candles = self._gmma_trend_candles()
        result = run_custom_strategy_backtest(
            candles,
            "GMMA_OBV",
            {"direction": "BOTH", "gmma_require_gc": False},
            symbol="TCS",
            from_dt=candles[-1]["date"] + timedelta(days=1),
            to_dt=candles[-1]["date"] + timedelta(days=2),
            qty=1,
        )

        self.assertEqual(result["in_range_candles"], 0)
        self.assertEqual(result["warmup_candles"], len(candles))
        self.assertEqual(len(result["detail_report"]), len(candles))
        self.assertEqual({row["status"] for row in result["detail_report"]}, {"WARMUP"})
        self.assertEqual(result["detail_report"][0]["reason"], "CANDLE_BEFORE_BACKTEST_START")


if __name__ == "__main__":
    unittest.main()
