from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
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
        self._master_frame: Optional[pd.DataFrame] = None
        self._lock = asyncio.Lock()
        self.loaded_at: Optional[datetime] = None

    INDEX_SECURITY_IDS = {
        "NIFTY": "13",
        "NIFTY50": "13",
        "BANKNIFTY": "25",
        "NIFTYBANK": "25",
        "NIFTY BANK": "25",
    }

    INDEX_DISPLAY = {
        "13": "NIFTY",
        "25": "BANKNIFTY",
    }

    async def ensure_loaded(self, force: bool = False) -> bool:
        if self.symbol_to_security and not force:
            return True
        async with self._lock:
            if self.symbol_to_security and not force:
                return True
            try:
                frame = await self._load_master_frame(force=force)
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
                for symbol, security_id in self.INDEX_SECURITY_IDS.items():
                    symbol_map[symbol] = security_id
                    security_map[security_id] = self.INDEX_DISPLAY.get(security_id, symbol)
                self.symbol_to_security = symbol_map
                self.security_to_symbol = security_map
                self.loaded_at = datetime.now()
                log.info("Loaded %s Dhan NSE equity instruments", len(symbol_map))
                return True
            except Exception as exc:
                log.error("Dhan instrument master load failed: %s", exc)
                return False

    async def _load_master_frame(self, force: bool = False) -> pd.DataFrame:
        if self._master_frame is not None and not force:
            return self._master_frame
        frame = await asyncio.to_thread(
            pd.read_csv,
            DHAN_SCRIP_MASTER_URL,
            low_memory=False,
        )
        self._master_frame = frame
        return frame

    async def security_id(self, symbol: str) -> Optional[str]:
        normalized = norm_symbol(symbol)
        if normalized in self.INDEX_SECURITY_IDS:
            return self.INDEX_SECURITY_IDS.get(normalized)
        await self.ensure_loaded()
        return self.symbol_to_security.get(normalized)

    def is_index_symbol(self, symbol: str) -> bool:
        return norm_symbol(symbol) in self.INDEX_SECURITY_IDS

    def index_security_id(self, symbol: str) -> Optional[str]:
        return self.INDEX_SECURITY_IDS.get(norm_symbol(symbol))

    async def atm_index_option(
        self,
        underlying: str,
        side: str,
        spot_price: float,
        today: Optional[date] = None,
    ) -> Optional[Dict[str, Any]]:
        normalized = norm_symbol(underlying)
        if normalized in {"NIFTY50", "NIFTY BANK"}:
            normalized = "NIFTY" if normalized == "NIFTY50" else "BANKNIFTY"
        if normalized not in {"NIFTY", "BANKNIFTY"}:
            return None
        option_type = "CE" if str(side).upper() == "BUY" else "PE"
        strike_step = 50 if normalized == "NIFTY" else 100
        atm_strike = round(float(spot_price) / strike_step) * strike_step
        frame = await self._load_master_frame()
        if frame.empty:
            return None
        today = today or datetime.now().date()

        rows = frame.copy()
        for col in ("SEM_TRADING_SYMBOL", "SEM_SEGMENT", "SEM_EXPIRY_DATE"):
            if col not in rows.columns:
                return None
        rows = rows[
            (rows["SEM_TRADING_SYMBOL"].astype(str).str.contains(normalized, na=False))
            & (rows["SEM_SEGMENT"].astype(str).str.upper() == "OPTIDX")
        ].copy()
        if rows.empty:
            return None

        rows["_expiry"] = pd.to_datetime(rows["SEM_EXPIRY_DATE"], errors="coerce").dt.date
        rows = rows[rows["_expiry"].notna() & (rows["_expiry"] >= today)]
        if rows.empty:
            return None

        if "SEM_OPTION_TYPE" in rows.columns:
            rows = rows[rows["SEM_OPTION_TYPE"].astype(str).str.upper().str.endswith(option_type)]
        else:
            rows = rows[rows["SEM_TRADING_SYMBOL"].astype(str).str.upper().str.endswith(option_type)]
        if rows.empty:
            return None

        strike_col = "SEM_STRIKE_PRICE" if "SEM_STRIKE_PRICE" in rows.columns else ""
        if strike_col:
            rows["_strike"] = pd.to_numeric(rows[strike_col], errors="coerce")
            rows = rows[rows["_strike"].notna()]
            rows["_strike_distance"] = (rows["_strike"] - atm_strike).abs()
        else:
            rows["_strike_distance"] = rows["SEM_TRADING_SYMBOL"].astype(str).str.extract(r"(\d+)(?:CE|PE)$")[0]
            rows["_strike_distance"] = pd.to_numeric(rows["_strike_distance"], errors="coerce").sub(atm_strike).abs()
            rows = rows[rows["_strike_distance"].notna()]
        if rows.empty:
            return None

        rows = rows.sort_values(["_expiry", "_strike_distance"])
        row = rows.iloc[0]
        security_id = _value(row, "SEM_SMST_SECURITY_ID", "SECURITY_ID")
        trading_symbol = norm_symbol(_value(row, "SEM_TRADING_SYMBOL", "TRADING_SYMBOL"))
        if not security_id or not trading_symbol:
            return None
        return {
            "security_id": security_id,
            "trading_symbol": trading_symbol,
            "underlying": normalized,
            "option_type": option_type,
            "strike": float(row.get("_strike", atm_strike) or atm_strike),
            "expiry": str(row.get("_expiry") or ""),
        }

    def symbol(self, security_id: Any) -> str:
        return self.security_to_symbol.get(str(security_id), self.INDEX_DISPLAY.get(str(security_id), ""))


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
    if isinstance(data, list):
        rows: List[Dict[str, Any]] = []
        ist = pytz.timezone("Asia/Kolkata")
        now = datetime.now(ist)
        for row in data:
            if not isinstance(row, dict):
                continue
            raw_stamp = row.get("timestamp") or row.get("start_Time") or row.get("date") or row.get("time")
            if raw_stamp is None:
                continue
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
                    "open": float(row.get("open") or row.get("Open") or 0.0),
                    "high": float(row.get("high") or row.get("High") or 0.0),
                    "low": float(row.get("low") or row.get("Low") or 0.0),
                    "close": float(row.get("close") or row.get("Close") or 0.0),
                    "volume": float(row.get("volume") or row.get("Volume") or 0.0),
                }
            )
        return rows
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


