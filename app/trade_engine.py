# app/trade_engine.py
from __future__ import annotations

import asyncio
import time
import uuid
import logging
import json
import math
import pytz
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional, Literal, List, Tuple, Set
from dataclasses import fields as _dc_fields
from kiteconnect import KiteConnect  # type: ignore
from dhanhq import dhanhq  # type: ignore

# Keep dependencies intact (same modules you already use)
from .redis_store import RedisStore, norm_alert_name, norm_symbol
from .stock_sector import STOCK_INDEX_MAPPING
import os 
import re
from datetime import timedelta
from .custom_strategy import evaluate_precision_sniper, resolve_settings
from .dhan_broker import (
    DHAN_INSTRUMENTS,
    dhan_client,
    normalize_dhan_candles,
    normalize_dhan_positions,
    order_id_from_response,
    response_data,
)

log = logging.getLogger("trade_engine")

Side = Literal["BUY", "SELL"]
Product = Literal["MIS", "CNC"]
QtyMode = Literal["QTY", "CAPITAL"]


# =========================
# Normalization (ONE SOURCE OF TRUTH)
# =========================
def normalize_alert_key(name: str) -> str:
    return norm_alert_name(name or "")


def _j(**k: Any) -> str:
    try:
        return json.dumps(k, separators=(",", ":"), ensure_ascii=False)
    except Exception:
        return str(k)


def _fmt_pos(p: "Position") -> str:
    return (
        f"{p.symbol} | {p.side} {p.qty} {p.product} | "
        f"entry={p.entry_price:.2f} ltp={p.ltp:.2f} pnl={p.pnl:.2f} | "
        f"tgt={p.target_price:.2f} sl={p.sl_price:.2f} "
        f"hi={p.highest:.2f} lo={p.lowest:.2f} tsl%={p.tsl_pct:.2f}"
    )

# -----------------------------
# Color helpers (ANSI)
# -----------------------------
_NO_COLOR = bool(os.getenv("NO_COLOR", "").strip())

def _c(code: str, s: str) -> str:
    if _NO_COLOR:
        return s
    return f"\x1b[{code}m{s}\x1b[0m"

def _green(s: str) -> str: return _c("32", s)
def _red(s: str) -> str: return _c("31", s)
def _yellow(s: str) -> str: return _c("33", s)
def _cyan(s: str) -> str: return _c("36", s)
def _magenta(s: str) -> str: return _c("35", s)
def _bold(s: str) -> str: return _c("1", s)
def _dim(s: str) -> str: return _c("2", s)
def _bg_blue(s: str) -> str:
    return _c("1;37;44", s)

def _bg_yellow(s: str) -> str:
    return _c("1;30;43", s)


def _bg_magenta(s: str) -> str:
    return _c("1;37;45", s)


def _fmt_side(side: str) -> str:
    return _green(side) if side == "BUY" else _red(side)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

def _vis_len(s: str) -> int:
    return len(_ANSI_RE.sub("", s))

def _pad(s: str, width: int) -> str:
    return s + (" " * max(0, width - _vis_len(s)))


def _fmt_pnl(pnl: float) -> str:
    if pnl > 0:
        return _green(f"{pnl:.2f}")
    if pnl < 0:
        return _red(f"{pnl:.2f}")
    return f"{pnl:.2f}"

def _fmt_pct(x: float) -> str:
    # positive green, negative red, near zero yellow
    if x > 0.05:
        return _green(f"{x:.2f}%")
    if x < -0.05:
        return _red(f"{x:.2f}%")
    return _yellow(f"{x:.2f}%")



def _safe_symbol(raw: str) -> str:
    """
    Manual-squareoff UI sometimes sends: NSE:SBIN, SBIN-EQ, etc.
    We keep norm_symbol as primary.
    """
    return norm_symbol(raw)


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    return default


def _pct_dist(cur: float, ref: float) -> float:
    if ref == 0:
        return 0.0
    return ((cur - ref) / ref) * 100.0


def _partial_quantities(total_qty: int, tp1_pct: float, tp2_pct: float) -> Tuple[int, int, int]:
    """Round TP1/TP2 down and assign every remaining share to TP3."""
    total = max(0, int(total_qty))
    tp1 = min(total, max(0, int(total * max(0.0, float(tp1_pct)) / 100.0)))
    tp2 = min(total - tp1, max(0, int(total * max(0.0, float(tp2_pct)) / 100.0)))
    return tp1, tp2, total - tp1 - tp2


def _stepwise_anchor_long(entry: float, highest: float, step_pct: float) -> float:
    """
    Quantize the trailing anchor in steps of `step_pct` moves from entry.
    Example (BUY): step_pct=0.65 means anchor updates only at +0.65%, +1.30%, ...
    """
    if entry <= 0 or highest <= 0 or step_pct <= 0:
        return highest
    step = float(step_pct) / 100.0
    if step <= 0:
        return highest
    idx = int(math.floor(((float(highest) / float(entry)) - 1.0) / step))
    if idx < 0:
        idx = 0
    return float(entry) * (1.0 + (idx * step))


def _stepwise_anchor_short(entry: float, lowest: float, step_pct: float) -> float:
    """
    Quantize the trailing anchor in steps of `step_pct` moves from entry.
    Example (SELL): step_pct=0.65 means anchor updates only at -0.65%, -1.30%, ...
    """
    if entry <= 0 or lowest <= 0 or step_pct <= 0:
        return lowest
    step = float(step_pct) / 100.0
    if step <= 0:
        return lowest
    idx = int(math.floor((1.0 - (float(lowest) / float(entry))) / step))
    if idx < 0:
        idx = 0
    return float(entry) * (1.0 - (idx * step))


def _is_within_entry_window(start_time: str, end_time: str) -> bool:
    """
    Check if current IST time is within the entry time window.
    
    Args:
        start_time: Entry start time in HH:MM format (e.g., "09:15")
        end_time: Entry end time in HH:MM format (e.g., "15:15")
    
    Returns:
        True if current time is within window, False otherwise
    """
    try:
        import pytz
        ist = pytz.timezone("Asia/Kolkata")
        now = datetime.now(ist)
        
        # Parse start and end times
        start_parts = start_time.strip().split(":")
        end_parts = end_time.strip().split(":")
        
        if len(start_parts) != 2 or len(end_parts) != 2:
            # Invalid format, allow by default
            return True
        
        start_hour, start_min = int(start_parts[0]), int(start_parts[1])
        end_hour, end_min = int(end_parts[0]), int(end_parts[1])
        
        # Create time objects for comparison
        current_minutes = now.hour * 60 + now.minute
        start_minutes = start_hour * 60 + start_min
        end_minutes = end_hour * 60 + end_min
        
        # Check if current time is within window
        return start_minutes <= current_minutes <= end_minutes
        
    except Exception as e:
        log.debug("TIME_WINDOW_CHECK_FAIL | err=%s", e)
        # On error, allow by default
        return True


# =========================
# Data models
# =========================
@dataclass
class AlertConfig:
    alert_name: str
    enabled: bool = True

    direction: Literal["LONG", "SHORT", "BOTH"] = "LONG"   # LONG->BUY, SHORT->SELL
    product: Product = "MIS"                       # MIS / CNC

    qty_mode: QtyMode = "CAPITAL"
    capital: float = 20000.0
    qty: int = 1

    # monitoring (MIS only)
    target_pct: float = 1.0
    stop_loss_pct: float = 0.7
    trailing_sl_pct: float = 0.5
    tsl_stepwise: bool = False

    trade_limit_per_day: int = 5

    # sector filter
    sector_filter_on: bool = False
    top_n_sector: int = 2

    # entry time window (IST format HH:MM)
    entry_start_time: str = "09:15"
    entry_end_time: str = "15:15"
    strategy_mode: Literal["CLASSIC", "PRECISION_SNIPER"] = "CLASSIC"
    custom_settings: Dict[str, Any] = None  # type: ignore[assignment]

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "AlertConfig":
        raw_name = str(d.get("alert_name") or d.get("name") or d.get("alert") or "UNKNOWN").strip()

        direction = str(d.get("direction", "LONG") or "LONG").strip().upper()
        if direction not in ("LONG", "SHORT", "BOTH"):
            direction = "LONG"

        p_raw = str(d.get("product", "MIS") or "MIS").strip().upper()
        if p_raw in ("CNC", "DELIVERY", "DEMAT", "CASH"):
            product: Product = "CNC"
        else:
            product = "MIS"

        qty_mode = str(d.get("qty_mode", "CAPITAL") or "CAPITAL").strip().upper()
        if qty_mode not in ("QTY", "CAPITAL"):
            qty_mode = "CAPITAL"

        strategy_mode = str(d.get("strategy_mode", "CLASSIC") or "CLASSIC").strip().upper()
        if strategy_mode not in ("CLASSIC", "PRECISION_SNIPER"):
            strategy_mode = "CLASSIC"

        return AlertConfig(
            alert_name=normalize_alert_key(raw_name),
            enabled=_as_bool(d.get("enabled"), True),
            direction=direction,  # type: ignore[arg-type]
            product=product,
            qty_mode=qty_mode,  # type: ignore[arg-type]
            capital=float(d.get("capital", 20000.0) or 0.0),
            qty=int(d.get("qty", 1) or 1),
            target_pct=float(d.get("target_pct", 1.0) or 0.0),
            stop_loss_pct=float(d.get("stop_loss_pct", 0.7) or 0.0),
            trailing_sl_pct=float(d.get("trailing_sl_pct", 0.5) or 0.0),
            tsl_stepwise=_as_bool(d.get("tsl_stepwise"), False),
            trade_limit_per_day=int(d.get("trade_limit_per_day", 3) or 0),
            sector_filter_on=_as_bool(d.get("sector_filter_on"), False),
            top_n_sector=int(d.get("top_n_sector", 2) or 2),
            entry_start_time=str(d.get("entry_start_time", "09:15") or "09:15").strip(),
            entry_end_time=str(d.get("entry_end_time", "15:15") or "15:15").strip(),
            strategy_mode=strategy_mode,  # type: ignore[arg-type]
            custom_settings=dict(d),
        )


