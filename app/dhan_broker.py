from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional

import pandas as pd
import pytz
from dhanhq import DhanContext, MarketFeed, OrderUpdate, dhanhq

from .redis_store import norm_symbol

log = logging.getLogger("dhan_broker")

DHAN_SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"


def _value(row: Any, *names: str) -> str:
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip() and str(value).lower() != "nan":
            return str(value).strip()
    return ""


class DhanInstrumentRegistry:
    """In-memory NSE equity symbol/security-id map from Dhan's official master."""

    def __init__(self) -> None:
        self.symbol_to_security: Dict[str, str] = {}
        self.security_to_symbol: Dict[str, str] = {}
        self._lock = asyncio.Lock()
        self.loaded_at: Optional[datetime] = None

    async def ensure_loaded(self, force: bool = False) -> bool:
        if self.symbol_to_security and not force:
            return True
        async with self._lock:
            if self.symbol_to_security and not force:
                return True
            try:
                frame = await asyncio.to_thread(
                    pd.read_csv,
                    DHAN_SCRIP_MASTER_URL,
                    low_memory=False,
                )
                symbol_map: Dict[str, str] = {}
                security_map: Dict[str, str] = {}
                for _, row in frame.iterrows():
                    exchange = _value(row, "SEM_EXM_EXCH_ID", "EXCH_ID").upper()
                    segment = _value(row, "SEM_SEGMENT", "SEGMENT").upper()
                    series = _value(row, "SEM_SERIES", "SERIES").upper()
                    instrument = _value(row, "SEM_INSTRUMENT_NAME", "INSTRUMENT").upper()
                    if exchange != "NSE" or segment not in {"E", "C"}:
                        continue
                    if series and series not in {"EQ", "BE", "BZ"}:
                        continue
                    if instrument and instrument not in {"EQUITY", "EQ"}:
                        continue
                    symbol = norm_symbol(_value(row, "SEM_TRADING_SYMBOL", "TRADING_SYMBOL"))
                    security_id = _value(row, "SEM_SMST_SECURITY_ID", "SECURITY_ID")
                    if symbol and security_id:
                        symbol_map[symbol] = security_id
                        security_map[security_id] = symbol
                if not symbol_map:
                    raise RuntimeError("DHAN_SCRIP_MASTER_EMPTY")
                self.symbol_to_security = symbol_map
                self.security_to_symbol = security_map
                self.loaded_at = datetime.now()
                log.info("Loaded %s Dhan NSE equity instruments", len(symbol_map))
                return True
            except Exception as exc:
                log.error("Dhan instrument master load failed: %s", exc)
                return False

    async def security_id(self, symbol: str) -> Optional[str]:
        await self.ensure_loaded()
        return self.symbol_to_security.get(norm_symbol(symbol))

    def symbol(self, security_id: Any) -> str:
        return self.security_to_symbol.get(str(security_id), "")


DHAN_INSTRUMENTS = DhanInstrumentRegistry()


def dhan_client(client_id: str, access_token: str) -> dhanhq:
    return dhanhq(DhanContext(str(client_id), str(access_token)))


def response_data(response: Any) -> Any:
    if isinstance(response, dict) and "data" in response:
        return response.get("data")
    return response


def order_id_from_response(response: Any) -> str:
    data = response_data(response)
    if isinstance(data, list) and data:
        data = data[0]
    if isinstance(data, dict):
        value = data.get("orderId") or data.get("order_id") or data.get("orderNo")
        if value:
            return str(value)
    if isinstance(response, dict):
        message = response.get("errorMessage") or response.get("message")
        if message:
            raise RuntimeError(str(message))
    if isinstance(response, (str, int)):
        return str(response)
    raise RuntimeError(f"DHAN_ORDER_REJECTED:{response}")


def normalize_dhan_positions(response: Any) -> Dict[str, Any]:
    rows = response_data(response)
    if not isinstance(rows, list):
        rows = []
    normalized: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        qty = int(float(row.get("netQty") or row.get("quantity") or 0))
        normalized.append(
            {
                "tradingsymbol": norm_symbol(
                    str(row.get("tradingSymbol") or row.get("tradingsymbol") or "")
                ),
                "quantity": qty,
                "average_price": float(
                    row.get("costPrice")
                    or row.get("averagePrice")
                    or row.get("buyAvg")
                    or 0.0
                ),
                "product": "CNC"
                if str(row.get("productType") or "").upper() == "CNC"
                else "MIS",
                "pnl": float(
                    row.get("realizedProfit")
                    or 0.0
                )
                + float(row.get("unrealizedProfit") or 0.0),
                "security_id": str(row.get("securityId") or ""),
                "_raw": row,
            }
        )
    return {"net": normalized, "day": []}