def resample_intraday_candles(candles: List[Dict[str, Any]], interval_minutes: int) -> List[Dict[str, Any]]:
    """Aggregate 1-minute/session candles into custom intraday bars."""
    interval = max(1, int(interval_minutes))
    if interval <= 1:
        return list(candles)
    ist = pytz.timezone("Asia/Kolkata")
    buckets: Dict[tuple, Dict[str, Any]] = {}
    for candle in sorted(candles, key=lambda item: item.get("date") or datetime.min):
        stamp = candle.get("date")
        if not isinstance(stamp, datetime):
            continue
        stamp = stamp.astimezone(ist) if stamp.tzinfo else ist.localize(stamp)
        session_start = stamp.replace(hour=9, minute=15, second=0, microsecond=0)
        minutes_from_open = int((stamp - session_start).total_seconds() // 60)
        if minutes_from_open < 0:
            continue
        bucket_offset = (minutes_from_open // interval) * interval
        bucket_time = session_start + timedelta(minutes=bucket_offset)
        key = (bucket_time.date().isoformat(), bucket_time.strftime("%H:%M"))
        current = buckets.get(key)
        if not current:
            buckets[key] = {
                "date": bucket_time,
                "open": float(candle.get("open") or 0.0),
                "high": float(candle.get("high") or 0.0),
                "low": float(candle.get("low") or 0.0),
                "close": float(candle.get("close") or 0.0),
                "volume": float(candle.get("volume") or 0.0),
            }
            continue
        current["high"] = max(float(current["high"]), float(candle.get("high") or 0.0))
        current["low"] = min(float(current["low"]), float(candle.get("low") or 0.0))
        current["close"] = float(candle.get("close") or 0.0)
        current["volume"] = float(current.get("volume") or 0.0) + float(candle.get("volume") or 0.0)
    return sorted(buckets.values(), key=lambda item: item.get("date") or datetime.min)


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