@dataclass
class Position:
    trade_id: str
    user_id: int
    symbol: str
    alert_name: str

    side: Side
    product: Product
    qty: int

    entry_price: float
    entry_order_id: str = ""

    # monitoring (MIS only)
    target_price: float = 0.0
    sl_price: float = 0.0
    tsl_pct: float = 0.0
    tsl_stepwise: bool = False
    highest: float = 0.0
    lowest: float = 0.0

    status: Literal["OPEN", "EXIT_CONDITIONS_MET", "EXITING", "CLOSED", "REJECTED", "ERROR"] = "OPEN"
    exit_reason: str = ""
    exit_order_id: str = ""
    alert_time: str = ""
    created_ts: float = 0.0
    updated_ts: float = 0.0

    cfg_target_pct: float = 0.0
    cfg_sl_pct: float = 0.0
    cfg_tsl_pct: float = 0.0
    cfg_tsl_stepwise: bool = False


    ltp: float = 0.0
    pnl: float = 0.0
    realized_pnl: float = 0.0
    sector: str = ""  # Sector/index group the stock belongs to
    strategy_mode: str = "CLASSIC"
    signal_price: float = 0.0
    signal_score: float = 0.0
    signal_max_score: float = 0.0
    signal_grade: str = ""
    signal_preset: str = ""
    signal_volatility: str = ""
    signal_candle_time: str = ""
    tp1_price: float = 0.0
    tp2_price: float = 0.0
    tp3_price: float = 0.0
    trail_price: float = 0.0
    tp1_hit: bool = False
    tp2_hit: bool = False
    tp3_hit: bool = False
    custom_use_trail: bool = True
    custom_full_exit_tp3: bool = True
    initial_qty: int = 0
    custom_partial_profit_enabled: bool = False
    partial_tp1_pct: float = 50.0
    partial_tp2_pct: float = 25.0
    partial_tp3_pct: float = 25.0
    tp1_exit_qty: int = 0
    tp2_exit_qty: int = 0
    tp3_exit_qty: int = 0
    tp1_booked: bool = False
    tp2_booked: bool = False
    tp3_booked: bool = False
    tp1_exit_order_id: str = ""
    tp2_exit_order_id: str = ""
    tp3_exit_order_id: str = ""

    def to_public(self) -> Dict[str, Any]:
        return asdict(self)


# =========================
# Ultra-fast order worker
# =========================
class OrderWorker:
    """
    Single async queue that offloads blocking KiteConnect calls to threadpool.
    Prevents event-loop stalls.
    """

    def __init__(self) -> None:
        self.q: "asyncio.Queue[Tuple[asyncio.Future, Any, Tuple[Any, ...], Dict[str, Any]]]" = asyncio.Queue()
        self.task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self.task and not self.task.done():
            return
        self.task = asyncio.create_task(self._run(), name="order_worker")

    async def stop(self) -> None:
        if not self.task:
            return
        self.task.cancel()
        try:
            await self.task
        except asyncio.CancelledError:
            pass
        self.task = None

    async def submit(self, fn, *args, **kwargs):
        fut = asyncio.get_running_loop().create_future()
        await self.q.put((fut, fn, args, kwargs))
        return await fut

    async def _run(self):
        while True:
            fut, fn, args, kwargs = await self.q.get()
            try:
                res = await asyncio.to_thread(fn, *args, **kwargs)
                if not fut.cancelled():
                    fut.set_result(res)
            except Exception as e:
                if not fut.cancelled():
                    fut.set_exception(e)


class MarketDataWorker:
    """Run blocking market-data calls concurrently without blocking order placement."""

    def __init__(self, max_concurrency: int = 4) -> None:
        self._limit = asyncio.Semaphore(max(1, int(max_concurrency)))

    async def submit(self, fn, *args, **kwargs):
        async with self._limit:
            return await asyncio.to_thread(fn, *args, **kwargs)


