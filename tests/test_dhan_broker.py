import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.dhan_broker import (
    normalize_dhan_candles,
    normalize_dhan_positions,
    order_id_from_response,
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


if __name__ == "__main__":
    unittest.main()
