import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd

from app.dhan_broker import (
    DhanInstrumentRegistry,
    normalize_dhan_candles,
    normalize_dhan_positions,
    order_id_from_response,
    resample_intraday_candles,
)
from app.memory_store import InMemoryStore
from app.trade_engine import TradeEngine


class DhanBrokerTests(unittest.TestCase):
    def test_order_id_is_normalized(self) -> None:
        self.assertEqual(
            order_id_from_response({"status": "success", "data": {"orderId": "123"}}),
            "123",
        )

    def test_positions_are_converted_to_engine_shape(self) -> None:
        result = normalize_dhan_positions(
            {
                "data": [
                    {
                        "tradingSymbol": "SBIN",
                        "securityId": "3045",
                        "netQty": 10,
                        "costPrice": 812.5,
                        "productType": "INTRADAY",
                        "realizedProfit": 20,
                        "unrealizedProfit": 30,
                    }
                ]
            }
        )
        self.assertEqual(result["net"][0]["tradingsymbol"], "SBIN")
        self.assertEqual(result["net"][0]["quantity"], 10)
        self.assertEqual(result["net"][0]["product"], "MIS")
        self.assertEqual(result["net"][0]["pnl"], 50)

    def test_open_candle_is_excluded(self) -> None:
        result = normalize_dhan_candles(
            {
                "data": {
                    "open": [100],
                    "high": [102],
                    "low": [99],
                    "close": [101],
                    "volume": [1000],
                    "timestamp": [1],
                }
            },
            5,
        )
        self.assertEqual(len(result), 1)

    def test_resamples_one_minute_candles_to_three_minute(self) -> None:
        from datetime import datetime, timedelta
        import pytz

        ist = pytz.timezone("Asia/Kolkata")
        start = ist.localize(datetime(2026, 6, 19, 9, 15))
        candles = [
            {
                "date": start + timedelta(minutes=i),
                "open": 100 + i,
                "high": 101 + i,
                "low": 99 + i,
                "close": 100.5 + i,
                "volume": 10 + i,
            }
            for i in range(6)
        ]

        result = resample_intraday_candles(candles, 3)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["date"], start)
        self.assertEqual(result[0]["open"], 100)
        self.assertEqual(result[0]["high"], 103)
        self.assertEqual(result[0]["low"], 99)
        self.assertEqual(result[0]["close"], 102.5)
        self.assertEqual(result[0]["volume"], 33)

    def test_dhan_registry_selects_nearest_expiry_atm_index_option(self) -> None:
        registry = DhanInstrumentRegistry()
        registry._master_frame = pd.DataFrame(
            [
                {
                    "SEM_TRADING_SYMBOL": "BANKNIFTY26JUN54000CE",
                    "SEM_SEGMENT": "OPTIDX",
                    "SEM_EXPIRY_DATE": "2026-06-26",
                    "SEM_OPTION_TYPE": "CE",
                    "SEM_STRIKE_PRICE": 54000,
                    "SEM_SMST_SECURITY_ID": "9001",
                },
                {
                    "SEM_TRADING_SYMBOL": "BANKNIFTY26JUN54100CE",
                    "SEM_SEGMENT": "OPTIDX",
                    "SEM_EXPIRY_DATE": "2026-06-26",
                    "SEM_OPTION_TYPE": "CE",
                    "SEM_STRIKE_PRICE": 54100,
                    "SEM_SMST_SECURITY_ID": "9002",
                },
                {
                    "SEM_TRADING_SYMBOL": "BANKNIFTY26JUN54000PE",
                    "SEM_SEGMENT": "OPTIDX",
                    "SEM_EXPIRY_DATE": "2026-06-26",
                    "SEM_OPTION_TYPE": "PE",
                    "SEM_STRIKE_PRICE": 54000,
                    "SEM_SMST_SECURITY_ID": "9003",
                },
            ]
        )

        async def run():
            return await registry.atm_index_option("BANKNIFTY", "BUY", 54024, today=pd.Timestamp("2026-06-19").date())

        result = __import__("asyncio").run(run())

        self.assertEqual(result["security_id"], "9001")
        self.assertEqual(result["option_type"], "CE")


class DhanTradeEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_dhan_market_order_uses_security_id(self) -> None:
        store = InMemoryStore()
        await store.save_broker(1, "DHAN")
        await store.save_dhan_credentials(1, "client", "token")
        engine = TradeEngine(1, store)
        await engine.configure_broker()
        engine.dhan = MagicMock(
            NSE="NSE_EQ",
            BUY="BUY",
            SELL="SELL",
            MARKET="MARKET",
            INTRA="INTRADAY",
            CNC="CNC",
        )
        engine.order_worker.submit = AsyncMock(
            return_value={"status": "success", "data": {"orderId": "DHAN-1"}}
        )

        with patch(
            "app.trade_engine.DHAN_INSTRUMENTS.security_id",
            AsyncMock(return_value="3045"),
        ):
            order_id = await engine._place_order("SBIN", "BUY", 10, "MIS")

        self.assertEqual(order_id, "DHAN-1")
        kwargs = engine.order_worker.submit.await_args.kwargs
        self.assertEqual(kwargs["security_id"], "3045")
        self.assertEqual(kwargs["exchange_segment"], "NSE_EQ")
        self.assertEqual(kwargs["product_type"], "INTRADAY")
        engine.order_worker.task.cancel()
        if engine._pnl_exit_task:
            engine._pnl_exit_task.cancel()

    async def test_dhan_pending_order_is_cancelled_and_retried_until_executed(self) -> None:
        store = InMemoryStore()
        await store.save_broker(1, "DHAN")
        await store.save_dhan_credentials(1, "client", "token")
        engine = TradeEngine(1, store)
        await engine.configure_broker()
        engine.dhan = MagicMock(
            NSE="NSE_EQ",
            BUY="BUY",
            SELL="SELL",
            MARKET="MARKET",
            INTRA="INTRADAY",
            CNC="CNC",
        )
        engine.dhan.place_order = MagicMock()
        engine.dhan.cancel_order = MagicMock()
        engine.dhan.get_order_by_id = MagicMock()

        placed: list[str] = []
        cancelled: list[str] = []

        async def fake_order_submit(fn, *args, **kwargs):
            if fn is engine.dhan.place_order:
                order_id = "DHAN-PENDING" if not placed else "DHAN-FILLED"
                placed.append(order_id)
                return {"status": "success", "data": {"orderId": order_id}}
            if fn is engine.dhan.cancel_order:
                cancelled.append(str(args[0]))
                return {"status": "success"}
            raise AssertionError(f"unexpected order worker fn {fn}")

        async def fake_market_submit(fn, *args, **kwargs):
            self.assertIs(fn, engine.dhan.get_order_by_id)
            order_id = str(args[0])
            if order_id == "DHAN-PENDING":
                return {
                    "data": {
                        "orderId": order_id,
                        "orderStatus": "PENDING",
                        "quantity": 10,
                        "remainingQuantity": 10,
                    }
                }
            return {
                "data": {
                    "orderId": order_id,
                    "orderStatus": "TRADED",
                    "quantity": 10,
                    "filledQuantity": 10,
                    "remainingQuantity": 0,
                    "averagePrice": 101.25,
                    "tradingSymbol": "SBIN",
                }
            }

        engine.order_worker.submit = fake_order_submit
        engine.market_data_worker.submit = fake_market_submit

        with patch(
            "app.trade_engine.DHAN_INSTRUMENTS.security_id",
            AsyncMock(return_value="3045"),
        ):
            execution = await engine._place_order_with_execution(
                "SBIN",
                "BUY",
                10,
                "MIS",
                {"order_confirm_timeout_sec": 0.2, "order_pending_retry_count": 1},
            )

        self.assertEqual(execution.order_id, "DHAN-FILLED")
        self.assertEqual(execution.status, "COMPLETE")
        self.assertEqual(execution.avg_price, 101.25)
        self.assertEqual(execution.attempts, 2)
        self.assertEqual(cancelled, ["DHAN-PENDING"])
        engine.order_worker.task.cancel()
        if engine._pnl_exit_task:
            engine._pnl_exit_task.cancel()

    async def test_dhan_candles_request_uses_intraday_time_boundaries(self) -> None:
        store = InMemoryStore()
        await store.save_broker(1, "DHAN")
        await store.save_dhan_credentials(1, "client", "token")
        engine = TradeEngine(1, store)
        await engine.configure_broker()
        engine.dhan = MagicMock(NSE="NSE_EQ")
        engine.market_data_worker.submit = AsyncMock(
            return_value={
                "data": {
                    "open": [],
                    "high": [],
                    "low": [],
                    "close": [],
                    "volume": [],
                    "timestamp": [],
                }
            }
        )

        with patch(
            "app.trade_engine.DHAN_INSTRUMENTS.security_id",
            AsyncMock(return_value="3045"),
        ):
            await engine._fetch_historical_candles("SBIN", "5minute", 5)

        kwargs = engine.market_data_worker.submit.await_args.kwargs
        self.assertTrue(kwargs["from_date"].endswith("09:15:00"))
        self.assertRegex(kwargs["to_date"], r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
        self.assertEqual(kwargs["interval"], 5)
        self.assertEqual(kwargs["instrument_type"], "EQUITY")
        engine.order_worker.task.cancel()
        if engine._pnl_exit_task:
            engine._pnl_exit_task.cancel()

    async def test_dhan_three_minute_fetch_uses_one_minute_and_resamples(self) -> None:
        from datetime import datetime, timedelta
        import pytz

        store = InMemoryStore()
        await store.save_broker(1, "DHAN")
        await store.save_dhan_credentials(1, "client", "token")
        engine = TradeEngine(1, store)
        await engine.configure_broker()
        engine.dhan = MagicMock(NSE="NSE_EQ")
        ist = pytz.timezone("Asia/Kolkata")
        start = ist.localize(datetime(2026, 6, 19, 9, 15))
        stamps = [int((start + timedelta(minutes=i)).timestamp()) for i in range(6)]
        engine.market_data_worker.submit = AsyncMock(
            return_value={
                "data": {
                    "open": [100, 101, 102, 103, 104, 105],
                    "high": [101, 102, 103, 104, 105, 106],
                    "low": [99, 100, 101, 102, 103, 104],
                    "close": [100.5, 101.5, 102.5, 103.5, 104.5, 105.5],
                    "volume": [10, 11, 12, 13, 14, 15],
                    "timestamp": stamps,
                }
            }
        )

        result = await engine._fetch_dhan_intraday_candles(
            "3045",
            3,
            start,
            start + timedelta(minutes=5),
        )

        self.assertEqual(engine.market_data_worker.submit.await_args.kwargs["interval"], 1)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["open"], 100)
        self.assertEqual(result[0]["close"], 102.5)
        engine.order_worker.task.cancel()
        if engine._pnl_exit_task:
            engine._pnl_exit_task.cancel()

    async def test_dhan_intraday_discards_candles_outside_requested_window(self) -> None:
        from datetime import datetime, timedelta
        import pytz

        store = InMemoryStore()
        await store.save_broker(1, "DHAN")
        await store.save_dhan_credentials(1, "client", "token")
        engine = TradeEngine(1, store)
        await engine.configure_broker()
        engine.dhan = MagicMock(NSE="NSE_EQ")
        ist = pytz.timezone("Asia/Kolkata")
        requested_start = ist.localize(datetime(2026, 6, 12, 9, 15))
        stale_start = ist.localize(datetime(2026, 5, 29, 9, 15))
        engine.market_data_worker.submit = AsyncMock(
            return_value={
                "data": {
                    "open": [100],
                    "high": [101],
                    "low": [99],
                    "close": [100.5],
                    "volume": [10],
                    "timestamp": [int(stale_start.timestamp())],
                }
            }
        )

        result = await engine._fetch_dhan_intraday_candles(
            "3045",
            5,
            requested_start,
            requested_start + timedelta(minutes=5),
        )

        self.assertEqual(result, [])
        self.assertEqual(engine.market_data_worker.submit.await_args.kwargs["from_date"], "2026-06-12 09:15:00")
        engine.order_worker.task.cancel()
        if engine._pnl_exit_task:
            engine._pnl_exit_task.cancel()

    async def test_dhan_backtest_uses_index_segment_for_nifty(self) -> None:
        from datetime import datetime
        import pytz

        store = InMemoryStore()
        await store.save_broker(1, "DHAN")
        await store.save_dhan_credentials(1, "client", "token")
        engine = TradeEngine(1, store)
        await engine.configure_broker()
        engine.dhan = MagicMock(NSE="NSE_EQ", INDEX="IDX_I")
        engine.market_data_worker.submit = AsyncMock(
            return_value={
                "data": {
                    "open": [],
                    "high": [],
                    "low": [],
                    "close": [],
                    "volume": [],
                    "timestamp": [],
                }
            }
        )
        ist = pytz.timezone("Asia/Kolkata")

        await engine._fetch_backtest_candles(
            "NIFTY",
            "15minute",
            ist.localize(datetime(2026, 6, 19, 9, 15)),
            ist.localize(datetime(2026, 6, 19, 15, 30)),
            warmup_days=1,
        )

        kwargs = engine.market_data_worker.submit.await_args.kwargs
        self.assertEqual(kwargs["security_id"], "13")
        self.assertEqual(kwargs["exchange_segment"], "IDX_I")
        self.assertEqual(kwargs["instrument_type"], "INDEX")
        self.assertEqual(kwargs["interval"], 5)
        engine.order_worker.task.cancel()
        if engine._pnl_exit_task:
            engine._pnl_exit_task.cancel()

    async def test_dhan_fifteen_minute_fetch_uses_five_minute_and_resamples(self) -> None:
        from datetime import datetime, timedelta
        import pytz

        store = InMemoryStore()
        await store.save_broker(1, "DHAN")
        await store.save_dhan_credentials(1, "client", "token")
        engine = TradeEngine(1, store)
        await engine.configure_broker()
        engine.dhan = MagicMock(NSE="NSE_EQ")
        ist = pytz.timezone("Asia/Kolkata")
        start = ist.localize(datetime(2026, 6, 19, 9, 15))
        stamps = [int((start + timedelta(minutes=5 * i)).timestamp()) for i in range(6)]
        engine.market_data_worker.submit = AsyncMock(
            return_value={
                "data": {
                    "open": [100, 101, 102, 103, 104, 105],
                    "high": [101, 102, 103, 104, 105, 106],
                    "low": [99, 100, 101, 102, 103, 104],
                    "close": [100.5, 101.5, 102.5, 103.5, 104.5, 105.5],
                    "volume": [10, 11, 12, 13, 14, 15],
                    "timestamp": stamps,
                }
            }
        )

        result = await engine._fetch_dhan_intraday_candles(
            "3045",
            15,
            start,
            start + timedelta(minutes=25),
        )

        self.assertEqual(engine.market_data_worker.submit.await_args.kwargs["interval"], 5)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["date"].strftime("%H:%M"), "09:15")
        self.assertEqual(result[0]["open"], 100)
        self.assertEqual(result[0]["high"], 103)
        self.assertEqual(result[0]["low"], 99)
        self.assertEqual(result[0]["close"], 102.5)
        self.assertEqual(result[0]["volume"], 33)
        self.assertEqual(result[1]["date"].strftime("%H:%M"), "09:30")
        self.assertEqual(result[1]["close"], 105.5)
        engine.order_worker.task.cancel()
        if engine._pnl_exit_task:
            engine._pnl_exit_task.cancel()

    async def test_dhan_backtest_fetches_selected_range_before_warmup(self) -> None:
        from datetime import datetime, timedelta
        import pytz

        store = InMemoryStore()
        await store.save_broker(1, "DHAN")
        await store.save_dhan_credentials(1, "client", "token")
        engine = TradeEngine(1, store)
        await engine.configure_broker()
        engine.dhan = MagicMock(NSE="NSE_EQ", INDEX="IDX_I")
        ist = pytz.timezone("Asia/Kolkata")
        selected_start = ist.localize(datetime(2026, 6, 19, 9, 15))
        warmup_start = ist.localize(datetime(2026, 6, 17, 9, 15))
        calls = []

        async def submit_side_effect(_fn, *args, **kwargs):
            calls.append(dict(kwargs))
            from_date = str(kwargs.get("from_date") or "")
            stamp = selected_start if from_date.startswith("2026-06-19") else warmup_start
            return {
                "data": {
                    "open": [100],
                    "high": [101],
                    "low": [99],
                    "close": [100.5],
                    "volume": [10],
                    "timestamp": [int(stamp.timestamp())],
                }
            }

        engine.market_data_worker.submit = AsyncMock(side_effect=submit_side_effect)

        result = await engine._fetch_backtest_candles(
            "NIFTY",
            "5minute",
            selected_start,
            selected_start.replace(hour=15, minute=30),
            warmup_days=2,
        )

        self.assertTrue(calls[0]["from_date"].startswith("2026-06-19"))
        self.assertTrue(any(candle["date"].date().isoformat() == "2026-06-19" for candle in result))
        self.assertTrue(any(candle["date"].date().isoformat() == "2026-06-17" for candle in result))
        engine.order_worker.task.cancel()
        if engine._pnl_exit_task:
            engine._pnl_exit_task.cancel()

    async def test_dhan_backtest_returns_empty_when_selected_range_has_no_candles(self) -> None:
        from datetime import datetime
        import pytz

        store = InMemoryStore()
        await store.save_broker(1, "DHAN")
        await store.save_dhan_credentials(1, "client", "token")
        engine = TradeEngine(1, store)
        await engine.configure_broker()
        engine.dhan = MagicMock(NSE="NSE_EQ", INDEX="IDX_I")
        engine.market_data_worker.submit = AsyncMock(
            return_value={"data": {"open": [], "high": [], "low": [], "close": [], "volume": [], "timestamp": []}}
        )
        ist = pytz.timezone("Asia/Kolkata")

        result = await engine._fetch_backtest_candles(
            "NIFTY",
            "5minute",
            ist.localize(datetime(2026, 6, 19, 9, 15)),
            ist.localize(datetime(2026, 6, 19, 15, 30)),
            warmup_days=2,
        )

        self.assertEqual(result, [])
        self.assertEqual(engine.market_data_worker.submit.await_count, 1)
        engine.order_worker.task.cancel()
        if engine._pnl_exit_task:
            engine._pnl_exit_task.cancel()


if __name__ == "__main__":
    unittest.main()