def normalize_dhan_candles(response: Any, interval_minutes: int) -> List[Dict[str, Any]]:
    data = response_data(response)
    if not isinstance(data, dict):
        return []
    opens = data.get("open") or []
    highs = data.get("high") or []
    lows = data.get("low") or []
    closes = data.get("close") or []
    volumes = data.get("volume") or []
    stamps = data.get("timestamp") or data.get("start_Time") or []
    size = min(len(opens), len(highs), len(lows), len(closes), len(stamps))
    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.now(ist)
    rows: List[Dict[str, Any]] = []
    for index in range(size):
        raw_stamp = stamps[index]
        if isinstance(raw_stamp, (int, float)):
            stamp = datetime.fromtimestamp(float(raw_stamp), tz=ist)
        else:
            stamp = datetime.fromisoformat(str(raw_stamp).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = ist.localize(stamp)
            else:
                stamp = stamp.astimezone(ist)
        if stamp + timedelta(minutes=interval_minutes) > now:
            continue
        rows.append(
            {
                "date": stamp,
                "open": float(opens[index]),
                "high": float(highs[index]),
                "low": float(lows[index]),
                "close": float(closes[index]),
                "volume": float(volumes[index]) if index < len(volumes) else 0.0,
            }
        )
    return rows


@dataclass
class DhanFeedService:
    user_id: int
    client_id: str
    access_token: str
    on_tick: Callable[[Dict[str, Any]], None]
    on_order_update: Callable[[Dict[str, Any]], None]
    on_state: Optional[Callable[[bool], None]] = None

    def __post_init__(self) -> None:
        self.context = DhanContext(self.client_id, self.access_token)
        self.feed: Optional[MarketFeed] = None
        self.order_update: Optional[OrderUpdate] = None
        self.security_ids: set[str] = set()
        self.feed_thread: Any = None
        self.order_task: Optional[asyncio.Task[None]] = None

    async def start(self, security_ids: Iterable[str]) -> None:
        self.security_ids.update(str(item) for item in security_ids if item)
        instruments = [
            (MarketFeed.NSE, security_id, MarketFeed.Full)
            for security_id in sorted(self.security_ids)
        ]

        def connected(_feed: MarketFeed) -> None:
            if self.on_state:
                self.on_state(True)

        def closed(_feed: MarketFeed) -> None:
            if self.on_state:
                self.on_state(False)

        def errored(_feed: MarketFeed, exc: Exception) -> None:
            if self.on_state:
                self.on_state(False)
            log.warning("Dhan market feed error: %s", exc)

        self.feed = MarketFeed(
            self.context,
            instruments,
            "v2",
            on_connect=connected,
            on_message=lambda _feed, packet: self.on_tick(packet or {}),
            on_close=closed,
            on_error=errored,
        )
        self.feed_thread = self.feed.start()

        self.order_update = OrderUpdate(self.context)
        self.order_update.on_update = self.on_order_update
        self.order_task = asyncio.create_task(
            self._run_order_updates(),
            name=f"dhan_order_updates_{self.user_id}",
        )

    async def _run_order_updates(self) -> None:
        while self.order_update is not None:
            try:
                await self.order_update.connect_order_update()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Dhan order update feed error: %s", exc)
            await asyncio.sleep(5)

    async def subscribe(self, security_ids: Iterable[str]) -> None:
        new_ids = {str(item) for item in security_ids if item} - self.security_ids
        if not new_ids:
            return
        self.security_ids.update(new_ids)
        if self.feed:
            self.feed.subscribe_symbols(
                [(MarketFeed.NSE, security_id, MarketFeed.Full) for security_id in new_ids]
            )

    async def stop(self) -> None:
        if self.feed:
            await asyncio.to_thread(self.feed.close_connection)
        self.feed = None
        if self.order_task:
            self.order_task.cancel()
            try:
                await self.order_task
            except asyncio.CancelledError:
                pass
        self.order_task = None
        self.order_update = None
        if self.on_state:
            self.on_state(False)