# =========================
# Trade Engine
# =========================
class TradeEngine:
    """
    Includes:
    - Unified alert-name normalization
    - Lazy ensure Zerodha connected
    - No REST LTP: waits for tick (CAPITAL mode)
    - mark_open AFTER successful order placement
    - Always releases locks
    - Exit de-bounced + lock-based safe exit
    - Rich monitoring logs: who is near/hit target/sl/tsl
    - Manual squareoff fixed (works even if inflight)
    """

    def __init__(
        self,
        user_id: int,
        store: RedisStore,
        broadcast_cb: Optional[Any] = None,
        token_resolver: Optional[Any] = None,
        token_ready_cb: Optional[Any] = None,
    ) -> None:
        self.user_id = int(user_id)
        self.store = store
        self.broadcast_cb = broadcast_cb
        self.token_resolver = token_resolver
        self.token_ready_cb = token_ready_cb

        self.api_key: str = ""
        self.access_token: str = ""
        self.kite: Optional[KiteConnect] = None
        self.broker: str = "ZERODHA"
        self.dhan_client_id: str = ""
        self.dhan_access_token: str = ""
        self.dhan: Optional[dhanhq] = None

        self.ticks: Dict[str, Dict[str, float]] = {}
        self.positions: Dict[str, Position] = {}

        # sector perf (incremental)
        self.sym_sector: Dict[str, str] = dict(STOCK_INDEX_MAPPING)
        self.sym_pct: Dict[str, float] = {}
        self.sector_sum: Dict[str, float] = {}
        self.sector_cnt: Dict[str, int] = {}

        self.order_worker = OrderWorker()
        self.market_data_worker = MarketDataWorker(max_concurrency=4)

        # exit guards
        self._exit_inflight: Dict[str, bool] = {}
        self._exit_signal_sent: Dict[str, bool] = {}
        # entry reconciliation (avoid repeated REST calls)
        self._recon_inflight: Dict[str, bool] = {}
        # monitoring log controls
        self._mon_last_log: Dict[str, float] = {}
        self.monitor_log_interval_sec: float = 10.0   # per symbol

        # tick visibility (first tick log)
        self._first_tick_logged: Dict[str, bool] = {}
        
        # Sector ranking periodic log
        self._last_sector_rank_log: float = 0.0
        self.sector_rank_log_interval_sec: float = 30.0  # Log every 30 seconds

        # Kill-switch / panic coordination (avoid concurrent triggers)
        self._kill_trigger_lock = asyncio.Lock()
        self._custom_last_signal: Dict[Tuple[str, str], str] = {}
        self._partial_inflight: Dict[Tuple[str, str], bool] = {}

        # MTM-based daily P&L guard
        self._pnl_exit_task: Optional["asyncio.Task[None]"] = None

    # ---------------- broker setup ----------------
    async def configure_kite(self) -> None:
        await self.configure_broker()

    async def configure_broker(self) -> None:
        try:
            self.broker = await self.store.load_broker(self.user_id)
        except Exception:
            self.broker = "ZERODHA"

        creds = await self.store.load_credentials(self.user_id)
        api_key = (creds.get("api_key") or "").strip()

        token = ""
        try:
            token = (await self.store.load_access_token(self.user_id)).strip()
        except Exception:
            token = ""

        if not token:
            token = (creds.get("access_token") or "").strip()

        self.api_key = api_key
        self.access_token = token
        dhan_creds = await self.store.load_dhan_credentials(self.user_id)
        self.dhan_client_id = str(dhan_creds.get("client_id") or "").strip()
        self.dhan_access_token = str(dhan_creds.get("access_token") or "").strip()
        self.kite = None
        self.dhan = None

        await self.order_worker.start()
        self._ensure_pnl_exit_monitor_started()

    async def close(self) -> None:
        if self._pnl_exit_task:
            self._pnl_exit_task.cancel()
            try:
                await self._pnl_exit_task
            except asyncio.CancelledError:
                pass
            self._pnl_exit_task = None
        await self.order_worker.stop()

    def _ensure_pnl_exit_monitor_started(self) -> None:
        if self._pnl_exit_task and not self._pnl_exit_task.done():
            return
        try:
            self._pnl_exit_task = asyncio.create_task(self._pnl_exit_monitor(), name=f"pnl_exit_{self.user_id}")
        except Exception:
            self._pnl_exit_task = None

    async def _pnl_exit_monitor(self) -> None:
        """
        Poll Kite MTM/P&L (positions) every ~2s.
        If MTM >= max_profit OR MTM <= -max_loss => squareoff all + enable kill switch for the day.
        """
        while True:
            try:
                await asyncio.sleep(2.0)

                # If already killed for the day, no need to check further.
                if await self.store.is_kill(self.user_id):
                    continue

                cfg = await self.store.get_pnl_exit_config(self.user_id)
                if not bool(cfg.get("enabled", False)):
                    continue

                max_profit = float(cfg.get("max_profit", 0.0) or 0.0)
                max_loss = float(cfg.get("max_loss", 0.0) or 0.0)
                if max_profit <= 0 and max_loss <= 0:
                    continue

                ok = await self._ensure_broker_ready()
                if not ok:
                    continue

                data = await self._broker_positions()
                rows = list((data or {}).get("net") or [])
                mtm = 0.0
                for r in rows:
                    try:
                        mtm += float(r.get("pnl") or r.get("m2m") or 0.0)
                    except Exception:
                        continue

                trigger: Optional[str] = None
                if max_profit > 0 and mtm >= max_profit:
                    trigger = "MAX_PROFIT"
                elif max_loss > 0 and mtm <= (-1.0 * abs(max_loss)):
                    trigger = "MAX_LOSS"

                if trigger:
                    log.warning(
                        "PNL_EXIT_TRIGGER | user=%s trigger=%s mtm=%.2f max_profit=%.2f max_loss=%.2f",
                        self.user_id,
                        trigger,
                        mtm,
                        max_profit,
                        max_loss,
                    )
                    await self.trigger_kill_switch(reason=f"PNL_EXIT:{trigger}:MTM={mtm:.2f}", squareoff_first=True)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.debug("PNL_EXIT_MONITOR_FAIL | user=%s err=%s", self.user_id, e)

    async def rehydrate_open_positions(self) -> List[str]:
        restored: List[str] = []
        try:
            rows = await self.store.list_positions(self.user_id)
        except Exception as e:
            log.warning("REHYDRATE_FAIL | user=%s err=%s", self.user_id, e)
            return restored

        for r in rows or []:
            try:
                status = str(r.get("status") or "").upper()
                if status not in ("OPEN", "EXIT_CONDITIONS_MET", "EXITING"):
                    continue

                sym = norm_symbol(r.get("symbol", ""))
                if not sym:
                    continue

                data = {}
                for k, f in Position.__dataclass_fields__.items():  # type: ignore[attr-defined]
                    data[k] = r.get(k, f.default)

                data["user_id"] = int(self.user_id)
                data["symbol"] = sym

                pos = Position(**data)
                self.positions[sym] = pos
                restored.append(sym)
            except Exception as e:
                log.debug("REHYDRATE_ROW_FAIL | user=%s err=%s row=%s", self.user_id, e, r)

        return restored

    # ---------------- broker helpers ----------------
    async def _ensure_kite_ready(self) -> bool:
        if not self.api_key or not self.access_token:
            return False
        if not self.kite:
            self.kite = KiteConnect(api_key=self.api_key)
            self.kite.set_access_token(self.access_token)
        return True

    async def _ensure_dhan_ready(self) -> bool:
        if not self.dhan_client_id or not self.dhan_access_token:
            return False
        if not self.dhan:
            self.dhan = dhan_client(self.dhan_client_id, self.dhan_access_token)
        return True

    async def _ensure_broker_ready(self) -> bool:
        if self.broker == "DHAN":
            return await self._ensure_dhan_ready()
        return await self._ensure_kite_ready()

    async def _kite_positions(self) -> Dict[str, Any]:
        ok = await self._ensure_kite_ready()
        if not ok or not self.kite:
            raise RuntimeError("ZERODHA_NOT_CONNECTED")
        return await self.market_data_worker.submit(self.kite.positions)

    async def _broker_positions(self) -> Dict[str, Any]:
        if self.broker == "DHAN":
            if not await self._ensure_dhan_ready() or not self.dhan:
                raise RuntimeError("DHAN_NOT_CONNECTED")
            response = await self.market_data_worker.submit(self.dhan.get_positions)
            return normalize_dhan_positions(response)
        return await self._kite_positions()

    async def _place_order(self, symbol: str, side: Side, qty: int, product: Product) -> Any:
        if self.broker == "DHAN":
            if not await self._ensure_dhan_ready() or not self.dhan:
                raise RuntimeError("DHAN_NOT_CONNECTED")
            security_id = await DHAN_INSTRUMENTS.security_id(symbol)
            if not security_id:
                raise RuntimeError("DHAN_SECURITY_ID_MISSING")
            response = await self.order_worker.submit(
                self.dhan.place_order,
                security_id=str(security_id),
                exchange_segment=self.dhan.NSE,
                transaction_type=self.dhan.BUY if side == "BUY" else self.dhan.SELL,
                quantity=int(qty),
                order_type=self.dhan.MARKET,
                product_type=self.dhan.CNC if product == "CNC" else self.dhan.INTRA,
                price=0,
            )
            return order_id_from_response(response)

        ok = await self._ensure_kite_ready()
        if not ok or not self.kite:
            raise RuntimeError("ZERODHA_NOT_CONNECTED")
        return await self.order_worker.submit(
            self.kite.place_order,
            variety="regular",
            exchange="NSE",
            tradingsymbol=str(symbol),
            transaction_type=str(side),
            quantity=int(qty),
            product=str(product),
            order_type="MARKET",
            market_protection=-1
        )

    async def _fetch_positions_avg(self, symbol: str) -> float:
        symbol = norm_symbol(symbol)
        try:
            data = await self._broker_positions()
        except Exception:
            return 0.0
        rows = list(data.get("net") or []) + list(data.get("day") or [])
        for r in rows:
            tsym = norm_symbol(str(r.get("tradingsymbol") or ""))
            if tsym != symbol:
                continue
            qty = int(r.get("quantity") or 0)
            if qty == 0:
                continue
            avg = float(r.get("average_price") or r.get("buy_price") or 0.0)
            return avg
        return 0.0

    async def _fetch_ltp(self, symbol: str) -> float:
        symbol = norm_symbol(symbol)
        for _ in range(3):
            tick = self.ticks.get(symbol)
            if tick and float(tick.get("ltp", 0.0)) > 0:
                return float(tick["ltp"])
            if self.broker == "DHAN":
                if await self._ensure_dhan_ready() and self.dhan:
                    try:
                        security_id = await DHAN_INSTRUMENTS.security_id(symbol)
                        if security_id:
                            response = await self.market_data_worker.submit(
                                self.dhan.ohlc_data,
                                {"NSE_EQ": [int(security_id)]},
                            )
                            data = response_data(response)
                            stack = [data]
                            while stack:
                                value = stack.pop()
                                if isinstance(value, dict):
                                    for key in ("last_price", "lastPrice", "ltp", "LTP"):
                                        if key in value and float(value[key] or 0) > 0:
                                            return float(value[key])
                                    stack.extend(value.values())
                                elif isinstance(value, list):
                                    stack.extend(value)
                    except Exception:
                        pass
                await asyncio.sleep(0.5)
                continue
            ok = await self._ensure_kite_ready()
            if ok and self.kite:
                try:
                    data = await self.market_data_worker.submit(self.kite.ltp, f"NSE:{symbol}")
                    row = data.get(f"NSE:{symbol}") or {}
                    last_price = float(row.get("last_price") or 0.0)
                    if last_price > 0:
                        return last_price
                except Exception:
                    pass
            await asyncio.sleep(0.5)
        return 0.0

    async def _fetch_historical_candles(
        self,
        symbol: str,
        interval: str = "5minute",
        lookback_days: int = 12,
    ) -> List[Dict[str, Any]]:
        if self.broker == "DHAN":
            if not await self._ensure_dhan_ready() or not self.dhan:
                raise RuntimeError("DHAN_NOT_CONNECTED")
            security_id = await DHAN_INSTRUMENTS.security_id(symbol)
            if not security_id:
                raise RuntimeError("DHAN_SECURITY_ID_MISSING")
            interval_minutes = 5
            try:
                interval_minutes = int(str(interval).replace("minute", ""))
            except Exception:
                pass
            ist = pytz.timezone("Asia/Kolkata")
            now = datetime.now(ist)
            start_day = now - timedelta(days=min(5, max(1, int(lookback_days))))
            start = start_day.replace(hour=9, minute=15, second=0, microsecond=0)
            response = await self.market_data_worker.submit(
                self.dhan.intraday_minute_data,
                security_id=str(security_id),
                exchange_segment=self.dhan.NSE,
                instrument_type="EQUITY",
                from_date=start.strftime("%Y-%m-%d %H:%M:%S"),
                to_date=now.strftime("%Y-%m-%d %H:%M:%S"),
                interval=interval_minutes,
                oi=False,
            )
            return normalize_dhan_candles(response, interval_minutes)

        ok = await self._ensure_kite_ready()
        if not ok or not self.kite:
            raise RuntimeError("ZERODHA_NOT_CONNECTED")
        token = None
        if self.token_resolver:
            token = self.token_resolver(norm_symbol(symbol))
        if not token and self.token_ready_cb:
            await self.token_ready_cb(self.user_id)
            if self.token_resolver:
                token = self.token_resolver(norm_symbol(symbol))
        if not token:
            try:
                token = await self.store.get_symbol_token(symbol)
            except Exception:
                token = None
        if not token:
            raise RuntimeError("INSTRUMENT_TOKEN_MISSING")

        ist = pytz.timezone("Asia/Kolkata")
        now = datetime.now(ist)
        start = now - timedelta(days=max(7, int(lookback_days)))
        rows = await self.market_data_worker.submit(
            self.kite.historical_data,
            instrument_token=int(token),
            from_date=start,
            to_date=now,
            interval=interval,
            continuous=False,
            oi=False,
        )
        interval_minutes = 5
        if interval.endswith("minute"):
            try:
                interval_minutes = int(interval[:-6])
            except Exception:
                interval_minutes = 5
        closed: List[Dict[str, Any]] = []
        for row in rows or []:
            stamp = row.get("date")
            if isinstance(stamp, datetime):
                if stamp.tzinfo is None:
                    stamp = ist.localize(stamp)
                if stamp + timedelta(minutes=interval_minutes) > now:
                    continue
            closed.append(dict(row))
        return closed

    async def on_chartink_alert(self, alert_name: str, symbols: List[str], ts: str = "") -> List[Dict[str, Any]]:
        alert_key = normalize_alert_key(alert_name)
        cfg_raw = await self.store.get_alert_config(self.user_id, alert_key)
        if not cfg_raw:
            return [{"symbol": s, "status": "ERROR", "reason": "CFG_MISSING"} for s in symbols]

        # Daily kill switch (e.g., triggered by max MTM profit/loss)
        if await self.store.is_kill(self.user_id):
            return [{"symbol": s, "status": "SKIPPED", "reason": "KILL_SWITCH"} for s in symbols]

        cfg = AlertConfig.from_dict(cfg_raw)
        if not cfg.enabled:
            return [{"symbol": s, "status": "SKIPPED", "reason": "DISABLED"} for s in symbols]

        if not _is_within_entry_window(cfg.entry_start_time, cfg.entry_end_time):
            return [{"symbol": s, "status": "SKIPPED", "reason": "ENTRY_WINDOW"} for s in symbols]

        results: List[Dict[str, Any]] = []
        for raw in symbols:
            sym = norm_symbol(raw)
            if not sym:
                results.append({"symbol": raw, "status": "ERROR", "reason": "BAD_SYMBOL"})
                continue

            # Per-symbol entry lock (also enforces kill-switch via Lua, if available)
            lock_acquired = False
            try:
                lk = await self.store.acquire_lock(self.user_id, sym, "entry", ttl_ms=5000)
                if lk == -2:
                    results.append({"symbol": sym, "status": "SKIPPED", "reason": "KILL_SWITCH"})
                    continue
                if lk == 0:
                    results.append({"symbol": sym, "status": "SKIPPED", "reason": "BUSY"})
                    continue
                lock_acquired = True
            except Exception:
                # Store may not implement lock (tests/local). Proceed without it.
                lock_acquired = False

            try:
                custom_signal = None
                custom_meta: Dict[str, Any] = {}
                custom_settings: Dict[str, Any] = {}

                # sector filter
                if cfg.sector_filter_on:
                    sector = self.sym_sector.get(sym, "")
                    ranked = self.get_sector_rank()
                    top_secs = [sec for sec, _ in ranked[: max(1, int(cfg.top_n_sector or 1))]]
                    if sector and sector not in top_secs:
                        results.append({"symbol": sym, "status": "SKIPPED", "reason": "SECTOR_FILTER"})
                        continue

                if cfg.strategy_mode == "PRECISION_SNIPER":
                    try:
                        candles = await self._fetch_historical_candles(sym, "5minute", 15)
                        custom_settings = resolve_settings(cfg.custom_settings or {})
                        htf_minutes = int(custom_settings.get("htf_minutes", 5) or 5)
                        htf_candles = candles
                        if htf_minutes > 5:
                            htf_candles = await self._fetch_historical_candles(
                                sym,
                                f"{htf_minutes}minute",
                                30,
                            )
                        custom_signal, custom_meta = evaluate_precision_sniper(
                            candles,
                            cfg.custom_settings or {},
                            htf_candles,
                        )
                    except Exception as e:
                        results.append({"symbol": sym, "status": "ERROR", "reason": f"CUSTOM_DATA_FAIL:{e}"})
                        continue

                    if not custom_signal:
                        results.append(
                            {
                                "symbol": sym,
                                "status": "SKIPPED",
                                "reason": str(custom_meta.get("reason") or "CUSTOM_ENTRY_CHECK_FAILED"),
                                "custom": custom_meta,
                            }
                        )
                        continue
                    if cfg.direction == "LONG" and custom_signal.side != "BUY":
                        results.append({"symbol": sym, "status": "SKIPPED", "reason": "CUSTOM_DIRECTION_FILTER"})
                        continue
                    if cfg.direction == "SHORT" and custom_signal.side != "SELL":
                        results.append({"symbol": sym, "status": "SKIPPED", "reason": "CUSTOM_DIRECTION_FILTER"})
                        continue
                    signal_key = (alert_key, sym)
                    if self._custom_last_signal.get(signal_key) == custom_signal.candle_time:
                        results.append({"symbol": sym, "status": "SKIPPED", "reason": "CUSTOM_DUPLICATE_CANDLE"})
                        continue

                # already open
                pos_existing = self.positions.get(sym)
                if pos_existing and pos_existing.status in ("OPEN", "EXIT_CONDITIONS_MET", "EXITING"):
                    results.append({"symbol": sym, "status": "SKIPPED", "reason": "ALREADY_OPEN"})
                    continue
                if await self.store.get_open(self.user_id, sym):
                    results.append({"symbol": sym, "status": "SKIPPED", "reason": "ALREADY_OPEN"})
                    continue

                ltp = await self._fetch_ltp(sym)
                if ltp <= 0:
                    results.append({"symbol": sym, "status": "ERROR", "reason": "NO_LTP"})
                    continue

                qty = 0
                if cfg.qty_mode == "QTY":
                    qty = int(cfg.qty)
                else:
                    if ltp <= 0:
                        results.append({"symbol": sym, "status": "ERROR", "reason": "NO_LTP"})
                        continue
                    qty = int(float(cfg.capital) / float(ltp))
                if qty <= 0:
                    results.append({"symbol": sym, "status": "ERROR", "reason": "ZERO_QTY"})
                    continue

                # Count only candidates that passed validation and are ready
                # for order placement. The per-symbol entry lock prevents races.
                allowed = await self.store.allow_trade(self.user_id, alert_key, int(cfg.trade_limit_per_day))
                if not allowed:
                    results.append({"symbol": sym, "status": "SKIPPED", "reason": "TRADE_LIMIT"})
                    continue

                side: Side
                if custom_signal:
                    side = custom_signal.side  # type: ignore[assignment]
                else:
                    side = "BUY" if cfg.direction == "LONG" else "SELL"

                # place order
                try:
                    oid = await self._place_order(sym, side, qty, cfg.product)
                except Exception as e:
                    results.append({"symbol": sym, "status": "ERROR", "reason": f"ORDER_FAIL:{e}"})
                    continue

                entry = float(ltp or 0.0)
                target_price = 0.0
                sl_price = 0.0
                if custom_signal:
                    target_price = float(custom_signal.tp3)
                    sl_price = float(custom_signal.stop_loss)
                elif entry > 0 and cfg.target_pct > 0:
                    if side == "BUY":
                        target_price = entry * (1.0 + float(cfg.target_pct) / 100.0)
                    else:
                        target_price = entry * (1.0 - float(cfg.target_pct) / 100.0)
                if not custom_signal and entry > 0 and cfg.stop_loss_pct > 0:
                    if side == "BUY":
                        sl_price = entry * (1.0 - float(cfg.stop_loss_pct) / 100.0)
                    else:
                        sl_price = entry * (1.0 + float(cfg.stop_loss_pct) / 100.0)

                partial_enabled = bool(custom_signal and custom_settings.get("partial_profit_enabled", False))
                partial_qty = _partial_quantities(
                    qty,
                    float(custom_settings.get("partial_tp1_pct", 50.0)),
                    float(custom_settings.get("partial_tp2_pct", 25.0)),
                )

                pos = Position(
                    trade_id=uuid.uuid4().hex[:12],
                    user_id=self.user_id,
                    symbol=sym,
                    alert_name=alert_key,
                    side=side,
                    product=cfg.product,
                    qty=qty,
                    initial_qty=qty,
                    entry_price=entry,
                    entry_order_id=str(oid),
                    target_price=target_price,
                    sl_price=sl_price,
                    tsl_pct=0.0 if custom_signal else float(cfg.trailing_sl_pct),
                    tsl_stepwise=bool(cfg.tsl_stepwise),
                    highest=entry if side == "BUY" else 0.0,
                    lowest=entry if side == "SELL" else 0.0,
                    status="OPEN",
                    alert_time=str(ts or ""),
                    created_ts=time.time(),
                    updated_ts=time.time(),
                    cfg_target_pct=float(cfg.target_pct),
                    cfg_sl_pct=float(cfg.stop_loss_pct),
                    cfg_tsl_pct=0.0 if custom_signal else float(cfg.trailing_sl_pct),
                    cfg_tsl_stepwise=bool(cfg.tsl_stepwise),
                    ltp=entry,
                    pnl=0.0,
                    sector=self.sym_sector.get(sym, ""),
                    strategy_mode=cfg.strategy_mode,
                    signal_price=float(custom_signal.signal_price) if custom_signal else 0.0,
                    signal_score=float(custom_signal.score) if custom_signal else 0.0,
                    signal_max_score=float(custom_signal.max_score) if custom_signal else 0.0,
                    signal_grade=str(custom_signal.grade) if custom_signal else "",
                    signal_preset=str(custom_signal.preset) if custom_signal else "",
                    signal_volatility=str(custom_signal.volatility) if custom_signal else "",
                    signal_candle_time=str(custom_signal.candle_time) if custom_signal else "",
                    tp1_price=float(custom_signal.tp1) if custom_signal else 0.0,
                    tp2_price=float(custom_signal.tp2) if custom_signal else 0.0,
                    tp3_price=float(custom_signal.tp3) if custom_signal else 0.0,
                    trail_price=float(custom_signal.trail_price) if custom_signal else 0.0,
                    custom_use_trail=bool(custom_settings.get("use_trail", True)) if custom_signal else True,
                    custom_full_exit_tp3=bool(custom_settings.get("full_exit_tp3", True)) if custom_signal else True,
                    custom_partial_profit_enabled=partial_enabled,
                    partial_tp1_pct=float(custom_settings.get("partial_tp1_pct", 50.0)),
                    partial_tp2_pct=float(custom_settings.get("partial_tp2_pct", 25.0)),
                    partial_tp3_pct=float(custom_settings.get("partial_tp3_pct", 25.0)),
                    tp1_exit_qty=partial_qty[0] if partial_enabled else 0,
                    tp2_exit_qty=partial_qty[1] if partial_enabled else 0,
                    tp3_exit_qty=partial_qty[2] if partial_enabled else 0,
                )

                self.positions[sym] = pos
                if custom_signal:
                    self._custom_last_signal[(alert_key, sym)] = custom_signal.candle_time
                try:
                    await self.store.upsert_position(self.user_id, sym, pos.to_public())
                    await self.store.mark_open(self.user_id, sym, pos.trade_id)
                except Exception:
                    pass

                tick = self.ticks.get(sym) or {}
                close = float(tick.get("close") or 0.0)
                pct = ((entry - close) / close * 100.0) if close > 0 else 0.0
                tsl_line = 0.0
                if entry > 0 and cfg.trailing_sl_pct > 0:
                    if side == "BUY":
                        tsl_line = entry * (1.0 - float(cfg.trailing_sl_pct) / 100.0)
                    else:
                        tsl_line = entry * (1.0 + float(cfg.trailing_sl_pct) / 100.0)

                results.append(
                    {
                        "symbol": sym,
                        "status": "ENTERED",
                        "reason": "ORDER_OK",
                        "side": side,
                        "qty": qty,
                        "ltp": entry,
                        "pct": pct,
                        "entry": entry,
                        "target": target_price,
                        "stoploss": sl_price,
                        "tsl": tsl_line,
                        "strategy_mode": cfg.strategy_mode,
                        "custom": custom_signal.to_dict() if custom_signal else None,
                    }
                )
            finally:
                if lock_acquired:
                    try:
                        await self.store.release_lock(self.user_id, sym, "entry")
                    except Exception:
                        pass

        return results

    async def on_order_update(self, data: Dict[str, Any]) -> None:
        """
        Handle Kite order update callbacks.
        Keep it safe: update in-memory/redis positions if relevant.
        """
        try:
            order_id = str(data.get("order_id") or "")
            status = str(data.get("status") or "").upper()
            symbol = norm_symbol(str(data.get("tradingsymbol") or ""))
            if not symbol:
                return

            pos = self.positions.get(symbol)
            if not pos:
                return

            # Entry order updates
            if order_id and pos.entry_order_id and order_id == pos.entry_order_id:
                if status in ("COMPLETE", "FILLED"):
                    avg = float(data.get("average_price") or data.get("price") or 0.0)
                    if avg > 0:
                        pos.entry_price = avg
                    pos.updated_ts = time.time()
                    await self.store.upsert_position(self.user_id, symbol, pos.to_public())
                elif status in ("REJECTED", "CANCELLED"):
                    pos.status = "ERROR"
                    pos.exit_reason = f"ENTRY_{status}"
                    pos.updated_ts = time.time()
                    await self.store.upsert_position(self.user_id, symbol, pos.to_public())
                return

            # Exit order updates
            if order_id and pos.exit_order_id and order_id == pos.exit_order_id:
                if status in ("COMPLETE", "FILLED"):
                    pos.status = "CLOSED"
                    pos.updated_ts = time.time()
                    await self.store.upsert_position(self.user_id, symbol, pos.to_public())
                elif status in ("REJECTED", "CANCELLED"):
                    pos.status = "ERROR"
                    pos.exit_reason = f"EXIT_{status}"
                    pos.updated_ts = time.time()
                    await self.store.upsert_position(self.user_id, symbol, pos.to_public())
        except Exception as e:
            log.debug("ORDER_UPDATE_FAIL | user=%s err=%s data=%s", self.user_id, e, data)

    async def _book_partial_profit(self, pos: Position, target: str, requested_qty: int) -> bool:
        symbol = norm_symbol(pos.symbol)
        target = str(target or "").strip().upper()
        fields = {
            "TP1": ("tp1_booked", "tp1_exit_order_id"),
            "TP2": ("tp2_booked", "tp2_exit_order_id"),
            "TP3": ("tp3_booked", "tp3_exit_order_id"),
        }
        if target not in fields:
            return False

        booked_field, order_field = fields[target]
        if bool(getattr(pos, booked_field)):
            return True

        inflight_key = (symbol, target)
        if self._partial_inflight.get(inflight_key):
            return False
        self._partial_inflight[inflight_key] = True

        lock_acquired = False
        try:
            lk = await self.store.acquire_lock(self.user_id, symbol, "exit", ttl_ms=5000)
            if lk != 1:
                return False
            lock_acquired = True

            exit_qty = min(max(0, int(requested_qty)), max(0, int(pos.qty)))
            if target == "TP3":
                exit_qty = max(0, int(pos.qty))

            if exit_qty <= 0:
                setattr(pos, booked_field, True)
                pos.updated_ts = time.time()
                await self.store.upsert_position(self.user_id, symbol, pos.to_public())
                return True

            exit_side: Side = "SELL" if pos.side == "BUY" else "BUY"
            oid = await self._place_order(symbol, exit_side, exit_qty, pos.product)
            setattr(pos, booked_field, True)
            setattr(pos, order_field, str(oid))
            booked_pnl = (
                (float(pos.ltp) - float(pos.entry_price)) * exit_qty
                if pos.side == "BUY"
                else (float(pos.entry_price) - float(pos.ltp)) * exit_qty
            )
            pos.realized_pnl += booked_pnl
            pos.qty = max(0, int(pos.qty) - exit_qty)
            pos.updated_ts = time.time()
            pos.exit_reason = f"{target}_PARTIAL_BOOKED"

            log.info(
                "PARTIAL_PROFIT_OK | user=%s symbol=%s target=%s exit_qty=%s remaining_qty=%s order_id=%s",
                self.user_id,
                symbol,
                target,
                exit_qty,
                pos.qty,
                oid,
            )

            if pos.qty <= 0:
                pos.status = "CLOSED"
                pos.exit_reason = f"CUSTOM_{target}"
                pos.exit_order_id = str(oid)
                await self.store.delete_position(self.user_id, symbol)
                await self.store.clear_open(self.user_id, symbol)
                self.positions.pop(symbol, None)
                if pos.alert_time:
                    await self.store.update_alert_status(
                        self.user_id,
                        pos.alert_time,
                        symbol,
                        new_status=f"{target} CLOSED",
                        reason=pos.exit_reason,
                        alert_name=pos.alert_name,
                    )
                if self.broadcast_cb:
                    self.broadcast_cb(self.user_id, {"type": "pos_refresh"})
            else:
                pos.status = "OPEN"
                await self.store.upsert_position(self.user_id, symbol, pos.to_public())
                if self.broadcast_cb:
                    self.broadcast_cb(self.user_id, {"type": "pos_refresh"})
            return True
        except Exception as e:
            pos.status = "ERROR"
            pos.exit_reason = f"{target}_PARTIAL_ORDER_FAIL:{e}"
            pos.updated_ts = time.time()
            try:
                await self.store.upsert_position(self.user_id, symbol, pos.to_public())
            except Exception:
                pass
            try:
                await self._enable_kill_switch(reason=f"PARTIAL_ORDER_FAIL:{symbol}:{target}")
            except Exception:
                pass
            log.error(
                "PARTIAL_PROFIT_FAIL | user=%s symbol=%s target=%s qty=%s err=%s",
                self.user_id,
                symbol,
                target,
                requested_qty,
                e,
            )
            return False
        finally:
            if lock_acquired:
                try:
                    await self.store.release_lock(self.user_id, symbol, "exit")
                except Exception:
                    pass
            self._partial_inflight[inflight_key] = False

    # =========================
    # Tick ingestion + monitoring (HOT PATH)
    # =========================
    async def on_tick(
        self,
        symbol: str,
        ltp: float,
        close: float,
        high: float,
        low: float,
        tbq: float = 0.0,
        tsq: float = 0.0,
    ) -> Optional[Position]:
        try:
            return await self._on_tick_unsafe(symbol, ltp, close, high, low, tbq, tsq)
        except Exception as e:
            log.exception("🔥 CRITICAL_TICK_ERROR | user=%s symbol=%s err=%s", self.user_id, symbol, e)
            return None

    async def _on_tick_unsafe(
        self,
        symbol: str, 
        ltp: float,
        close: float,
        high: float,
        low: float,
        tbq: float,
        tsq: float
    ) -> Optional[Position]:
        symbol = norm_symbol(symbol)
        if not symbol or ltp <= 0:
            return None

        self.ticks[symbol] = {
            "ltp": float(ltp),
            "close": float(close),
            "high": float(high),
            "low": float(low),
            "tbq": float(tbq),
            "tsq": float(tsq),
        }

        if close and close > 0:
            pct = ((ltp - close) / close) * 100.0
            self._update_sector_perf(symbol, float(pct))
            
            # Periodic sector ranking summary
            now = time.time()
            if now - self._last_sector_rank_log >= self.sector_rank_log_interval_sec:
                self._last_sector_rank_log = now
                ranked = self.get_sector_rank()
                if ranked:
                    # Explicit Top 1 Gainer / Loser
                    top_gainer_name, top_gainer_pct = ranked[0]
                    top_loser_name, top_loser_pct = ranked[-1]

                    log.info("\n" + "="*80)
                    log.info("📊 SECTOR PERFORMANCE SUMMARY (Updated: %s)", 
                             datetime.now().strftime("%H:%M:%S"))
                    
                    # 1. Always show Top Gainer (or Best Performer)
                    if top_gainer_pct > 0:
                        log.info("👑 TOP GAINER: %s (+%.2f%%)", _green(_bold(top_gainer_name)), top_gainer_pct)
                    else:
                        # If best is negative, it's still the "Best" relative
                        log.info("👑 TOP GAINER: %s (%.2f%%)", top_gainer_name, top_gainer_pct)

                    # 2. Show Top Loser ONLY if it's different from Top Gainer
                    if top_gainer_name != top_loser_name:
                        if top_loser_pct < 0:
                             log.info("💀 TOP LOSER : %s (%.2f%%)", _red(_bold(top_loser_name)), top_loser_pct)
                        else:
                             log.info("💀 TOP LOSER : %s (+%.2f%%)", top_loser_name, top_loser_pct)

                    log.info("-" * 40)
                    log.info("All Sectors Ranked:")
                    
                    for i, (sec, avg_pct) in enumerate(ranked, 1):
                        cnt = self.sector_cnt.get(sec, 0)
                        emoji = "🟢" if avg_pct > 0 else "🔴" if avg_pct < 0 else "⚪"
                        
                        # Highlight top 2 boundaries if relevant
                        prefix = "   "
                        if i <= 2: prefix = "⚡ " # Top 2
                        
                        log.info("  %s%2d. %s %-25s %+7.2f%% (%d stocks)", 
                                prefix, i, emoji, sec, avg_pct, cnt)
                    log.info("="*80 + "\n")

        pos = self.positions.get(symbol)
        if not pos or pos.status != "OPEN":
            return None

        # update LTP and pnl safely (avoid entry=0 wrong pnl)
        pos.ltp = float(ltp)
        pos.updated_ts = time.time()

        if pos.entry_price > 0:
            unrealized = (ltp - pos.entry_price) * pos.qty if pos.side == "BUY" else (pos.entry_price - ltp) * pos.qty
            pos.pnl = float(pos.realized_pnl) + unrealized
        else:
            pos.pnl = 0.0

        # CNC: no auto exit monitoring (keep as per your design)
        if pos.product == "CNC":
            return pos

        # reconcile entry_price once if missing (REST)
        if pos.entry_price <= 0 and not self._recon_inflight.get(symbol):
            self._recon_inflight[symbol] = True

            async def _recon():
                try:
                    ok = await self._ensure_broker_ready()
                    if not ok:
                        return
                    avg = await self._fetch_positions_avg(symbol)
                    if avg > 0 and pos.entry_price <= 0:
                        pos.entry_price = float(avg)

                        # init monitoring from cfg pcts
                        if pos.cfg_target_pct > 0 and pos.target_price <= 0:
                            if pos.side == "BUY":
                                pos.target_price = pos.entry_price * (1.0 + pos.cfg_target_pct / 100.0)
                            else:
                                pos.target_price = pos.entry_price * (1.0 - pos.cfg_target_pct / 100.0)
                        if pos.cfg_sl_pct > 0 and pos.sl_price <= 0:
                            if pos.side == "BUY":
                                pos.sl_price = pos.entry_price * (1.0 - pos.cfg_sl_pct / 100.0)
                            else:
                                pos.sl_price = pos.entry_price * (1.0 + pos.cfg_sl_pct / 100.0)
                        if pos.cfg_tsl_pct > 0 and pos.tsl_pct <= 0:
                            pos.tsl_pct = float(pos.cfg_tsl_pct)

                        if pos.side == "BUY" and pos.highest <= 0:
                            pos.highest = pos.entry_price
                        if pos.side == "SELL" and pos.lowest <= 0:
                            pos.lowest = pos.entry_price

                        log.info(
                            "\n%s\n%s",
                            _bold(_yellow("♻️ ENTRY_RECONCILED")),
                            _dim(_j(user=self.user_id, symbol=symbol, avg=avg)),
                        )

                        try:
                            await self.store.upsert_position(self.user_id, symbol, pos.to_public())
                        except Exception:
                            pass
                finally:
                    self._recon_inflight[symbol] = False

            asyncio.create_task(_recon(), name=f"recon_{symbol}")

        if pos.strategy_mode == "PRECISION_SNIPER":
            reason: Optional[str] = None
            previous_trail = float(pos.trail_price or pos.sl_price or 0.0)

            # Check the pre-existing stop first. A trail advanced by a target
            # on this tick only becomes active for subsequent ticks.
            if pos.side == "BUY":
                if previous_trail > 0 and ltp <= previous_trail:
                    reason = "CUSTOM_STOP_LOSS" if not pos.tp1_hit else "CUSTOM_TRAILING_STOP"
                if not reason:
                    tp1_reached = pos.tp1_price > 0 and ltp >= pos.tp1_price
                    tp2_reached = pos.tp2_price > 0 and ltp >= pos.tp2_price
                    tp3_reached = pos.tp3_price > 0 and ltp >= pos.tp3_price
            else:
                if previous_trail > 0 and ltp >= previous_trail:
                    reason = "CUSTOM_STOP_LOSS" if not pos.tp1_hit else "CUSTOM_TRAILING_STOP"
                if not reason:
                    tp1_reached = pos.tp1_price > 0 and ltp <= pos.tp1_price
                    tp2_reached = pos.tp2_price > 0 and ltp <= pos.tp2_price
                    tp3_reached = pos.tp3_price > 0 and ltp <= pos.tp3_price

            if not reason:
                if pos.custom_partial_profit_enabled:
                    if tp1_reached and not pos.tp1_booked:
                        if await self._book_partial_profit(pos, "TP1", pos.tp1_exit_qty):
                            pos.tp1_hit = True
                            if pos.custom_use_trail and pos.qty > 0:
                                pos.trail_price = pos.signal_price or pos.entry_price
                        elif pos.status == "ERROR":
                            return pos
                    if pos.qty > 0 and tp2_reached and not pos.tp2_booked:
                        if await self._book_partial_profit(pos, "TP2", pos.tp2_exit_qty):
                            pos.tp2_hit = True
                            if pos.custom_use_trail and pos.qty > 0:
                                pos.trail_price = pos.tp1_price
                        elif pos.status == "ERROR":
                            return pos
                    if pos.qty > 0 and tp3_reached and not pos.tp3_booked:
                        if await self._book_partial_profit(pos, "TP3", pos.qty):
                            pos.tp3_hit = True
                            if pos.qty <= 0:
                                return pos
                        elif pos.status == "ERROR":
                            return pos
                else:
                    if tp1_reached and not pos.tp1_hit:
                        pos.tp1_hit = True
                        if pos.custom_use_trail:
                            pos.trail_price = pos.signal_price or pos.entry_price
                    if tp2_reached and not pos.tp2_hit:
                        pos.tp2_hit = True
                        if pos.custom_use_trail:
                            pos.trail_price = pos.tp1_price
                    if tp3_reached and not pos.tp3_hit:
                        pos.tp3_hit = True
                        if pos.custom_use_trail:
                            pos.trail_price = pos.tp2_price
                        if pos.custom_full_exit_tp3:
                            reason = "CUSTOM_TP3"

            if reason:
                if not self._exit_signal_sent.get(symbol):
                    self._exit_signal_sent[symbol] = True
                    pos.status = "EXIT_CONDITIONS_MET"
                    pos.exit_reason = reason
                    pos.updated_ts = time.time()
                    try:
                        await self.store.upsert_position(self.user_id, symbol, pos.to_public())
                    except Exception:
                        pass
                if not self._exit_inflight.get(symbol):
                    self._exit_inflight[symbol] = True
                    pos.status = "EXITING"
                    try:
                        await self.store.upsert_position(self.user_id, symbol, pos.to_public())
                    except Exception:
                        pass
                    asyncio.create_task(self._exit_position(symbol, reason), name=f"exit_{symbol}")
            elif (
                pos.tp1_hit
                or pos.tp2_hit
                or pos.tp3_hit
                or pos.trail_price != previous_trail
            ):
                try:
                    await self.store.upsert_position(self.user_id, symbol, pos.to_public())
                except Exception:
                    pass
            return pos

        # extremes
        if pos.side == "BUY":
            pos.highest = max(pos.highest, ltp) if pos.highest else float(ltp)
        else:
            pos.lowest = min(pos.lowest, ltp) if pos.lowest else float(ltp)

        # tsl line for BUY/SELL
        tsl_line = 0.0
        if pos.tsl_pct > 0:
            if pos.side == "BUY" and pos.highest > 0:
                anchor = float(pos.highest)
                if pos.tsl_stepwise and float(pos.entry_price) > 0:
                    anchor = _stepwise_anchor_long(float(pos.entry_price), float(pos.highest), float(pos.tsl_pct))
                tsl_line = anchor * (1.0 - pos.tsl_pct / 100.0)
            elif pos.side == "SELL" and pos.lowest > 0:
                anchor = float(pos.lowest)
                if pos.tsl_stepwise and float(pos.entry_price) > 0:
                    anchor = _stepwise_anchor_short(float(pos.entry_price), float(pos.lowest), float(pos.tsl_pct))
                tsl_line = anchor * (1.0 + pos.tsl_pct / 100.0)

        # distances (signed)
        tgt_dist = 0.0
        sl_dist = 0.0
        tsl_dist = 0.0
        if pos.target_price > 0:
            tgt_dist = ((pos.ltp - pos.target_price) / pos.target_price) * 100.0
        if pos.sl_price > 0:
            sl_dist = ((pos.ltp - pos.sl_price) / pos.sl_price) * 100.0
        if tsl_line > 0:
            tsl_dist = ((pos.ltp - tsl_line) / tsl_line) * 100.0

        # exit reason
        reason: Optional[str] = None
        if pos.side == "BUY":
            if pos.target_price > 0 and ltp >= pos.target_price:
                reason = "TARGET"
            elif pos.sl_price > 0 and ltp <= pos.sl_price:
                reason = "STOP_LOSS"
            elif tsl_line > 0 and ltp <= tsl_line:
                reason = "TRAILING_SL"
        else:
            if pos.target_price > 0 and ltp <= pos.target_price:
                reason = "TARGET"
            elif pos.sl_price > 0 and ltp >= pos.sl_price:
                reason = "STOP_LOSS"
            elif tsl_line > 0 and ltp >= tsl_line:
                reason = "TRAILING_SL"

        # near tags (for monitor)
        near_tags: List[str] = []
        if pos.target_price > 0 and abs(tgt_dist) <= 0.15:
            near_tags.append("NEAR_TARGET")
        if pos.sl_price > 0 and abs(sl_dist) <= 0.15:
            near_tags.append("NEAR_SL")
        if tsl_line > 0 and abs(tsl_dist) <= 0.15:
            near_tags.append("NEAR_TSL")

        # -----------------------------
        # ✅ MONITOR LOG (throttled: 5 sec per symbol)
        # -----------------------------
        now = time.time()
        last = self._mon_last_log.get(symbol, 0.0)
        if now - last >= self.monitor_log_interval_sec:
            self._mon_last_log[symbol] = now

            if not reason:
                # Suppress continuous monitor logs; only log on exit triggers.
                return pos

            log.info(
                "EXIT_TRIGGER | %s | reason=%s | side=%s qty=%s product=%s",
                symbol,
                reason,
                pos.side,
                pos.qty,
                pos.product,
            )
            exit_at = float(pos.ltp)
            if reason == "TRAILING_SL" and tsl_line > 0:
                exit_at = float(tsl_line)
            elif reason == "STOP_LOSS" and float(pos.sl_price) > 0:
                exit_at = float(pos.sl_price)
            elif reason == "TARGET" and float(pos.target_price) > 0:
                exit_at = float(pos.target_price)

            log.info(
                "entry=%.2f ltp=%.2f exit_at=%.2f pnl=%.2f | tgt=%.2f sl=%.2f tsl=%.2f",
                float(pos.entry_price),
                float(pos.ltp),
                float(exit_at),
                float(pos.pnl),
                float(pos.target_price),
                float(pos.sl_price),
                float(tsl_line),
            )

        # -----------------------------
        # ✅ LOG when condition fulfilled (only once) + trigger exit
        # -----------------------------
        if reason:
            if not self._exit_signal_sent.get(symbol):
                self._exit_signal_sent[symbol] = True
                
                # ✅ UPDATE STATUS - Mark as EXIT_CONDITIONS_MET
                pos.status = "EXIT_CONDITIONS_MET"
                pos.exit_reason = reason
                pos.updated_ts = time.time()
                
                # Save to Redis so dashboard shows the status
                try:
                    await self.store.upsert_position(self.user_id, symbol, pos.to_public())
                except Exception as e:
                    log.debug("REDIS_UPDATE_FAIL | symbol=%s err=%s", symbol, e)
                
                log.info(
                    "✅ EXIT_CONDITION_MET | %s | reason=%s | side=%s qty=%s | entry=%.2f ltp=%.2f pnl=%.2f",
                    symbol,
                    reason,
                    pos.side,
                    pos.qty,
                    float(pos.entry_price),
                    float(pos.ltp),
                    float(pos.pnl),
                )

            if not self._exit_inflight.get(symbol):
                self._exit_inflight[symbol] = True
                # Set status to EXITING before placing exit order
                pos.status = "EXITING"
                try:
                    await self.store.upsert_position(self.user_id, symbol, pos.to_public())
                except Exception:
                    pass
                asyncio.create_task(self._exit_position(symbol, reason), name=f"exit_{symbol}")
            else:
                log.debug("⏳ EXIT_DEBOUNCE | user=%s symbol=%s reason=%s", self.user_id, symbol, reason)

        return pos

    def _update_sector_perf(self, symbol: str, pct: float) -> None:
        sector = self.sym_sector.get(symbol)
        if not sector:
            return
        prev = self.sym_pct.get(symbol)
        if prev is None:
            self.sym_pct[symbol] = pct
            self.sector_sum[sector] = self.sector_sum.get(sector, 0.0) + pct
            self.sector_cnt[sector] = self.sector_cnt.get(sector, 0) + 1
            return
        if prev == pct:
            return
        self.sym_pct[symbol] = pct
        self.sector_sum[sector] = self.sector_sum.get(sector, 0.0) + (pct - prev)

    def get_sector_rank(self) -> List[tuple]:
        ranked: List[tuple] = []
        for sec, total in self.sector_sum.items():
            cnt = self.sector_cnt.get(sec, 0)
            if cnt <= 0:
                continue
            ranked.append((sec, total / cnt))
        ranked.sort(key=lambda x: x[1], reverse=True)
        return ranked


    def _maybe_log_monitor(self, pos: Position) -> None:
        """
        Rich monitoring log: shows for each OPEN position:
        - ltp, pnl, entry
        - target/sl/tsl-line
        - distance (%) to each level
        - "NEAR" tags so you know which stocks are close to hit
        """
        now = time.time()
        last = self._mon_last_log.get(pos.symbol, 0.0)
        if self.monitor_log_interval_sec > 0 and (now - last) < self.monitor_log_interval_sec:
            return
        self._mon_last_log[pos.symbol] = now

        ltp = float(pos.ltp)
        entry = float(pos.entry_price)
        tgt = float(pos.target_price)
        sl = float(pos.sl_price)

        tsl_line = 0.0
        if pos.product == "MIS" and pos.tsl_pct > 0:
            if pos.side == "BUY" and pos.highest > 0:
                anchor = float(pos.highest)
                if pos.tsl_stepwise and entry > 0:
                    anchor = _stepwise_anchor_long(entry, float(pos.highest), float(pos.tsl_pct))
                tsl_line = anchor * (1.0 - float(pos.tsl_pct) / 100.0)
            elif pos.side == "SELL" and pos.lowest > 0:
                anchor = float(pos.lowest)
                if pos.tsl_stepwise and entry > 0:
                    anchor = _stepwise_anchor_short(entry, float(pos.lowest), float(pos.tsl_pct))
                tsl_line = anchor * (1.0 + float(pos.tsl_pct) / 100.0)

        # distance sign: + means ltp above level, - means below (generic)
        dt = _pct_dist(ltp, tgt) if tgt > 0 else 0.0
        ds = _pct_dist(ltp, sl) if sl > 0 else 0.0
        dtsl = _pct_dist(ltp, tsl_line) if tsl_line > 0 else 0.0

        # interpret "near" differently for BUY/SELL
        near_tags: List[str] = []
        hit_tags: List[str] = []

        if pos.product == "MIS":
            if pos.side == "BUY":
                if tgt > 0 and ltp >= tgt:
                    hit_tags.append("HIT_TARGET")
                elif tgt > 0 and abs(_pct_dist(ltp, tgt)) <= self.near_pct:
                    near_tags.append("NEAR_TARGET")

                if sl > 0 and ltp <= sl:
                    hit_tags.append("HIT_SL")
                elif sl > 0 and abs(_pct_dist(ltp, sl)) <= self.near_pct:
                    near_tags.append("NEAR_SL")

                if tsl_line > 0 and ltp <= tsl_line:
                    hit_tags.append("HIT_TSL")
                elif tsl_line > 0 and abs(_pct_dist(ltp, tsl_line)) <= self.near_pct:
                    near_tags.append("NEAR_TSL")
            else:
                if tgt > 0 and ltp <= tgt:
                    hit_tags.append("HIT_TARGET")
                elif tgt > 0 and abs(_pct_dist(ltp, tgt)) <= self.near_pct:
                    near_tags.append("NEAR_TARGET")

                if sl > 0 and ltp >= sl:
                    hit_tags.append("HIT_SL")
                elif sl > 0 and abs(_pct_dist(ltp, sl)) <= self.near_pct:
                    near_tags.append("NEAR_SL")

                if tsl_line > 0 and ltp >= tsl_line:
                    hit_tags.append("HIT_TSL")
                elif tsl_line > 0 and abs(_pct_dist(ltp, tsl_line)) <= self.near_pct:
                    near_tags.append("NEAR_TSL")

        tag_str = ""
        if hit_tags:
            tag_str += " ✅" + ",".join(hit_tags)
        else:
            # Suppress continuous monitor logs; only log on hits.
            return

        log.info(
            "📈 MONITOR | user=%s trade=%s alert=%s | %s | "
            "dist[tgt=%.2f%% sl=%.2f%% tsl=%.2f%%]%s",
            self.user_id,
            pos.trade_id,
            pos.alert_name,
            _fmt_pos(pos),
            float(dt),
            float(ds),
            float(dtsl),
            tag_str,
        )

    # =========================
    # Manual squareoff (FIXED)
    # =========================
    async def manual_squareoff_zerodha(self, symbol: str, reason: str = "MANUAL_RESTART") -> Dict[str, Any]:
        """
        Manual squareoff that works even after restart (without Redis).
        Strategy:
          1) Try in-memory position -> normal exit path
          2) Else call kite.positions() and find open position for symbol
          3) Place opposite market order (MIS/CNC) for abs(quantity)
        """
        symbol = norm_symbol(symbol or "")
        if not symbol:
            return {"status": "ERROR", "reason": "BAD_SYMBOL"}

        # 1) Memory fast path
        pos = self.positions.get(symbol)
        if pos and pos.status == "OPEN":
            log.info("🖐️ MANUAL_EXIT_MEM | user=%s symbol=%s reason=%s", self.user_id, symbol, reason)
            await self._exit_position(symbol, reason)
            return {"status": "EXIT_TRIGGERED", "symbol": symbol, "reason": reason, "source": "MEMORY"}

        # Ensure selected broker is ready
        ok = await self._ensure_broker_ready()
        if not ok:
            return {"status": "ERROR", "reason": f"{self.broker}_NOT_CONNECTED"}

        # 2) Zerodha REST fallback
        log.info("🔎 MANUAL_EXIT_RESTART_LOOKUP | user=%s symbol=%s reason=%s", self.user_id, symbol, reason)
        try:
            data = await self._broker_positions()
        except Exception as e:
            log.error("❌ POSITIONS_FETCH_FAIL | user=%s symbol=%s err=%s", self.user_id, symbol, e)
            return {"status": "ERROR", "reason": f"POSITIONS_FETCH_FAIL:{e}"}

        rows = []
        try:
            rows = list(data.get("net") or []) + list(data.get("day") or [])
        except Exception:
            rows = []

        # Find position for this symbol with non-zero qty
        found = None
        for r in rows:
            tsym = norm_symbol(str(r.get("tradingsymbol") or ""))
            if tsym != symbol:
                continue
            qty = int(r.get("quantity") or 0)  # net quantity (+ long, - short)
            if qty == 0:
                continue
            found = r
            break

        if not found:
            log.warning("⚠️ MANUAL_EXIT_NO_ZERODHA_POS | user=%s symbol=%s", self.user_id, symbol)
            return {"status": "NOT_FOUND", "symbol": symbol, "reason": "NO_OPEN_POSITION_ON_ZERODHA"}

        qty = abs(int(found.get("quantity") or 0))
        if qty <= 0:
            return {"status": "NOT_FOUND", "symbol": symbol, "reason": "ZERO_QTY"}

        # Product: MIS/CNC from Zerodha position (usually 'product' key)
        prod_raw = str(found.get("product") or "MIS").strip().upper()
        product: Product = "CNC" if prod_raw == "CNC" else "MIS"

        # If net qty positive => long => exit by SELL, else short => exit by BUY
        net_qty = int(found.get("quantity") or 0)
        exit_side: Side = "SELL" if net_qty > 0 else "BUY"

        log.info(
            "🧯 MANUAL_EXIT_ZERODHA_POS_FOUND | user=%s symbol=%s net_qty=%s exit_side=%s qty=%s product=%s reason=%s",
            self.user_id, symbol, net_qty, exit_side, qty, product, reason
        )

        # Place exit order on Zerodha
        try:
            oid = await self._place_order(symbol, exit_side, qty, product)
            log.info(
                "✅ MANUAL_EXIT_ZERODHA_OK | user=%s symbol=%s exit_oid=%s side=%s qty=%s product=%s",
                self.user_id, symbol, str(oid), exit_side, qty, product
            )
            return {
                "status": "EXIT_OK",
                "symbol": symbol,
                "exit_order_id": str(oid),
                "exit_side": exit_side,
                "qty": qty,
                "product": product,
                "reason": reason,
                "source": "ZERODHA_POSITIONS",
            }
        except Exception as e:
            log.error("❌ MANUAL_EXIT_ZERODHA_FAIL | user=%s symbol=%s err=%s", self.user_id, symbol, e)
            return {"status": "ERROR", "reason": f"EXIT_ORDER_FAIL:{e}", "symbol": symbol}


    # =========================
    # Exit path
    # =========================
    async def _exit_position(self, symbol: str, reason: str) -> None:
        symbol = _safe_symbol(symbol)
        exit_side: Side = "SELL"

        try:
            pos = self.positions.get(symbol)
            if not pos or pos.status not in ("OPEN", "EXITING", "EXIT_CONDITIONS_MET"):
                log.debug("↩️ EXIT_SKIP | user=%s symbol=%s reason=%s (not OPEN/EXITING)", self.user_id, symbol, reason)
                return

            exit_side = "SELL" if pos.side == "BUY" else "BUY"

            log.info(
                "🚪 EXIT_START | %s | reason=%s | exit_side=%s | %s",
                symbol,
                reason,
                exit_side,
                _fmt_pos(pos),
            )

            lk = await self.store.acquire_lock(self.user_id, symbol, "exit", ttl_ms=2500)
            if lk != 1:
                log.warning(
                    "🔒 EXIT_LOCK_FAIL | user=%s trade=%s symbol=%s reason=%s lock=%s",
                    self.user_id,
                    pos.trade_id,
                    symbol,
                    reason,
                    lk,
                )
                return

            try:
                pos.status = "EXITING"
                pos.exit_reason = str(reason)
                pos.updated_ts = time.time()
                try:
                    await self.store.upsert_position(self.user_id, symbol, pos.to_public())
                except Exception as e:
                    log.debug("📝 EXIT_UPSERT_FAIL | user=%s symbol=%s err=%s", self.user_id, symbol, e)

                log.info(
                    "📤 EXIT_ORDER_SEND | %s | %s qty=%s product=%s",
                    symbol,
                    exit_side,
                    pos.qty,
                    pos.product,
                )

                try:
                    oid = await self._place_order(symbol, exit_side, int(pos.qty), pos.product)
                    pos.exit_order_id = str(oid)
                    pos.status = "CLOSED"
                    pos.updated_ts = time.time()

                    log.info(
                        "✅ EXIT_ORDER_OK | %s | reason=%s | pnl=%.2f",
                        symbol,
                        reason,
                        float(pos.pnl),
                    )

                    # Delete from Redis and memory instead of keeping CLOSED positions
                    try:
                        await self.store.delete_position(self.user_id, symbol)
                        # Remove from memory
                        if symbol in self.positions:
                            del self.positions[symbol]
                        log.info("🗑️ POSITION_DELETED | %s (CLOSED)", symbol)
                        
                        # ✅ Update alert status in history
                        if pos.alert_time:
                            await self.store.update_alert_status(
                                self.user_id, 
                                pos.alert_time, 
                                symbol, 
                                new_status=reason.replace("_", " "), # e.g. "TARGET_HIT" -> "TARGET HIT"
                                reason=reason,
                                alert_name=pos.alert_name
                            )

                        # ✅ Trigger UI refresh
                        if self.broadcast_cb:
                            self.broadcast_cb(self.user_id, {"type": "pos_refresh"})
                            
                    except Exception as e:
                        log.debug("📝 DELETE_POS_FAIL | user=%s symbol=%s err=%s", self.user_id, symbol, e)

                except Exception as e:
                    pos.status = "ERROR"
                    pos.exit_reason = f"EXIT_ORDER_FAIL:{e}"
                    pos.updated_ts = time.time()

                    log.error(
                        "❌ EXIT_ORDER_FAIL | user=%s trade=%s symbol=%s reason=%s err=%s | %s",
                        self.user_id,
                        pos.trade_id,
                        symbol,
                        reason,
                        e,
                        _fmt_pos(pos),
                    )

                    # On exit failure, enable kill switch (prevents new entries). Avoid square-off recursion here.
                    try:
                        await self._enable_kill_switch(reason=f"EXIT_ORDER_FAIL:{symbol}")
                    except Exception as e3:
                        log.error("KILL_SWITCH_ENABLE_FAIL | user=%s err=%s", self.user_id, e3)

                    try:
                        await self.store.upsert_position(self.user_id, symbol, pos.to_public())
                    except Exception as e2:
                        log.debug("📝 EXIT_UPSERT_FAIL3 | user=%s symbol=%s err=%s", self.user_id, symbol, e2)

                finally:
                    try:
                        await self.store.clear_open(self.user_id, symbol)
                        log.info("🧹 CLEAR_OPEN_OK | user=%s trade=%s symbol=%s", self.user_id, pos.trade_id, symbol)
                    except Exception as e:
                        log.warning("🧹 CLEAR_OPEN_FAIL | user=%s trade=%s symbol=%s err=%s", self.user_id, pos.trade_id, symbol, e)

            finally:
                try:
                    await self.store.release_lock(self.user_id, symbol, "exit")
                    log.debug("🔓 EXIT_LOCK_RELEASED | user=%s symbol=%s", self.user_id, symbol)
                except Exception as e:
                    log.debug("🔓 EXIT_LOCK_RELEASE_FAIL | user=%s symbol=%s err=%s", self.user_id, symbol, e)

        finally:
            self._exit_inflight[symbol] = False
            self._exit_signal_sent[symbol] = False
            log.debug("🏁 EXIT_DONE | user=%s symbol=%s", self.user_id, symbol)

    async def exit_all_open_positions(self, reason: str = "AUTO_SQ_OFF") -> int:
        """
        Trigger exit for ALL open positions (e.g. at 3:15 PM).
        Returns number of positions triggered.
        """
        count = 0
        # Snapshot keys to avoid runtime dict change errors if async
        symbols = [s for s, p in self.positions.items() if p.status == "OPEN"]
        
        if not symbols:
            log.warning("⏰ EXIT_ALL_SKIP | user=%s reason=%s | No OPEN positions found in memory. Total tracked=%s", 
                        self.user_id, reason, len(self.positions))
            return 0

        log.info("⏰ EXIT_ALL_TRIGGER | user=%s reason=%s count=%s symbols=%s", self.user_id, reason, len(symbols), symbols)

        for sym in symbols:
            # fire and forget exits (they have their own locks/logging)
            asyncio.create_task(self._exit_position(sym, reason))
            count += 1
        
        return count

    async def squareoff_all_positions(self, reason: str = "MANUAL_EXIT_ALL") -> Dict[str, Any]:
        """
        Best-effort square-off of *all* known open positions.

        Sources:
          1) In-memory OPEN positions
          2) Redis snapshot (if available)
          3) Zerodha positions() REST fallback (if connected)
        """
        symbols: Set[str] = set()

        # 1) Memory
        try:
            for s, p in (self.positions or {}).items():
                if getattr(p, "status", "") == "OPEN":
                    symbols.add(norm_symbol(s))
        except Exception:
            pass

        # 2) Redis snapshot
        try:
            rows = await self.store.list_positions(self.user_id)
            for r in rows or []:
                sym = norm_symbol(str(r.get("symbol") or ""))
                qty = int(r.get("qty") or 0)
                status = str(r.get("status") or "").upper()
                if sym and qty != 0 and status in {"OPEN", "EXITING", "EXIT_CONDITIONS_MET"}:
                    symbols.add(sym)
        except Exception:
            pass

        # 3) Zerodha REST fallback
        try:
            ok = await self._ensure_broker_ready()
            if ok:
                data = await self._broker_positions()
                rows = []
                try:
                    rows = list(data.get("net") or []) + list(data.get("day") or [])
                except Exception:
                    rows = []
                for r in rows:
                    sym = norm_symbol(str(r.get("tradingsymbol") or ""))
                    qty = int(r.get("quantity") or 0)
                    if sym and qty != 0:
                        symbols.add(sym)
        except Exception:
            pass

        results: List[Dict[str, Any]] = []
        for sym in sorted(symbols):
            try:
                r = await self.manual_squareoff_zerodha(sym, reason=reason)
            except Exception as e:
                r = {"status": "ERROR", "reason": str(e), "symbol": sym}
            results.append(dict(r))

        return {"ok": True, "count": len(symbols), "results": results}

    async def _enable_kill_switch(self, reason: str) -> None:
        await self.store.set_kill(self.user_id, True)
        try:
            if self.broadcast_cb:
                self.broadcast_cb(self.user_id, {"type": "kill_switch", "enabled": True, "reason": reason})
        except Exception:
            pass

    async def trigger_kill_switch(self, reason: str, squareoff_first: bool = True) -> Dict[str, Any]:
        """
        Panic action: square-off exposure (best-effort), then enable kill switch.
        """
        async with self._kill_trigger_lock:
            if await self.store.is_kill(self.user_id):
                return {"ok": True, "enabled": True, "already": True}

            sq: Optional[Dict[str, Any]] = None
            if squareoff_first:
                try:
                    sq = await self.squareoff_all_positions(reason=f"KILL_SWITCH:{reason}")
                except Exception as e:
                    sq = {"ok": False, "error": str(e), "count": 0, "results": []}

            await self._enable_kill_switch(reason=reason)
            return {"ok": True, "enabled": True, "squareoff": sq}
