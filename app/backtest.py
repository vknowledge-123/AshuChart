from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

import pytz

from .custom_strategy import (
    StrategySignal,
    evaluate_gmma_gold_cross_strategy,
    evaluate_gmma_obv_strategy,
    evaluate_gvk_trend_strategy,
    evaluate_liquidity_sweep_strategy,
    evaluate_pure_liquidity_sweep_strategy,
    evaluate_precision_sniper,
    resolve_gmma_gold_cross_settings,
    resolve_gmma_obv_settings,
    resolve_gvk_trend_settings,
    resolve_liquidity_sweep_settings,
    resolve_pure_liquidity_sweep_settings,
    resolve_settings,
)


IST = pytz.timezone("Asia/Kolkata")


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _as_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        dt = value
    elif value:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        return IST.localize(dt)
    return dt.astimezone(IST)


def _candle_time(candle: Dict[str, Any]) -> datetime:
    dt = _as_dt(candle.get("date"))
    if dt is None:
        return IST.localize(datetime.min.replace(year=1970))
    return dt


def _round_price(value: float) -> float:
    return round(float(value), 2)


def _round_any(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, dict):
        return {str(k): _round_any(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_round_any(v) for v in value]
    return value


def _diagnostic_row(
    candle: Dict[str, Any],
    *,
    status: str,
    reason: str = "",
    meta: Optional[Dict[str, Any]] = None,
    action: str = "",
) -> Dict[str, Any]:
    meta = meta or {}
    checks: Dict[str, Any] = {}
    if isinstance(meta.get("bull_checks"), dict):
        checks["bull"] = meta.get("bull_checks")
    if isinstance(meta.get("bear_checks"), dict):
        checks["bear"] = meta.get("bear_checks")
    return {
        "time": _candle_time(candle).isoformat(),
        "open": _round_price(_num(candle.get("open"))),
        "high": _round_price(_num(candle.get("high"))),
        "low": _round_price(_num(candle.get("low"))),
        "close": _round_price(_num(candle.get("close"))),
        "volume": _round_price(_num(candle.get("volume"))),
        "status": status,
        "reason": reason or str(meta.get("reason") or ""),
        "action": action,
        "side": str(meta.get("side") or ""),
        "score": _round_any(meta.get("score")),
        "max_score": _round_any(meta.get("max_score")),
        "bull_score": _round_any(meta.get("bull_score")),
        "bear_score": _round_any(meta.get("bear_score")),
        "grade": str(meta.get("grade") or ""),
        "adx": _round_any(meta.get("adx")),
        "vwap": _round_any(meta.get("vwap")),
        "obv": _round_any(meta.get("obv")),
        "obv_slow": _round_any(meta.get("obv_slow")),
        "required": _round_any(meta.get("required")),
        "received": _round_any(meta.get("received")),
        "indicators": _round_any(meta.get("indicators") or {}),
        "checks": _round_any(checks),
    }


def _settings_public(settings: Dict[str, Any]) -> Dict[str, Any]:
    return {str(k): _round_any(v) for k, v in sorted(settings.items(), key=lambda item: str(item[0]))}


def _settings_for_mode(strategy_mode: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    if strategy_mode == "GMMA_GOLD_CROSS":
        return resolve_gmma_gold_cross_settings(cfg)
    if strategy_mode == "GVK_TREND":
        return resolve_gvk_trend_settings(cfg)
    if strategy_mode == "PURE_LIQUIDITY_SWEEP":
        return resolve_pure_liquidity_sweep_settings(cfg)
    if strategy_mode == "LIQUIDITY_SWEEP":
        return resolve_liquidity_sweep_settings(cfg)
    if strategy_mode == "GMMA_OBV":
        return resolve_gmma_obv_settings(cfg)
    return resolve_settings(cfg)


def _evaluate(strategy_mode: str, history: Sequence[Dict[str, Any]], cfg: Dict[str, Any]) -> tuple[Optional[StrategySignal], Dict[str, Any]]:
    if strategy_mode == "GMMA_GOLD_CROSS":
        return evaluate_gmma_gold_cross_strategy(history, cfg)
    if strategy_mode == "GVK_TREND":
        return evaluate_gvk_trend_strategy(history, cfg)
    if strategy_mode == "PURE_LIQUIDITY_SWEEP":
        return evaluate_pure_liquidity_sweep_strategy(history, cfg)
    if strategy_mode == "LIQUIDITY_SWEEP":
        return evaluate_liquidity_sweep_strategy(history, cfg)
    if strategy_mode == "GMMA_OBV":
        return evaluate_gmma_obv_strategy(history, cfg)
    return evaluate_precision_sniper(history, cfg)


def _pnl(side: str, entry: float, exit_price: float, qty: int) -> float:
    if side == "SELL":
        return (entry - exit_price) * qty
    return (exit_price - entry) * qty


def run_custom_strategy_backtest(
    candles: Sequence[Dict[str, Any]],
    strategy_mode: str,
    cfg: Dict[str, Any],
    *,
    symbol: str,
    from_dt: datetime,
    to_dt: datetime,
    qty: int = 1,
    capital: float = 0.0,
) -> Dict[str, Any]:
    mode = str(strategy_mode or "").strip().upper()
    if mode not in {"PRECISION_SNIPER", "GMMA_OBV", "GMMA_GOLD_CROSS", "LIQUIDITY_SWEEP", "PURE_LIQUIDITY_SWEEP", "GVK_TREND"}:
        return {"error": "BACKTEST_UNSUPPORTED_STRATEGY"}

    ordered = sorted([dict(c) for c in candles], key=_candle_time)
    if from_dt.tzinfo is None:
        from_dt = IST.localize(from_dt)
    else:
        from_dt = from_dt.astimezone(IST)
    if to_dt.tzinfo is None:
        to_dt = IST.localize(to_dt)
    else:
        to_dt = to_dt.astimezone(IST)

    settings = _settings_for_mode(mode, cfg)
    direction_filter = str(cfg.get("direction") or "BOTH").strip().upper()
    fixed_qty = max(0, int(qty or 0))
    capital_value = float(capital or 0.0)

    trades: List[Dict[str, Any]] = []
    diagnostics: List[Dict[str, Any]] = []
    open_trade: Optional[Dict[str, Any]] = None
    last_signal_time = ""
    skipped_signals = 0
    evaluated_signals = 0
    warmup_candles = len([c for c in ordered if _candle_time(c) < from_dt])
    in_range_candles = len([c for c in ordered if from_dt <= _candle_time(c) <= to_dt])
    first_candle_time = _candle_time(ordered[0]).isoformat() if ordered else ""
    last_candle_time = _candle_time(ordered[-1]).isoformat() if ordered else ""

    if ordered and in_range_candles == 0:
        for candle in ordered:
            candle_dt = _candle_time(candle)
            diagnostics.append(
                _diagnostic_row(
                    candle,
                    status="WARMUP" if candle_dt < from_dt else "OUT_OF_RANGE",
                    reason="CANDLE_BEFORE_BACKTEST_START" if candle_dt < from_dt else "CANDLE_AFTER_BACKTEST_END",
                    action="NOT_EVALUATED",
                )
            )

    for i in range(len(ordered)):
        candle = ordered[i]
        candle_dt = _candle_time(candle)
        if candle_dt < from_dt:
            continue
        if candle_dt > to_dt:
            break

        if open_trade:
            side = str(open_trade["side"])
            high = _num(candle.get("high"))
            low = _num(candle.get("low"))
            close = _num(candle.get("close"))
            exit_price = 0.0
            exit_reason = ""
            previous_trail = _num(open_trade.get("trail_price") or open_trade.get("stop_loss"))

            if side == "BUY":
                if previous_trail > 0 and low <= previous_trail:
                    exit_price = previous_trail
                    exit_reason = "STOP_LOSS" if not open_trade.get("tp1_hit") else "TRAILING_STOP"
                if not exit_reason:
                    tp1_reached = high >= _num(open_trade.get("tp1"))
                    tp2_reached = high >= _num(open_trade.get("tp2"))
                    tp3_reached = high >= _num(open_trade.get("tp3"))
            else:
                if previous_trail > 0 and high >= previous_trail:
                    exit_price = previous_trail
                    exit_reason = "STOP_LOSS" if not open_trade.get("tp1_hit") else "TRAILING_STOP"
                if not exit_reason:
                    tp1_reached = low <= _num(open_trade.get("tp1"))
                    tp2_reached = low <= _num(open_trade.get("tp2"))
                    tp3_reached = low <= _num(open_trade.get("tp3"))

            if not exit_reason:
                if tp1_reached and not open_trade.get("tp1_hit"):
                    open_trade["tp1_hit"] = True
                    if settings.get("use_trail", True):
                        open_trade["trail_price"] = open_trade["signal_price"]
                if tp2_reached and not open_trade.get("tp2_hit"):
                    open_trade["tp2_hit"] = True
                    if settings.get("use_trail", True):
                        open_trade["trail_price"] = open_trade["tp1"]
                if tp3_reached and not open_trade.get("tp3_hit"):
                    open_trade["tp3_hit"] = True
                    if settings.get("use_trail", True):
                        open_trade["trail_price"] = open_trade["tp2"]
                    if settings.get("full_exit_tp3", True):
                        exit_price = _num(open_trade.get("tp3"))
                        exit_reason = "TP3"

            if not exit_reason and candle_dt >= to_dt:
                exit_price = close
                exit_reason = "EOD"

            if not exit_reason and mode == "GVK_TREND" and bool(settings.get("exit_on_reversal", True)):
                reversal_signal, reversal_meta = _evaluate(mode, ordered[: i + 1], cfg)
                if (
                    reversal_signal
                    and reversal_signal.side
                    and reversal_signal.side != side
                    and str(reversal_signal.candle_time or "") != str(open_trade.get("signal_time") or "")
                ):
                    exit_price = close
                    exit_reason = "STRATEGY_REVERSAL"
                    open_trade["exit_meta"] = reversal_meta

            if exit_reason:
                trade_qty = int(open_trade["qty"])
                pnl = _pnl(side, _num(open_trade["entry_price"]), exit_price, trade_qty)
                open_trade.update(
                    {
                        "exit_time": candle_dt.isoformat(),
                        "exit_price": _round_price(exit_price),
                        "exit_reason": exit_reason,
                        "pnl": _round_price(pnl),
                        "pnl_pct": round((pnl / (_num(open_trade["entry_price"]) * trade_qty)) * 100.0, 2)
                        if trade_qty > 0 and _num(open_trade["entry_price"]) > 0
                        else 0.0,
                    }
                )
                trades.append(open_trade)
                open_trade = None
                diagnostics.append(
                    _diagnostic_row(
                        candle,
                        status="EXIT",
                        reason=exit_reason,
                        action=f"EXIT {exit_reason} @ {_round_price(exit_price)}",
                    )
                )
                continue

        if open_trade or i + 1 >= len(ordered):
            if open_trade:
                diagnostics.append(
                    _diagnostic_row(
                        candle,
                        status="IN_POSITION",
                        reason="POSITION_OPEN",
                        action=f"TRAIL {_round_price(_num(open_trade.get('trail_price')))}",
                    )
                )
            continue

        history = ordered[: i + 1]
        signal, meta = _evaluate(mode, history, cfg)
        if not signal:
            diagnostics.append(
                _diagnostic_row(
                    candle,
                    status="FAILED",
                    reason=str(meta.get("reason") or "ENTRY_CHECK_FAILED"),
                    meta=meta,
                )
            )
            continue
        evaluated_signals += 1
        row = _diagnostic_row(candle, status="QUALIFIED", reason="CUSTOM_SIGNAL_OK", meta=meta)
        if signal.candle_time == last_signal_time:
            skipped_signals += 1
            row["status"] = "SKIPPED"
            row["reason"] = "DUPLICATE_SIGNAL_CANDLE"
            diagnostics.append(row)
            continue
        last_signal_time = signal.candle_time
        if direction_filter == "LONG" and signal.side != "BUY":
            skipped_signals += 1
            row["status"] = "SKIPPED"
            row["reason"] = "DIRECTION_FILTER_LONG_ONLY"
            diagnostics.append(row)
            continue
        if direction_filter == "SHORT" and signal.side != "SELL":
            skipped_signals += 1
            row["status"] = "SKIPPED"
            row["reason"] = "DIRECTION_FILTER_SHORT_ONLY"
            diagnostics.append(row)
            continue

        next_candle = ordered[i + 1]
        entry_dt = _candle_time(next_candle)
        if entry_dt > to_dt:
            row["status"] = "SKIPPED"
            row["reason"] = "NEXT_ENTRY_AFTER_TO_DATE"
            diagnostics.append(row)
            continue
        entry = _num(next_candle.get("open") or next_candle.get("close"))
        trade_qty = fixed_qty if fixed_qty > 0 else int(capital_value / entry) if entry > 0 and capital_value > 0 else 1
        if trade_qty <= 0:
            skipped_signals += 1
            row["status"] = "SKIPPED"
            row["reason"] = "ZERO_QTY"
            diagnostics.append(row)
            continue

        open_trade = {
            "symbol": symbol,
            "strategy_mode": mode,
            "side": signal.side,
            "qty": trade_qty,
            "signal_time": signal.candle_time,
            "entry_time": entry_dt.isoformat(),
            "entry_price": _round_price(entry),
            "signal_price": _round_price(signal.signal_price),
            "stop_loss": _round_price(signal.stop_loss),
            "trail_price": _round_price(signal.trail_price),
            "tp1": _round_price(signal.tp1),
            "tp2": _round_price(signal.tp2),
            "tp3": _round_price(signal.tp3),
            "score": round(float(signal.score), 2),
            "grade": signal.grade,
            "preset": signal.preset,
            "volatility": signal.volatility,
            "tp1_hit": False,
            "tp2_hit": False,
            "tp3_hit": False,
            "entry_meta": meta,
        }
        row["action"] = f"ENTRY {signal.side} next open {_round_price(entry)}"
        diagnostics.append(row)

    if open_trade:
        last = ordered[-1]
        last_dt = _candle_time(last)
        exit_price = _num(last.get("close"))
        trade_qty = int(open_trade["qty"])
        pnl = _pnl(str(open_trade["side"]), _num(open_trade["entry_price"]), exit_price, trade_qty)
        open_trade.update(
            {
                "exit_time": last_dt.isoformat(),
                "exit_price": _round_price(exit_price),
                "exit_reason": "OPEN_END",
                "pnl": _round_price(pnl),
                "pnl_pct": round((pnl / (_num(open_trade["entry_price"]) * trade_qty)) * 100.0, 2)
                if trade_qty > 0 and _num(open_trade["entry_price"]) > 0
                else 0.0,
            }
        )
        trades.append(open_trade)

    total_pnl = sum(_num(t.get("pnl")) for t in trades)
    wins = [t for t in trades if _num(t.get("pnl")) > 0]
    losses = [t for t in trades if _num(t.get("pnl")) < 0]
    gross_profit = sum(_num(t.get("pnl")) for t in wins)
    gross_loss = abs(sum(_num(t.get("pnl")) for t in losses))

    return {
        "symbol": symbol,
        "strategy_mode": mode,
        "parameters": _settings_public(settings),
        "required_candles": int(cfg.get("_required_candles") or 0),
        "warmup_days_requested": int(cfg.get("_warmup_days") or 0),
        "direction_filter": direction_filter,
        "qty": fixed_qty,
        "capital": _round_price(capital_value),
        "from": from_dt.isoformat(),
        "to": to_dt.isoformat(),
        "candles": len(ordered),
        "warmup_candles": warmup_candles,
        "in_range_candles": in_range_candles,
        "first_candle_time": first_candle_time,
        "last_candle_time": last_candle_time,
        "evaluated_signals": evaluated_signals,
        "skipped_signals": skipped_signals,
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round((len(wins) / len(trades)) * 100.0, 2) if trades else 0.0,
        "gross_profit": _round_price(gross_profit),
        "gross_loss": _round_price(gross_loss),
        "net_pnl": _round_price(total_pnl),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else None,
        "trades": trades,
        "detail_report": diagnostics,
    }
