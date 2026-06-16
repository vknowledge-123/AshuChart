from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence


@dataclass
class StrategySignal:
    side: str
    signal_price: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float
    trail_price: float
    score: float
    max_score: float
    grade: str
    preset: str
    volatility: str
    candle_time: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "side": self.side,
            "signal_price": self.signal_price,
            "stop_loss": self.stop_loss,
            "tp1": self.tp1,
            "tp2": self.tp2,
            "tp3": self.tp3,
            "trail_price": self.trail_price,
            "score": self.score,
            "max_score": self.max_score,
            "grade": self.grade,
            "preset": self.preset,
            "volatility": self.volatility,
            "candle_time": self.candle_time,
        }


PRESETS: Dict[str, Dict[str, float]] = {
    "Scalping": {"ema_fast": 5, "ema_slow": 13, "ema_trend": 34, "rsi_len": 8, "atr_len": 10, "min_score": 4, "sl_mult": 0.8},
    "Aggressive": {"ema_fast": 8, "ema_slow": 18, "ema_trend": 50, "rsi_len": 11, "atr_len": 12, "min_score": 3, "sl_mult": 1.2},
    "Default": {"ema_fast": 9, "ema_slow": 21, "ema_trend": 55, "rsi_len": 13, "atr_len": 14, "min_score": 5, "sl_mult": 1.5},
    "Conservative": {"ema_fast": 12, "ema_slow": 26, "ema_trend": 89, "rsi_len": 14, "atr_len": 14, "min_score": 7, "sl_mult": 2.0},
    "Swing": {"ema_fast": 13, "ema_slow": 34, "ema_trend": 89, "rsi_len": 21, "atr_len": 20, "min_score": 6, "sl_mult": 2.5},
    "Crypto 24/7": {"ema_fast": 9, "ema_slow": 21, "ema_trend": 55, "rsi_len": 14, "atr_len": 20, "min_score": 5, "sl_mult": 2.0},
}


