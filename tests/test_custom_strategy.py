import asyncio
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from app.custom_strategy import evaluate_precision_sniper, resolve_settings, validate_custom_config
from app.memory_store import InMemoryStore
from app.trade_engine import (
    AlertConfig,
    MarketDataWorker,
    OrderWorker,
    Position,
    TradeEngine,
    _partial_quantities,
)


class CustomStrategyTests(unittest.TestCase):
    def test_auto_resolves_to_five_minute_scalping_preset(self) -> None:
        settings = resolve_settings({"custom_preset": "Auto"})
        self.assertEqual(settings["preset"], "Scalping")
        self.assertEqual(settings["ema_fast"], 5)
        self.assertEqual(settings["ema_slow"], 13)

    def test_validation_requires_ordered_targets(self) -> None:
        error = validate_custom_config(
            {
                "custom_preset": "Custom",
                "custom_ema_fast": 9,
                "custom_ema_slow": 21,
                "custom_ema_trend": 55,
                "custom_tp1_mult": 2,
                "custom_tp2_mult": 1,
                "custom_tp3_mult": 3,
            }
        )
        self.assertEqual(error, "CUSTOM_TP_MULTIPLIERS_INVALID")

    def test_partial_profit_percentages_must_total_100(self) -> None:
        error = validate_custom_config(
            {
                "custom_partial_profit_enabled": True,
                "custom_partial_tp1_pct": 50,
                "custom_partial_tp2_pct": 25,
                "custom_partial_tp3_pct": 20,
            }
        )
        self.assertEqual(error, "CUSTOM_PARTIAL_PERCENT_TOTAL_MUST_BE_100")

    def test_partial_quantities_assign_rounding_remainder_to_tp3(self) -> None:
        self.assertEqual(_partial_quantities(100, 50, 25), (50, 25, 25))
        self.assertEqual(_partial_quantities(101, 50, 25), (50, 25, 26))

    def test_flat_market_does_not_create_signal(self) -> None:
        start = datetime(2026, 6, 12, 9, 15)
        candles = []
        for i in range(80):
            candles.append(
                {
                    "date": start + timedelta(minutes=5 * i),
                    "open": 100,
                    "high": 100.2,
                    "low": 99.8,
                    "close": 100,
                    "volume": 1000,
                }
            )
        signal, meta = evaluate_precision_sniper(candles, {"custom_preset": "Scalping"})
        self.assertIsNone(signal)
        self.assertEqual(meta["reason"], "CUSTOM_ENTRY_CHECK_FAILED")

    def test_bullish_crossover_creates_ranked_signal_and_risk_levels(self) -> None:
        start = datetime(2026, 6, 12, 9, 15)
        closes = [100] * 70 + [99, 98, 97, 96, 95, 95.5, 96, 96.5, 97, 97.5, 98, 98.5]
        candles = [
            {
                "date": start + timedelta(minutes=5 * i),
                "open": close,
                "high": close + 0.4,
                "low": close - 0.4,
                "close": close,
                "volume": 2000 if i == len(closes) - 1 else 1000,
            }
            for i, close in enumerate(closes)
        ]
        signal, meta = evaluate_precision_sniper(
            candles,
            {
                "custom_preset": "Scalping",
                "custom_grade_filter": "All",
                "custom_hide_c_grade": False,
                "custom_vol_filter_mode": "Off",
            },
        )
        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, "BUY")
        self.assertLess(signal.stop_loss, signal.signal_price)
        self.assertLess(signal.tp1, signal.tp2)
        self.assertLess(signal.tp2, signal.tp3)
        self.assertEqual(meta["reason"], "CUSTOM_SIGNAL_OK")