def _num(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _bool(value: Any, default: bool) -> bool:
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


def resolve_settings(cfg: Dict[str, Any]) -> Dict[str, Any]:
    preset = str(cfg.get("custom_preset") or "Auto").strip()
    resolved = "Scalping" if preset == "Auto" else preset
    manual = {
        "ema_fast": _int(cfg.get("custom_ema_fast"), 9),
        "ema_slow": _int(cfg.get("custom_ema_slow"), 21),
        "ema_trend": _int(cfg.get("custom_ema_trend"), 55),
        "rsi_len": _int(cfg.get("custom_rsi_len"), 13),
        "atr_len": _int(cfg.get("custom_atr_len"), 14),
        "min_score": _num(cfg.get("custom_min_score"), 5),
        "sl_mult": _num(cfg.get("custom_sl_mult"), 1.5),
    }
    values = dict(PRESETS.get(resolved, manual))
    values.update(
        {
            "preset": resolved,
            "grade_filter": str(cfg.get("custom_grade_filter") or "All"),
            "hide_c_grade": _bool(cfg.get("custom_hide_c_grade"), True),
            "vol_filter_mode": str(cfg.get("custom_vol_filter_mode") or "Skip Signals"),
            "vol_widen_factor": _num(cfg.get("custom_vol_widen_factor"), 1.5),
            "high_vol_threshold": _num(cfg.get("custom_high_vol_threshold"), 1.3),
            "tp1_mult": _num(cfg.get("custom_tp1_mult"), 1.0),
            "tp2_mult": _num(cfg.get("custom_tp2_mult"), 2.0),
            "tp3_mult": _num(cfg.get("custom_tp3_mult"), 3.0),
            "use_trail": _bool(cfg.get("custom_use_trail"), True),
            "full_exit_tp3": _bool(cfg.get("custom_full_exit_tp3"), True),
            "partial_profit_enabled": _bool(cfg.get("custom_partial_profit_enabled"), False),
            "partial_tp1_pct": _num(cfg.get("custom_partial_tp1_pct"), 50.0),
            "partial_tp2_pct": _num(cfg.get("custom_partial_tp2_pct"), 25.0),
            "partial_tp3_pct": _num(cfg.get("custom_partial_tp3_pct"), 25.0),
            "use_structure_sl": _bool(cfg.get("custom_use_structure_sl"), True),
            "swing_lookback": _int(cfg.get("custom_swing_lookback"), 10),
            "htf_minutes": max(5, _int(cfg.get("custom_htf_minutes"), 5)),
        }
    )
    return values


def validate_custom_config(cfg: Dict[str, Any]) -> Optional[str]:
    s = resolve_settings(cfg)
    if not (3 <= s["ema_fast"] <= 50 and 10 <= s["ema_slow"] <= 100 and 20 <= s["ema_trend"] <= 200):
        return "CUSTOM_EMA_LENGTH_INVALID"
    if s["ema_fast"] >= s["ema_slow"]:
        return "CUSTOM_EMA_FAST_MUST_BE_BELOW_SLOW"
    if not (0.5 <= s["tp1_mult"] < s["tp2_mult"] < s["tp3_mult"] <= 12):
        return "CUSTOM_TP_MULTIPLIERS_INVALID"
    if s["htf_minutes"] < 5 or s["htf_minutes"] % 5 != 0:
        return "CUSTOM_HTF_MUST_BE_5_MINUTE_MULTIPLE"
    if s["partial_profit_enabled"]:
        percentages = [
            float(s["partial_tp1_pct"]),
            float(s["partial_tp2_pct"]),
            float(s["partial_tp3_pct"]),
        ]
        if any(pct < 0 or pct > 100 for pct in percentages):
            return "CUSTOM_PARTIAL_PERCENT_INVALID"
        if abs(sum(percentages) - 100.0) > 0.001:
            return "CUSTOM_PARTIAL_PERCENT_TOTAL_MUST_BE_100"
    return None


def _ema(values: Sequence[float], length: int) -> List[float]:
    if not values:
        return []
    alpha = 2.0 / (length + 1.0)
    out = [float(values[0])]
    for value in values[1:]:
        out.append(alpha * float(value) + (1.0 - alpha) * out[-1])
    return out


def _rma(values: Sequence[float], length: int) -> List[float]:
    if not values:
        return []
    out = [float(values[0])]
    alpha = 1.0 / max(1, int(length))
    for value in values[1:]:
        out.append(alpha * float(value) + (1.0 - alpha) * out[-1])
    return out


def _sma_last(values: Sequence[float], length: int) -> float:
    if not values:
        return 0.0
    window = values[-max(1, int(length)) :]
    return sum(float(x) for x in window) / len(window)


def _rsi(values: Sequence[float], length: int) -> List[float]:
    if not values:
        return []
    changes = [0.0]
    for i in range(1, len(values)):
        changes.append(float(values[i]) - float(values[i - 1]))
    gains = _rma([max(x, 0.0) for x in changes], length)
    losses = _rma([max(-x, 0.0) for x in changes], length)
    out: List[float] = []
    for gain, loss in zip(gains, losses):
        if loss == 0:
            out.append(100.0 if gain > 0 else 50.0)
        else:
            out.append(100.0 - (100.0 / (1.0 + gain / loss)))
    return out


def _true_ranges(candles: Sequence[Dict[str, Any]]) -> List[float]:
    out: List[float] = []
    for i, candle in enumerate(candles):
        high = _num(candle.get("high"), 0)
        low = _num(candle.get("low"), 0)
        prev_close = _num(candles[i - 1].get("close"), high) if i else high
        out.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return out


def _dmi(candles: Sequence[Dict[str, Any]], length: int = 14) -> tuple[List[float], List[float], List[float]]:
    plus_dm = [0.0]
    minus_dm = [0.0]
    for i in range(1, len(candles)):
        up = _num(candles[i].get("high"), 0) - _num(candles[i - 1].get("high"), 0)
        down = _num(candles[i - 1].get("low"), 0) - _num(candles[i].get("low"), 0)
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
    atr = _rma(_true_ranges(candles), length)
    plus_smoothed = _rma(plus_dm, length)
    minus_smoothed = _rma(minus_dm, length)
    plus_di = [(100.0 * p / a) if a > 0 else 0.0 for p, a in zip(plus_smoothed, atr)]
    minus_di = [(100.0 * m / a) if a > 0 else 0.0 for m, a in zip(minus_smoothed, atr)]
    dx = [
        (100.0 * abs(p - m) / (p + m)) if (p + m) > 0 else 0.0
        for p, m in zip(plus_di, minus_di)
    ]
    return plus_di, minus_di, _rma(dx, length)


def _session_vwap(candles: Sequence[Dict[str, Any]]) -> List[float]:
    out: List[float] = []
    day = None
    pv = 0.0
    volume_sum = 0.0
    for candle in candles:
        stamp = candle.get("date")
        candle_day = stamp.date() if isinstance(stamp, datetime) else str(stamp)[:10]
        if candle_day != day:
            day = candle_day
            pv = 0.0
            volume_sum = 0.0
        volume = _num(candle.get("volume"), 0)
        typical = (
            _num(candle.get("high"), 0)
            + _num(candle.get("low"), 0)
            + _num(candle.get("close"), 0)
        ) / 3.0
        pv += typical * volume
        volume_sum += volume
        out.append(pv / volume_sum if volume_sum > 0 else _num(candle.get("close"), 0))
    return out


def _grade(score: float, max_score: float) -> str:
    ratio = score / max_score if max_score > 0 else 0.0
    if ratio >= 0.80:
        return "A+"
    if ratio >= 0.65:
        return "A"
    if ratio >= 0.50:
        return "B"
    return "C"


def _passes_grade(score: float, max_score: float, grade_filter: str, hide_c: bool) -> bool:
    ratio = score / max_score if max_score > 0 else 0.0
    if grade_filter == "A+ Only" and ratio < 0.80:
        return False
    if grade_filter == "A+ and A" and ratio < 0.65:
        return False
    return not hide_c or ratio >= 0.50


def evaluate_precision_sniper(
    candles: Sequence[Dict[str, Any]],
    cfg: Dict[str, Any],
    htf_candles: Optional[Sequence[Dict[str, Any]]] = None,
) -> tuple[Optional[StrategySignal], Dict[str, Any]]:
    s = resolve_settings(cfg)
    minimum = max(int(s["ema_trend"]), 50) + 2
    if len(candles) < minimum:
        return None, {"reason": "CUSTOM_NOT_ENOUGH_CANDLES", "required": minimum, "received": len(candles)}

    closes = [_num(x.get("close"), 0) for x in candles]
    highs = [_num(x.get("high"), 0) for x in candles]
    lows = [_num(x.get("low"), 0) for x in candles]
    volumes = [_num(x.get("volume"), 0) for x in candles]
    if any(x <= 0 for x in closes[-2:]):
        return None, {"reason": "CUSTOM_BAD_CANDLE_DATA"}

    ema_fast = _ema(closes, int(s["ema_fast"]))
    ema_slow = _ema(closes, int(s["ema_slow"]))
    ema_trend = _ema(closes, int(s["ema_trend"]))
    atr_values = _rma(_true_ranges(candles), int(s["atr_len"]))
    rsi_values = _rsi(closes, int(s["rsi_len"]))
    macd = [a - b for a, b in zip(_ema(closes, 12), _ema(closes, 26))]
    macd_signal = _ema(macd, 9)
    macd_hist = [a - b for a, b in zip(macd, macd_signal)]
    plus_di, minus_di, adx = _dmi(candles)
    vwap = _session_vwap(candles)

    htf_source = list(htf_candles or candles)
    htf_closes = [_num(x.get("close"), 0) for x in htf_source]
    htf_fast = _ema(htf_closes, int(s["ema_fast"]))
    htf_slow = _ema(htf_closes, int(s["ema_slow"]))
    htf_index = -2 if len(htf_fast) >= 2 else -1
    htf_bias = 1 if htf_fast[htf_index] > htf_slow[htf_index] else -1 if htf_fast[htf_index] < htf_slow[htf_index] else 0

    sym_has_volume = any(x > 0 for x in volumes)
    vol_sma = _sma_last(volumes, 20)
    vol_above_avg = sym_has_volume and volumes[-1] > vol_sma * 1.2
    vwap_valid = sym_has_volume
    max_score = 8.0 + (1.0 if sym_has_volume else 0.0) + (1.0 if vwap_valid else 0.0)
    effective_min_score = float(s["min_score"]) * max_score / 10.0

    atr = atr_values[-1]
    atr_average = _sma_last(atr_values, 42) or atr
    vol_ratio = atr / atr_average if atr_average > 0 else 1.0
    volatility = "High" if vol_ratio > float(s["high_vol_threshold"]) else "Low" if vol_ratio < 0.7 else "Normal"
    high_vol = volatility == "High"
    if s["vol_filter_mode"] == "Skip Signals" and high_vol:
        return None, {"reason": "CUSTOM_HIGH_VOLATILITY", "volatility": volatility, "vol_ratio": vol_ratio}

    close = closes[-1]
    bull_score = 0.0
    bull_score += 1.0 if ema_fast[-1] > ema_slow[-1] else 0.0
    bull_score += 1.0 if close > ema_trend[-1] else 0.0
    bull_score += 1.0 if 50 < rsi_values[-1] < 75 else 0.0
    bull_score += 1.0 if macd_hist[-1] > 0 else 0.0
    bull_score += 1.0 if macd_hist[-1] > macd_hist[-2] else 0.0
    bull_score += 1.0 if vwap_valid and close > vwap[-1] else 0.0
    bull_score += 1.0 if vol_above_avg else 0.0
    bull_score += 1.0 if adx[-1] > 20 and plus_di[-1] > minus_di[-1] else 0.0
    bull_score += 1.5 if htf_bias == 1 else 0.0
    bull_score += 0.5 if close > ema_fast[-1] else 0.0

    bear_score = 0.0
    bear_score += 1.0 if ema_fast[-1] < ema_slow[-1] else 0.0
    bear_score += 1.0 if close < ema_trend[-1] else 0.0
    bear_score += 1.0 if 25 < rsi_values[-1] < 50 else 0.0
    bear_score += 1.0 if macd_hist[-1] < 0 else 0.0
    bear_score += 1.0 if macd_hist[-1] < macd_hist[-2] else 0.0
    bear_score += 1.0 if vwap_valid and close < vwap[-1] else 0.0
    bear_score += 1.0 if vol_above_avg else 0.0
    bear_score += 1.0 if adx[-1] > 20 and minus_di[-1] > plus_di[-1] else 0.0
    bear_score += 1.5 if htf_bias == -1 else 0.0
    bear_score += 0.5 if close < ema_fast[-1] else 0.0

    bull_cross = ema_fast[-1] > ema_slow[-1] and ema_fast[-2] <= ema_slow[-2]
    bear_cross = ema_fast[-1] < ema_slow[-1] and ema_fast[-2] >= ema_slow[-2]
    buy = (
        bull_cross
        and close > ema_fast[-1]
        and close > ema_slow[-1]
        and rsi_values[-1] < 75
        and bull_score >= effective_min_score
        and _passes_grade(bull_score, max_score, str(s["grade_filter"]), bool(s["hide_c_grade"]))
    )
    sell = (
        bear_cross
        and close < ema_fast[-1]
        and close < ema_slow[-1]
        and rsi_values[-1] > 25
        and bear_score >= effective_min_score
        and _passes_grade(bear_score, max_score, str(s["grade_filter"]), bool(s["hide_c_grade"]))
    )
    if not buy and not sell:
        return None, {
            "reason": "CUSTOM_ENTRY_CHECK_FAILED",
            "bull_score": bull_score,
            "bear_score": bear_score,
            "max_score": max_score,
            "volatility": volatility,
            "rsi": rsi_values[-1],
            "adx": adx[-1],
        }

    side = "BUY" if buy else "SELL"
    score = bull_score if buy else bear_score
    sl_vol_mult = float(s["vol_widen_factor"]) if s["vol_filter_mode"] == "Widen SL" and high_vol else 1.0
    atr_stop_distance = atr * float(s["sl_mult"]) * sl_vol_mult
    stop = close - atr_stop_distance if buy else close + atr_stop_distance
    if bool(s["use_structure_sl"]):
        lookback = max(1, int(s["swing_lookback"]) + 1)
        structure = min(lows[-lookback:]) - atr * 0.2 if buy else max(highs[-lookback:]) + atr * 0.2
        stop = min(stop, structure) if buy else max(stop, structure)
        max_distance = atr_stop_distance * 1.5
        if abs(close - stop) > max_distance:
            stop = close - max_distance if buy else close + max_distance
        min_distance = atr * 0.5
        if abs(close - stop) < min_distance:
            stop = close - min_distance if buy else close + min_distance

    risk = abs(close - stop)
    direction = 1.0 if buy else -1.0
    stamp = candles[-1].get("date")
    candle_time = stamp.isoformat() if isinstance(stamp, datetime) else str(stamp or "")
    signal = StrategySignal(
        side=side,
        signal_price=close,
        stop_loss=stop,
        tp1=close + direction * risk * float(s["tp1_mult"]),
        tp2=close + direction * risk * float(s["tp2_mult"]),
        tp3=close + direction * risk * float(s["tp3_mult"]),
        trail_price=stop,
        score=score,
        max_score=max_score,
        grade=_grade(score, max_score),
        preset=str(s["preset"]),
        volatility=volatility,
        candle_time=candle_time,
    )
    return signal, {"reason": "CUSTOM_SIGNAL_OK", **signal.to_dict()}