class HistoricalDataTests(unittest.IsolatedAsyncioTestCase):
    async def test_order_worker_supports_args_and_kwargs(self) -> None:
        worker = OrderWorker()
        await worker.start()
        try:
            result = await worker.submit(lambda left, right=0: left + right, 4, right=6)
            self.assertEqual(result, 10)
        finally:
            await worker.stop()

    async def test_order_worker_can_restart_after_stop(self) -> None:
        worker = OrderWorker()
        await worker.start()
        await worker.stop()
        await worker.start()
        try:
            self.assertEqual(await worker.submit(lambda: 7), 7)
        finally:
            await worker.stop()

    async def test_slow_market_data_does_not_block_order_worker(self) -> None:
        import time

        order_worker = OrderWorker()
        market_worker = MarketDataWorker(max_concurrency=2)
        await order_worker.start()

        def slow_data() -> str:
            time.sleep(0.15)
            return "data"

        try:
            data_task = asyncio.create_task(market_worker.submit(slow_data))
            await asyncio.sleep(0.01)
            started = time.perf_counter()
            result = await order_worker.submit(lambda: "order")
            elapsed = time.perf_counter() - started
            self.assertEqual(result, "order")
            self.assertLess(elapsed, 0.05)
            await data_task
        finally:
            await order_worker.stop()

    async def test_candle_fetch_uses_worker_keyword_arguments(self) -> None:
        engine = TradeEngine(1, InMemoryStore(), token_resolver=lambda _symbol: 123)
        engine.kite = MagicMock()
        engine.api_key = "key"
        engine.access_token = "token"
        engine.market_data_worker.submit = AsyncMock(return_value=[])

        await engine._fetch_historical_candles("SBIN")

        kwargs = engine.market_data_worker.submit.await_args.kwargs
        self.assertEqual(kwargs["instrument_token"], 123)
        self.assertEqual(kwargs["interval"], "5minute")

    async def test_ltp_fallback_uses_kite_variadic_arguments(self) -> None:
        engine = TradeEngine(1, InMemoryStore())
        engine.kite = MagicMock()
        engine.api_key = "key"
        engine.access_token = "token"
        engine.market_data_worker.submit = AsyncMock(return_value={"NSE:SBIN": {"last_price": 625.5}})

        price = await engine._fetch_ltp("SBIN")

        self.assertEqual(price, 625.5)
        self.assertEqual(engine.market_data_worker.submit.await_args.args[1:], ("NSE:SBIN",))


class TradeEngineIntegrationTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _signal_candles():
        start = datetime(2026, 6, 12, 9, 15)
        closes = [100] * 70 + [99, 98, 97, 96, 95, 95.5, 96, 96.5, 97, 97.5, 98, 98.5]
        return [
            {
                "date": start + timedelta(minutes=5 * i),
                "open": close,
                "high": close + 0.4,
                "low": close - 0.4,
                "close": close,
                "volume": 2000 if i == len(closes) - 1 else 1000,
            }
            for i, close in enumerate(closes)
        ]

    async def test_custom_alert_places_order_and_keeps_atr_stop(self) -> None:
        store = InMemoryStore()
        await store.save_alert_config(
            1,
            {
                "alert_name": "sniper",
                "enabled": True,
                "direction": "BOTH",
                "product": "MIS",
                "qty_mode": "QTY",
                "qty": 2,
                "trade_limit_per_day": 5,
                "entry_start_time": "00:00",
                "entry_end_time": "23:59",
                "strategy_mode": "PRECISION_SNIPER",
                "custom_preset": "Scalping",
                "custom_grade_filter": "All",
                "custom_hide_c_grade": False,
                "custom_vol_filter_mode": "Off",
                "custom_tp1_mult": 1,
                "custom_tp2_mult": 2,
                "custom_tp3_mult": 3,
                "custom_partial_profit_enabled": True,
                "custom_partial_tp1_pct": 50,
                "custom_partial_tp2_pct": 25,
                "custom_partial_tp3_pct": 25,
                "stop_loss_pct": 9.9,
            },
        )
        engine = TradeEngine(1, store)
        candles = self._signal_candles()
        expected, _ = evaluate_precision_sniper(
            candles,
            {
                "custom_preset": "Scalping",
                "custom_grade_filter": "All",
                "custom_hide_c_grade": False,
                "custom_vol_filter_mode": "Off",
                "custom_tp1_mult": 1,
                "custom_tp2_mult": 2,
                "custom_tp3_mult": 3,
            },
        )
        engine._fetch_historical_candles = AsyncMock(return_value=candles)
        engine._fetch_ltp = AsyncMock(return_value=99.0)
        engine._place_order = AsyncMock(return_value="ENTRY-1")

        result = await engine.on_chartink_alert("SNIPER", ["SBIN"], ts="2026-06-12T10:00:00")

        self.assertEqual(result[0]["status"], "ENTERED")
        self.assertEqual(result[0]["side"], "BUY")
        self.assertEqual(engine.positions["SBIN"].sl_price, expected.stop_loss)
        self.assertNotAlmostEqual(engine.positions["SBIN"].sl_price, 99.0 * (1 - 9.9 / 100))
        self.assertEqual(await store.get_open(1, "SBIN"), engine.positions["SBIN"].trade_id)
        self.assertTrue(engine.positions["SBIN"].custom_partial_profit_enabled)
        self.assertEqual(
            (
                engine.positions["SBIN"].tp1_exit_qty,
                engine.positions["SBIN"].tp2_exit_qty,
                engine.positions["SBIN"].tp3_exit_qty,
            ),
            (1, 0, 1),
        )

    async def test_already_open_does_not_consume_trade_limit(self) -> None:
        store = InMemoryStore()
        await store.save_alert_config(
            1,
            {
                "alert_name": "classic",
                "enabled": True,
                "direction": "LONG",
                "product": "MIS",
                "qty_mode": "QTY",
                "qty": 1,
                "trade_limit_per_day": 1,
                "entry_start_time": "00:00",
                "entry_end_time": "23:59",
            },
        )
        engine = TradeEngine(1, store)
        engine.positions["SBIN"] = Position(
            trade_id="existing",
            user_id=1,
            symbol="SBIN",
            alert_name="classic",
            side="BUY",
            product="MIS",
            qty=1,
            entry_price=100,
        )
        engine._fetch_ltp = AsyncMock(return_value=100.0)
        engine._place_order = AsyncMock(return_value="ENTRY-2")

        skipped = await engine.on_chartink_alert("classic", ["SBIN"])
        entered = await engine.on_chartink_alert("classic", ["INFY"])

        self.assertEqual(skipped[0]["reason"], "ALREADY_OPEN")
        self.assertEqual(entered[0]["status"], "ENTERED")

    async def test_custom_target_ladder_updates_trail_and_exits_at_tp3(self) -> None:
        store = InMemoryStore()
        engine = TradeEngine(1, store)
        position = Position(
            trade_id="custom-1",
            user_id=1,
            symbol="SBIN",
            alert_name="sniper",
            side="BUY",
            product="MIS",
            qty=1,
            entry_price=101,
            strategy_mode="PRECISION_SNIPER",
            signal_price=100,
            sl_price=98,
            trail_price=98,
            tp1_price=102,
            tp2_price=104,
            tp3_price=106,
            custom_use_trail=True,
            custom_full_exit_tp3=True,
        )
        engine.positions["SBIN"] = position
        engine._exit_position = AsyncMock()

        await engine.on_tick("SBIN", 102, 100, 102, 101)
        self.assertTrue(position.tp1_hit)
        self.assertEqual(position.trail_price, 100)

        await engine.on_tick("SBIN", 104, 100, 104, 103)
        self.assertTrue(position.tp2_hit)
        self.assertEqual(position.trail_price, 102)

        await engine.on_tick("SBIN", 106, 100, 106, 105)
        await __import__("asyncio").sleep(0)
        self.assertTrue(position.tp3_hit)
        self.assertEqual(position.status, "EXITING")
        engine._exit_position.assert_awaited_once_with("SBIN", "CUSTOM_TP3")

    async def test_partial_profit_books_each_target_once_and_closes_remainder(self) -> None:
        store = InMemoryStore()
        engine = TradeEngine(1, store)
        position = Position(
            trade_id="partial-1",
            user_id=1,
            symbol="SBIN",
            alert_name="sniper",
            side="BUY",
            product="MIS",
            qty=100,
            initial_qty=100,
            entry_price=100,
            strategy_mode="PRECISION_SNIPER",
            signal_price=100,
            sl_price=98,
            trail_price=98,
            tp1_price=102,
            tp2_price=104,
            tp3_price=106,
            custom_use_trail=True,
            custom_partial_profit_enabled=True,
            tp1_exit_qty=50,
            tp2_exit_qty=25,
            tp3_exit_qty=25,
        )
        engine.positions["SBIN"] = position
        await store.upsert_position(1, "SBIN", position.to_public())
        await store.mark_open(1, "SBIN", position.trade_id)
        engine._place_order = AsyncMock(side_effect=["TP1-OID", "TP2-OID", "TP3-OID"])

        await engine.on_tick("SBIN", 102, 100, 102, 101)
        self.assertEqual(position.qty, 50)
        self.assertTrue(position.tp1_booked)
        self.assertEqual(position.trail_price, 100)
        self.assertEqual(position.realized_pnl, 100)

        await engine.on_tick("SBIN", 102.5, 100, 102.5, 102)
        self.assertEqual(engine._place_order.await_count, 1)
        self.assertEqual(position.pnl, 225)

        await engine.on_tick("SBIN", 104, 100, 104, 103)
        self.assertEqual(position.qty, 25)
        self.assertTrue(position.tp2_booked)
        self.assertEqual(position.trail_price, 102)
        self.assertEqual(position.realized_pnl, 200)

        await engine.on_tick("SBIN", 106, 100, 106, 105)
        self.assertEqual(position.qty, 0)
        self.assertTrue(position.tp3_booked)
        self.assertNotIn("SBIN", engine.positions)
        self.assertEqual(await store.get_open(1, "SBIN"), "")
        self.assertEqual(
            [call.args[2] for call in engine._place_order.await_args_list],
            [50, 25, 25],
        )

    def test_string_false_values_remain_false(self) -> None:
        cfg = AlertConfig.from_dict(
            {
                "alert_name": "legacy",
                "enabled": "false",
                "sector_filter_on": "false",
                "tsl_stepwise": "false",
            }
        )
        self.assertFalse(cfg.enabled)
        self.assertFalse(cfg.sector_filter_on)
        self.assertFalse(cfg.tsl_stepwise)


if __name__ == "__main__":
    unittest.main()
