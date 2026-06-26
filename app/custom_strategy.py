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


def timeframe_minutes(cfg: Dict[str, Any], default: int = 5) -> int:
    minutes = _int(
        cfg.get("strategy_timeframe_minutes")
        or cfg.get("timeframe_minutes")
        or cfg.get("custom_timeframe_minutes")
        or cfg.get("gmma_timeframe_minutes"),
        default,
    )
    if minutes not in {1, 3, 5, 15, 25, 30, 60}:
        return default
    return minutes


def timeframe_interval(cfg: Dict[str, Any], default: int = 5) -> str:
    return f"{timeframe_minutes(cfg, default)}minute"


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
            "timeframe_minutes": timeframe_minutes(cfg, 5),
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


def resolve_gmma_obv_settings(cfg: Dict[str, Any]) -> Dict[str, Any]:
    minutes = timeframe_minutes(cfg, 5)
    return {
        "preset": f"GMMA_OBV_{minutes}M",
        "timeframe": f"{minutes}minute",
        "timeframe_minutes": minutes,
        "adx_len": _int(cfg.get("gmma_adx_len"), 14),
        "adx_min": _num(cfg.get("gmma_adx_min"), 21.0),
        "atr_len": _int(cfg.get("gmma_atr_len"), 14),
        "sl_mult": _num(cfg.get("gmma_sl_mult"), 1.2),
        "tp1_mult": _num(cfg.get("gmma_tp1_mult"), 1.0),
        "tp2_mult": _num(cfg.get("gmma_tp2_mult"), 2.0),
        "tp3_mult": _num(cfg.get("gmma_tp3_mult"), 3.0),
        "obv_fast": _int(cfg.get("gmma_obv_fast"), 5),
        "obv_medium": _int(cfg.get("gmma_obv_medium"), 9),
        "obv_slow": _int(cfg.get("gmma_obv_slow"), 14),
        "obv_donchian": _int(cfg.get("gmma_obv_donchian"), 26),
        "obv_smoothing": _int(cfg.get("gmma_obv_smoothing"), 1),
        "require_gc": _bool(cfg.get("gmma_require_gc"), True),
        "use_trail": _bool(cfg.get("gmma_use_trail"), True),
        "full_exit_tp3": _bool(cfg.get("gmma_full_exit_tp3"), True),
        "partial_profit_enabled": _bool(cfg.get("gmma_partial_profit_enabled"), False),
        "partial_tp1_pct": _num(cfg.get("gmma_partial_tp1_pct"), 50.0),
        "partial_tp2_pct": _num(cfg.get("gmma_partial_tp2_pct"), 25.0),
        "partial_tp3_pct": _num(cfg.get("gmma_partial_tp3_pct"), 25.0),
    }


def validate_gmma_obv_config(cfg: Dict[str, Any]) -> Optional[str]:
    s = resolve_gmma_obv_settings(cfg)
    if not (5 <= s["adx_len"] <= 50 and 1 <= s["adx_min"] <= 60):
        return "GMMA_ADX_SETTINGS_INVALID"
    if not (5 <= s["atr_len"] <= 50 and 0.2 <= s["sl_mult"] <= 10):
        return "GMMA_RISK_SETTINGS_INVALID"
    if not (0.5 <= s["tp1_mult"] < s["tp2_mult"] < s["tp3_mult"] <= 12):
        return "GMMA_TP_MULTIPLIERS_INVALID"
    if not (2 <= s["obv_fast"] < s["obv_medium"] < s["obv_slow"] <= 100):
        return "GMMA_OBV_EMA_LENGTHS_INVALID"
    if not (2 <= s["obv_donchian"] <= 200 and 1 <= s["obv_smoothing"] <= 20):
        return "GMMA_OBV_SETTINGS_INVALID"
    if s["partial_profit_enabled"]:
        percentages = [float(s["partial_tp1_pct"]), float(s["partial_tp2_pct"]), float(s["partial_tp3_pct"])]
        if any(pct < 0 or pct > 100 for pct in percentages):
            return "GMMA_PARTIAL_PERCENT_INVALID"
        if abs(sum(percentages) - 100.0) > 0.001:
            return "GMMA_PARTIAL_PERCENT_TOTAL_MUST_BE_100"
    return None


def resolve_gmma_gold_cross_settings(cfg: Dict[str, Any]) -> Dict[str, Any]:
    # GMMA Gold Cross is intentionally fixed to 5m for live and backtest.
    # The TradingView indicator/requested execution logic is tuned to 5m
    # candles and also checks the prior-day 15:00-15:30 crossover window.
    minutes = 5
    short_defaults = [3, 5, 8, 10, 12, 15]
    long_defaults = [30, 35, 40, 45, 50, 60]
    short_lengths = [
        _int(cfg.get(f"ggc_short_len_{i + 1}"), default)
        for i, default in enumerate(short_defaults)
    ]
    long_lengths = [
        _int(cfg.get(f"ggc_long_len_{i + 1}"), default)
        for i, default in enumerate(long_defaults)
    ]
    return {
        "preset": f"GMMA_GOLD_CROSS_{minutes}M",
        "timeframe": f"{minutes}minute",
        "timeframe_minutes": minutes,
        "short_lengths": short_lengths,
        "long_lengths": long_lengths,
        "entry_mode": str(cfg.get("ggc_entry_mode") or "Golden Cross Only"),
        "require_obv": _bool(cfg.get("ggc_require_obv"), True),
        "allow_shorts": _bool(cfg.get("ggc_allow_shorts"), True),
        "obv_fast": _int(cfg.get("ggc_obv_fast"), 5),
        "obv_medium": _int(cfg.get("ggc_obv_medium"), 9),
        "obv_slow": _int(cfg.get("ggc_obv_slow"), 14),
        "obv_donchian": _int(cfg.get("ggc_obv_donchian"), 26),
        "obv_smoothing": _int(cfg.get("ggc_obv_smoothing"), 1),
        "atr_len": _int(cfg.get("ggc_atr_len"), 14),
        "sl_mult": _num(cfg.get("ggc_sl_mult"), 1.2),
        "tp1_mult": _num(cfg.get("ggc_tp1_mult"), 1.0),
        "tp2_mult": _num(cfg.get("ggc_tp2_mult"), 2.0),
        "tp3_mult": _num(cfg.get("ggc_tp3_mult"), 3.0),
        "use_trail": _bool(cfg.get("ggc_use_trail"), True),
        "full_exit_tp3": _bool(cfg.get("ggc_full_exit_tp3"), True),
        "partial_profit_enabled": _bool(cfg.get("ggc_partial_profit_enabled"), False),
        "partial_tp1_pct": _num(cfg.get("ggc_partial_tp1_pct"), 50.0),
        "partial_tp2_pct": _num(cfg.get("ggc_partial_tp2_pct"), 25.0),
        "partial_tp3_pct": _num(cfg.get("ggc_partial_tp3_pct"), 25.0),
    }


def validate_gmma_gold_cross_config(cfg: Dict[str, Any]) -> Optional[str]:
    s = resolve_gmma_gold_cross_settings(cfg)
    short_lengths = [int(x) for x in s["short_lengths"]]
    long_lengths = [int(x) for x in s["long_lengths"]]
    if len(short_lengths) != 6 or len(long_lengths) != 6:
        return "GMMA_GOLD_CROSS_LENGTHS_INVALID"
    if any(x < 1 or x > 200 for x in short_lengths + long_lengths):
        return "GMMA_GOLD_CROSS_LENGTHS_INVALID"
    if any(short_lengths[i] >= short_lengths[i + 1] for i in range(5)):
        return "GMMA_GOLD_CROSS_SHORT_LENGTHS_INVALID"
    if any(long_lengths[i] >= long_lengths[i + 1] for i in range(5)):
        return "GMMA_GOLD_CROSS_LONG_LENGTHS_INVALID"
    if max(short_lengths) >= min(long_lengths):
        return "GMMA_GOLD_CROSS_SHORTS_MUST_BE_BELOW_LONGS"
    if str(s["entry_mode"]) not in {"Golden Cross Only", "Regime Mode"}:
        return "GMMA_GOLD_CROSS_ENTRY_MODE_INVALID"
    if not (2 <= s["obv_fast"] < s["obv_medium"] < s["obv_slow"] <= 100):
        return "GMMA_GOLD_CROSS_OBV_EMA_LENGTHS_INVALID"
    if not (2 <= s["obv_donchian"] <= 200 and 1 <= s["obv_smoothing"] <= 20):
        return "GMMA_GOLD_CROSS_OBV_SETTINGS_INVALID"
    if not (5 <= s["atr_len"] <= 100 and 0.2 <= s["sl_mult"] <= 10):
        return "GMMA_GOLD_CROSS_RISK_SETTINGS_INVALID"
    if not (0.5 <= s["tp1_mult"] < s["tp2_mult"] < s["tp3_mult"] <= 15):
        return "GMMA_GOLD_CROSS_TP_MULTIPLIERS_INVALID"
    if s["partial_profit_enabled"]:
        percentages = [float(s["partial_tp1_pct"]), float(s["partial_tp2_pct"]), float(s["partial_tp3_pct"])]
        if any(pct < 0 or pct > 100 for pct in percentages):
            return "GMMA_GOLD_CROSS_PARTIAL_PERCENT_INVALID"
        if abs(sum(percentages) - 100.0) > 0.001:
            return "GMMA_GOLD_CROSS_PARTIAL_PERCENT_TOTAL_MUST_BE_100"
    return None


def resolve_liquidity_sweep_settings(cfg: Dict[str, Any]) -> Dict[str, Any]:
    preset = str(cfg.get("liq_risk_preset") or "Balanced").strip()
    risk_presets = {
        "Conservative": (0.50, 1.0, 2.0, 4.0),
        "Balanced": (0.25, 1.0, 2.0, 3.0),
        "Aggressive": (0.15, 1.5, 2.5, 4.0),
        "Scalping": (0.10, 0.8, 1.5, 2.0),
    }
    buf, tp1, tp2, tp3 = risk_presets.get(preset, risk_presets["Balanced"])
    buf = _num(cfg.get("liq_sl_buffer_atr"), buf)
    tp1 = _num(cfg.get("liq_tp1_mult"), tp1)
    tp2 = _num(cfg.get("liq_tp2_mult"), tp2)
    tp3 = _num(cfg.get("liq_tp3_mult"), tp3)
    minutes = timeframe_minutes(cfg, 5)
    return {
        "preset": f"LIQUIDITY_SWEEP_{minutes}M",
        "timeframe": f"{minutes}minute",
        "timeframe_minutes": minutes,
        "swing_len": _int(cfg.get("liq_swing_len"), 21),
        "lookback_bars": _int(cfg.get("liq_lookback_bars"), 80),
        "min_score": _num(cfg.get("liq_min_score"), 50.0),
        "require_choch": _bool(cfg.get("liq_require_choch"), True),
        "minor_len": _int(cfg.get("liq_minor_len"), 8),
        "confirm_window": _int(cfg.get("liq_confirm_window"), 13),
        "use_volume": _bool(cfg.get("liq_use_volume"), True),
        "vol_len": _int(cfg.get("liq_vol_len"), 21),
        "vol_mult": _num(cfg.get("liq_vol_mult"), 1.5),
        "use_htf_bias": _bool(cfg.get("liq_use_htf_bias"), True),
        "htf_ema_len": _int(cfg.get("liq_htf_ema_len"), 50),
        "atr_len": _int(cfg.get("liq_atr_len"), 14),
        "sl_buffer_atr": buf,
        "tp1_mult": tp1,
        "tp2_mult": tp2,
        "tp3_mult": tp3,
        "use_gk_filter": _bool(cfg.get("liq_use_gk_filter"), True),
        "gk_len": _int(cfg.get("liq_gk_len"), 200),
        "gk_mult": _num(cfg.get("liq_gk_mult"), 2.0),
        "gk_atr_len": _int(cfg.get("liq_gk_atr_len"), 21),
        "gk_confirm_bars": _int(cfg.get("liq_gk_confirm_bars"), 2),
        "use_sr_filter": _bool(cfg.get("liq_use_sr_filter"), True),
        "sr_pivot_span": _int(cfg.get("liq_sr_pivot_span"), 5),
        "sr_min_swing_atr": _num(cfg.get("liq_sr_min_swing_atr"), 0.15),
        "sr_near_atr": _num(cfg.get("liq_sr_near_atr"), 1.0),
        "partial_profit_enabled": _bool(cfg.get("liq_partial_profit_enabled"), False),
        "partial_tp1_pct": _num(cfg.get("liq_partial_tp1_pct"), 50.0),
        "partial_tp2_pct": _num(cfg.get("liq_partial_tp2_pct"), 25.0),
        "partial_tp3_pct": _num(cfg.get("liq_partial_tp3_pct"), 25.0),
        "use_trail": _bool(cfg.get("liq_use_trail"), True),
        "full_exit_tp3": _bool(cfg.get("liq_full_exit_tp3"), True),
    }


def validate_liquidity_sweep_config(cfg: Dict[str, Any]) -> Optional[str]:
    s = resolve_liquidity_sweep_settings(cfg)
    if not (3 <= s["swing_len"] <= 100 and 5 <= s["lookback_bars"] <= 500 and 0 <= s["min_score"] <= 100):
        return "LIQUIDITY_SWEEP_SETTINGS_INVALID"
    if not (2 <= s["minor_len"] <= 30 and 1 <= s["confirm_window"] <= 100):
        return "LIQUIDITY_CHOCH_SETTINGS_INVALID"
    if not (2 <= s["vol_len"] <= 200 and 1.0 <= s["vol_mult"] <= 5.0):
        return "LIQUIDITY_VOLUME_SETTINGS_INVALID"
    if not (5 <= s["atr_len"] <= 50 and 0 <= s["sl_buffer_atr"] <= 3.0):
        return "LIQUIDITY_RISK_SETTINGS_INVALID"
    if not (0.5 <= s["tp1_mult"] < s["tp2_mult"] < s["tp3_mult"] <= 15):
        return "LIQUIDITY_TP_MULTIPLIERS_INVALID"
    if not (20 <= s["gk_len"] <= 400 and 0.5 <= s["gk_mult"] <= 6.0 and 1 <= s["gk_confirm_bars"] <= 3):
        return "LIQUIDITY_GK_SETTINGS_INVALID"
    if s["partial_profit_enabled"]:
        percentages = [float(s["partial_tp1_pct"]), float(s["partial_tp2_pct"]), float(s["partial_tp3_pct"])]
        if any(pct < 0 or pct > 100 for pct in percentages):
            return "LIQUIDITY_PARTIAL_PERCENT_INVALID"
        if abs(sum(percentages) - 100.0) > 0.001:
            return "LIQUIDITY_PARTIAL_PERCENT_TOTAL_MUST_BE_100"
    return None


def resolve_pure_liquidity_sweep_settings(cfg: Dict[str, Any]) -> Dict[str, Any]:
    preset = str(cfg.get("pliq_risk_preset") or "Balanced").strip()
    risk_presets = {
        "Conservative": (0.50, 1.0, 2.0, 4.0),
        "Balanced": (0.25, 1.0, 2.0, 3.0),
        "Aggressive": (0.15, 1.5, 2.5, 4.0),
        "Scalping": (0.10, 0.8, 1.5, 2.0),
    }
    buf, tp1, tp2, tp3 = risk_presets.get(preset, risk_presets["Balanced"])
    if preset == "Custom":
        buf = _num(cfg.get("pliq_sl_buffer_atr"), buf)
        tp1 = _num(cfg.get("pliq_tp1_mult"), tp1)
        tp2 = _num(cfg.get("pliq_tp2_mult"), tp2)
        tp3 = _num(cfg.get("pliq_tp3_mult"), tp3)
    else:
        buf = _num(cfg.get("pliq_sl_buffer_atr"), buf)
        tp1 = _num(cfg.get("pliq_tp1_mult"), tp1)
        tp2 = _num(cfg.get("pliq_tp2_mult"), tp2)
        tp3 = _num(cfg.get("pliq_tp3_mult"), tp3)
    minutes = timeframe_minutes(cfg, 5)
    return {
        "preset": f"PURE_LIQUIDITY_SWEEP_{minutes}M",
        "timeframe": f"{minutes}minute",
        "timeframe_minutes": minutes,
        "swing_len": _int(cfg.get("pliq_swing_len"), 21),
        "lookback_bars": _int(cfg.get("pliq_lookback_bars"), 80),
        "sweep_mode": str(cfg.get("pliq_sweep_mode") or "Wicks + Outbreaks & Retest"),
        "min_score": _num(cfg.get("pliq_min_score"), 50.0),
        "require_choch": _bool(cfg.get("pliq_require_choch"), True),
        "minor_len": _int(cfg.get("pliq_minor_len"), 8),
        "confirm_window": _int(cfg.get("pliq_confirm_window"), 13),
        "use_volume": _bool(cfg.get("pliq_use_volume"), True),
        "vol_len": _int(cfg.get("pliq_vol_len"), 21),
        "vol_mult": _num(cfg.get("pliq_vol_mult"), 1.5),
        "use_htf_bias": _bool(cfg.get("pliq_use_htf_bias"), True),
        "htf_ema_len": _int(cfg.get("pliq_htf_ema_len"), 50),
        "atr_len": _int(cfg.get("pliq_atr_len"), 14),
        "sl_buffer_atr": buf,
        "tp1_mult": tp1,
        "tp2_mult": tp2,
        "tp3_mult": tp3,
        "use_trail": _bool(cfg.get("pliq_use_trail"), True),
        "full_exit_tp3": _bool(cfg.get("pliq_full_exit_tp3"), True),
        "partial_profit_enabled": _bool(cfg.get("pliq_partial_profit_enabled"), False),
        "partial_tp1_pct": _num(cfg.get("pliq_partial_tp1_pct"), 50.0),
        "partial_tp2_pct": _num(cfg.get("pliq_partial_tp2_pct"), 25.0),
        "partial_tp3_pct": _num(cfg.get("pliq_partial_tp3_pct"), 25.0),
    }


def validate_pure_liquidity_sweep_config(cfg: Dict[str, Any]) -> Optional[str]:
    s = resolve_pure_liquidity_sweep_settings(cfg)
    if not (3 <= s["swing_len"] <= 100 and 5 <= s["lookback_bars"] <= 500 and 0 <= s["min_score"] <= 100):
        return "PURE_LIQUIDITY_SWEEP_SETTINGS_INVALID"
    if s["sweep_mode"] not in {"Only Wicks", "Only Outbreaks & Retest", "Wicks + Outbreaks & Retest"}:
        return "PURE_LIQUIDITY_SWEEP_MODE_INVALID"
    if not (2 <= s["minor_len"] <= 30 and 1 <= s["confirm_window"] <= 100):
        return "PURE_LIQUIDITY_CHOCH_SETTINGS_INVALID"
    if not (2 <= s["vol_len"] <= 200 and 1.0 <= s["vol_mult"] <= 5.0):
        return "PURE_LIQUIDITY_VOLUME_SETTINGS_INVALID"
    if not (5 <= s["htf_ema_len"] <= 400 and 5 <= s["atr_len"] <= 50 and 0 <= s["sl_buffer_atr"] <= 3.0):
        return "PURE_LIQUIDITY_RISK_SETTINGS_INVALID"
    if not (0.5 <= s["tp1_mult"] < s["tp2_mult"] < s["tp3_mult"] <= 15):
        return "PURE_LIQUIDITY_TP_MULTIPLIERS_INVALID"
    if s["partial_profit_enabled"]:
        percentages = [float(s["partial_tp1_pct"]), float(s["partial_tp2_pct"]), float(s["partial_tp3_pct"])]
        if any(pct < 0 or pct > 100 for pct in percentages):
            return "PURE_LIQUIDITY_PARTIAL_PERCENT_INVALID"
        if abs(sum(percentages) - 100.0) > 0.001:
            return "PURE_LIQUIDITY_PARTIAL_PERCENT_TOTAL_MUST_BE_100"
    return None


def resolve_gvk_trend_settings(cfg: Dict[str, Any]) -> Dict[str, Any]:
    minutes = timeframe_minutes(cfg, 5)
    return {
        "preset": f"GVK_TREND_{minutes}M",
        "timeframe": f"{minutes}minute",
        "timeframe_minutes": minutes,
        "gk_len": _int(cfg.get("gvk_gk_len"), 200),
        "gk_mult": _num(cfg.get("gvk_gk_mult"), 2.0),
        "gk_atr_len": _int(cfg.get("gvk_gk_atr_len"), 21),
        "gk_confirm_bars": _int(cfg.get("gvk_gk_confirm_bars"), 2),
        "entry_mode": str(cfg.get("gvk_entry_mode") or "Trend Flip Only"),
        "min_score": _num(cfg.get("gvk_min_score"), 4.0),
        "atr_len": _int(cfg.get("gvk_atr_len"), 14),
        "sl_mult": _num(cfg.get("gvk_sl_mult"), 1.2),
        "tp1_mult": _num(cfg.get("gvk_tp1_mult"), 1.0),
        "tp2_mult": _num(cfg.get("gvk_tp2_mult"), 2.0),
        "tp3_mult": _num(cfg.get("gvk_tp3_mult"), 3.0),
        "use_trail": _bool(cfg.get("gvk_use_trail"), True),
        "full_exit_tp3": _bool(cfg.get("gvk_full_exit_tp3"), True),
        "exit_on_reversal": _bool(cfg.get("gvk_exit_on_reversal"), True),
        "partial_profit_enabled": _bool(cfg.get("gvk_partial_profit_enabled"), False),
        "partial_tp1_pct": _num(cfg.get("gvk_partial_tp1_pct"), 50.0),
        "partial_tp2_pct": _num(cfg.get("gvk_partial_tp2_pct"), 25.0),
        "partial_tp3_pct": _num(cfg.get("gvk_partial_tp3_pct"), 25.0),
    }


def validate_gvk_trend_config(cfg: Dict[str, Any]) -> Optional[str]:
    s = resolve_gvk_trend_settings(cfg)
    if not (20 <= s["gk_len"] <= 500 and 0.2 <= s["gk_mult"] <= 8.0 and 1 <= s["gk_confirm_bars"] <= 3):
        return "GVK_TREND_GK_SETTINGS_INVALID"
    if str(s["entry_mode"]) not in {"Trend Flip Only", "Trend Mode"}:
        return "GVK_TREND_ENTRY_MODE_INVALID"
    if not (0 <= s["min_score"] <= 5 and 5 <= s["atr_len"] <= 100 and 0.2 <= s["sl_mult"] <= 10):
        return "GVK_TREND_RISK_SETTINGS_INVALID"
    if not (0.5 <= s["tp1_mult"] < s["tp2_mult"] < s["tp3_mult"] <= 15):
        return "GVK_TREND_TP_MULTIPLIERS_INVALID"
    if s["partial_profit_enabled"]:
        percentages = [float(s["partial_tp1_pct"]), float(s["partial_tp2_pct"]), float(s["partial_tp3_pct"])]
        if any(pct < 0 or pct > 100 for pct in percentages):
            return "GVK_TREND_PARTIAL_PERCENT_INVALID"
        if abs(sum(percentages) - 100.0) > 0.001:
            return "GVK_TREND_PARTIAL_PERCENT_TOTAL_MUST_BE_100"
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


def _heikin_ashi(candles: Sequence[Dict[str, Any]]) -> tuple[List[float], List[float]]:
    ha_open: List[float] = []
    ha_close: List[float] = []
    for i, candle in enumerate(candles):
        open_ = _num(candle.get("open"), 0)
        high = _num(candle.get("high"), 0)
        low = _num(candle.get("low"), 0)
        close = _num(candle.get("close"), 0)
        close_ha = (open_ + high + low + close) / 4.0
        open_ha = (open_ + close) / 2.0 if i == 0 else (ha_open[-1] + ha_close[-1]) / 2.0
        ha_open.append(open_ha)
        ha_close.append(close_ha)
    return ha_open, ha_close


def _obv_traffic_lights(
    candles: Sequence[Dict[str, Any]],
    fast_len: int,
    medium_len: int,
    slow_len: int,
    donchian_len: int,
    smoothing: int = 1,
) -> Dict[str, List[float]]:
    _ha_open, ha_close = _heikin_ashi(candles)
    close_val = _ema(ha_close, max(1, smoothing)) if smoothing > 1 else list(ha_close)
    volumes = [_num(candle.get("volume"), 0) for candle in candles]
    obv: List[float] = []
    running = 0.0
    for i, close_ha in enumerate(close_val):
        prev = close_val[i - 1] if i else close_ha
        vol = volumes[i] if close_ha > prev else -volumes[i] if close_ha < prev else 0.0
        running += vol
        obv.append(running)
    ma1 = _ema(obv, fast_len)
    ma2 = _ema(obv, medium_len)
    ma3 = _ema(obv, slow_len)
    baseline: List[float] = []
    lookback = max(1, int(donchian_len))
    for i in range(len(obv)):
        window = obv[max(0, i + 1 - lookback) : i + 1]
        baseline.append((min(window) + max(window)) / 2.0 if window else 0.0)
    return {"obv": obv, "ma1": ma1, "ma2": ma2, "ma3": ma3, "baseline": baseline}


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


def evaluate_gmma_obv_strategy(
    candles: Sequence[Dict[str, Any]],
    cfg: Dict[str, Any],
) -> tuple[Optional[StrategySignal], Dict[str, Any]]:
    s = resolve_gmma_obv_settings(cfg)
    short_lengths = [3, 5, 8, 10, 12, 15]
    long_lengths = [30, 35, 40, 45, 50, 60]
    minimum = max(max(long_lengths), int(s["obv_donchian"]), int(s["adx_len"]), int(s["atr_len"])) + 5
    if len(candles) < minimum:
        return None, {"reason": "GMMA_NOT_ENOUGH_CANDLES", "required": minimum, "received": len(candles)}

    closes = [_num(x.get("close"), 0) for x in candles]
    highs = [_num(x.get("high"), 0) for x in candles]
    lows = [_num(x.get("low"), 0) for x in candles]
    volumes = [_num(x.get("volume"), 0) for x in candles]
    if any(x <= 0 for x in closes[-2:]) or not any(x > 0 for x in volumes):
        return None, {"reason": "GMMA_BAD_CANDLE_DATA"}

    short_emas = [_ema(closes, length) for length in short_lengths]
    long_emas = [_ema(closes, length) for length in long_lengths]

    def _gmma_cross_at(index: int, short_values: List[float]) -> int:
        longs = [series[index] for series in long_emas]
        if all(short_values[index] > value for value in longs):
            return 1
        if all(short_values[index] < value for value in longs):
            return -1
        return 0

    now_crosses = [_gmma_cross_at(-1, series) for series in short_emas]
    prev_s6_cross = _gmma_cross_at(-2, short_emas[5])
    gc = sum(now_crosses[:5]) == 5 and prev_s6_cross <= 0 and now_crosses[5] > 0
    dc = sum(now_crosses[:5]) == -5 and prev_s6_cross >= 0 and now_crosses[5] < 0

    short_now = [series[-1] for series in short_emas]
    long_now = [series[-1] for series in long_emas]
    trend_up_s = all(short_now[i] > short_now[i + 1] for i in range(len(short_now) - 1))
    trend_up_l = all(long_now[i] > long_now[i + 1] for i in range(len(long_now) - 1))
    trend_down_s = all(short_now[i] < short_now[i + 1] for i in range(len(short_now) - 1))
    trend_down_l = all(long_now[i] < long_now[i + 1] for i in range(len(long_now) - 1))

    obv = _obv_traffic_lights(
        candles,
        int(s["obv_fast"]),
        int(s["obv_medium"]),
        int(s["obv_slow"]),
        int(s["obv_donchian"]),
        int(s["obv_smoothing"]),
    )
    obv_bull = obv["obv"][-1] > obv["ma3"][-1] and obv["ma1"][-1] > obv["ma3"][-1]
    obv_bear = obv["obv"][-1] < obv["ma3"][-1] and obv["ma1"][-1] < obv["ma3"][-1]

    plus_di, minus_di, adx = _dmi(candles, int(s["adx_len"]))
    vwap = _session_vwap(candles)
    atr_values = _rma(_true_ranges(candles), int(s["atr_len"]))
    atr = atr_values[-1]
    atr_average = _sma_last(atr_values, 42) or atr
    vol_ratio = atr / atr_average if atr_average > 0 else 1.0
    volatility = "High" if vol_ratio > 1.3 else "Low" if vol_ratio < 0.7 else "Normal"

    close = closes[-1]
    require_gc = bool(s["require_gc"])
    indicator_values = {
        "close": close,
        "gmma_s1_ema3": short_emas[0][-1],
        "gmma_s2_ema5": short_emas[1][-1],
        "gmma_s3_ema8": short_emas[2][-1],
        "gmma_s4_ema10": short_emas[3][-1],
        "gmma_s5_ema12": short_emas[4][-1],
        "gmma_s6_ema15": short_emas[5][-1],
        "gmma_l1_ema30": long_emas[0][-1],
        "gmma_l2_ema35": long_emas[1][-1],
        "gmma_l3_ema40": long_emas[2][-1],
        "gmma_l4_ema45": long_emas[3][-1],
        "gmma_l5_ema50": long_emas[4][-1],
        "gmma_l6_ema60": long_emas[5][-1],
        "gmma_s6_cross": now_crosses[5],
        "gmma_prev_s6_cross": prev_s6_cross,
        "gmma_cross_count_first5": sum(now_crosses[:5]),
        "gmma_gc": gc,
        "gmma_dc": dc,
        "gmma_trend_up_short": trend_up_s,
        "gmma_trend_up_long": trend_up_l,
        "gmma_trend_down_short": trend_down_s,
        "gmma_trend_down_long": trend_down_l,
        "obv": obv["obv"][-1],
        "obv_fast_ema": obv["ma1"][-1],
        "obv_medium_ema": obv["ma2"][-1],
        "obv_slow_ema": obv["ma3"][-1],
        "obv_donchian_baseline": obv["baseline"][-1],
        "vwap": vwap[-1],
        "adx": adx[-1],
        "plus_di": plus_di[-1],
        "minus_di": minus_di[-1],
        "atr": atr,
        "vol_ratio": vol_ratio,
    }
    bull_checks = {
        "gmma_gc": gc or (not require_gc and min(short_now) > max(long_now)),
        "gmma_trend": trend_up_s and trend_up_l,
        "obv": obv_bull,
        "vwap": close > vwap[-1],
        "adx": adx[-1] > float(s["adx_min"]) and plus_di[-1] > minus_di[-1],
    }
    bear_checks = {
        "gmma_dc": dc or (not require_gc and max(short_now) < min(long_now)),
        "gmma_trend": trend_down_s and trend_down_l,
        "obv": obv_bear,
        "vwap": close < vwap[-1],
        "adx": adx[-1] > float(s["adx_min"]) and minus_di[-1] > plus_di[-1],
    }
    bull_score = float(sum(1 for ok in bull_checks.values() if ok))
    bear_score = float(sum(1 for ok in bear_checks.values() if ok))
    max_score = 5.0
    buy = bull_score == max_score
    sell = bear_score == max_score
    if not buy and not sell:
        return None, {
            "reason": "GMMA_ENTRY_CHECK_FAILED",
            "bull_score": bull_score,
            "bear_score": bear_score,
            "max_score": max_score,
            "bull_checks": bull_checks,
            "bear_checks": bear_checks,
            "adx": adx[-1],
            "plus_di": plus_di[-1],
            "minus_di": minus_di[-1],
            "vwap": vwap[-1],
            "obv": obv["obv"][-1],
            "obv_slow": obv["ma3"][-1],
            "indicators": indicator_values,
        }

    side = "BUY" if buy else "SELL"
    score = bull_score if buy else bear_score
    direction = 1.0 if buy else -1.0
    stop = close - atr * float(s["sl_mult"]) if buy else close + atr * float(s["sl_mult"])
    risk = abs(close - stop)
    if risk <= 0:
        return None, {"reason": "GMMA_BAD_RISK"}

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
    return signal, {
        "reason": "CUSTOM_SIGNAL_OK",
        "timeframe": str(s["timeframe"]),
        "adx": adx[-1],
        "plus_di": plus_di[-1],
        "minus_di": minus_di[-1],
        "vwap": vwap[-1],
        "obv": obv["obv"][-1],
        "obv_slow": obv["ma3"][-1],
        "indicators": indicator_values,
        **signal.to_dict(),
    }


def gmma_gold_cross_required_candles(cfg: Dict[str, Any]) -> int:
    s = resolve_gmma_gold_cross_settings(cfg)
    return max(
        max(int(x) for x in s["short_lengths"]),
        max(int(x) for x in s["long_lengths"]),
        int(s["obv_donchian"]),
        int(s["obv_slow"]),
        int(s["atr_len"]),
    ) + 5


def evaluate_gmma_gold_cross_strategy(
    candles: Sequence[Dict[str, Any]],
    cfg: Dict[str, Any],
) -> tuple[Optional[StrategySignal], Dict[str, Any]]:
    s = resolve_gmma_gold_cross_settings(cfg)
    minimum = gmma_gold_cross_required_candles(cfg)
    if len(candles) < minimum:
        return None, {"reason": "GMMA_GOLD_CROSS_NOT_ENOUGH_CANDLES", "required": minimum, "received": len(candles)}

    closes = [_num(x.get("close"), 0) for x in candles]
    highs = [_num(x.get("high"), 0) for x in candles]
    lows = [_num(x.get("low"), 0) for x in candles]
    volumes = [_num(x.get("volume"), 0) for x in candles]
    if any(x <= 0 for x in closes[-2:]) or not any(x > 0 for x in volumes):
        return None, {"reason": "GMMA_GOLD_CROSS_BAD_CANDLE_DATA"}

    short_lengths = [int(x) for x in s["short_lengths"]]
    long_lengths = [int(x) for x in s["long_lengths"]]
    short_emas = [_ema(closes, length) for length in short_lengths]
    long_emas = [_ema(closes, length) for length in long_lengths]

    def _gmma_cross_at(index: int, short_values: List[float]) -> int:
        longs = [series[index] for series in long_emas]
        if all(short_values[index] > value for value in longs):
            return 1
        if all(short_values[index] < value for value in longs):
            return -1
        return 0

    now_crosses = [_gmma_cross_at(-1, series) for series in short_emas]
    prev_s6_cross = _gmma_cross_at(-2, short_emas[5])
    gc = sum(now_crosses[:5]) == 5 and prev_s6_cross <= 0 and now_crosses[5] > 0
    dc = sum(now_crosses[:5]) == -5 and prev_s6_cross >= 0 and now_crosses[5] < 0

    candle_dates: List[Optional[datetime]] = []
    for candle in candles:
        stamp = candle.get("date")
        candle_dates.append(stamp if isinstance(stamp, datetime) else None)
    distinct_days = sorted({stamp.date() for stamp in candle_dates if isinstance(stamp, datetime)})
    latest_day = distinct_days[-1] if distinct_days else None
    previous_day = distinct_days[-2] if len(distinct_days) >= 2 else None

    def _in_gold_cross_window(index: int) -> bool:
        stamp = candle_dates[index]
        if not isinstance(stamp, datetime) or latest_day is None:
            return index == len(candles) - 1
        local_time = stamp.time()
        if stamp.date() == latest_day:
            return True
        if previous_day is not None and stamp.date() == previous_day:
            return local_time >= datetime.strptime("15:00", "%H:%M").time() and local_time <= datetime.strptime("15:30", "%H:%M").time()
        return False

    gc_window = False
    dc_window = False
    gc_window_time = ""
    dc_window_time = ""
    for i in range(1, len(candles)):
        if not _in_gold_cross_window(i):
            continue
        crosses_i = [_gmma_cross_at(i, series) for series in short_emas]
        prev_s6_i = _gmma_cross_at(i - 1, short_emas[5])
        gc_i = sum(crosses_i[:5]) == 5 and prev_s6_i <= 0 and crosses_i[5] > 0
        dc_i = sum(crosses_i[:5]) == -5 and prev_s6_i >= 0 and crosses_i[5] < 0
        stamp = candle_dates[i]
        stamp_text = stamp.isoformat() if isinstance(stamp, datetime) else str(candles[i].get("date") or "")
        if gc_i:
            gc_window = True
            gc_window_time = stamp_text
        if dc_i:
            dc_window = True
            dc_window_time = stamp_text

    short_now = [series[-1] for series in short_emas]
    long_now = [series[-1] for series in long_emas]
    trend_up_s = all(short_now[i] > short_now[i + 1] for i in range(len(short_now) - 1))
    trend_up_l = all(long_now[i] > long_now[i + 1] for i in range(len(long_now) - 1))
    trend_down_s = all(short_now[i] < short_now[i + 1] for i in range(len(short_now) - 1))
    trend_down_l = all(long_now[i] < long_now[i + 1] for i in range(len(long_now) - 1))
    gc_regime = min(short_now) > max(long_now)
    dc_regime = max(short_now) < min(long_now)

    obv = _obv_traffic_lights(
        candles,
        int(s["obv_fast"]),
        int(s["obv_medium"]),
        int(s["obv_slow"]),
        int(s["obv_donchian"]),
        int(s["obv_smoothing"]),
    )
    obv_bull = obv["obv"][-1] > obv["ma3"][-1] and obv["ma1"][-1] > obv["ma3"][-1]
    obv_bear = obv["obv"][-1] < obv["ma3"][-1] and obv["ma1"][-1] < obv["ma3"][-1]
    atr_values = _rma(_true_ranges(candles), int(s["atr_len"]))
    atr = atr_values[-1]
    atr_average = _sma_last(atr_values, 42) or atr
    vol_ratio = atr / atr_average if atr_average > 0 else 1.0
    volatility = "High" if vol_ratio > 1.3 else "Low" if vol_ratio < 0.7 else "Normal"
    close = closes[-1]
    regime_mode = str(s["entry_mode"]) == "Regime Mode"
    require_obv = bool(s["require_obv"])
    allow_shorts = bool(s["allow_shorts"])

    indicator_values = {
        "close": close,
        "ggc_s1_ema": short_emas[0][-1],
        "ggc_s2_ema": short_emas[1][-1],
        "ggc_s3_ema": short_emas[2][-1],
        "ggc_s4_ema": short_emas[3][-1],
        "ggc_s5_ema": short_emas[4][-1],
        "ggc_s6_ema": short_emas[5][-1],
        "ggc_l1_ema": long_emas[0][-1],
        "ggc_l2_ema": long_emas[1][-1],
        "ggc_l3_ema": long_emas[2][-1],
        "ggc_l4_ema": long_emas[3][-1],
        "ggc_l5_ema": long_emas[4][-1],
        "ggc_l6_ema": long_emas[5][-1],
        "ggc_gc": gc,
        "ggc_dc": dc,
        "ggc_gc_window": gc_window,
        "ggc_dc_window": dc_window,
        "ggc_gc_window_time": gc_window_time,
        "ggc_dc_window_time": dc_window_time,
        "ggc_gc_regime": gc_regime,
        "ggc_dc_regime": dc_regime,
        "ggc_s6_cross": now_crosses[5],
        "ggc_prev_s6_cross": prev_s6_cross,
        "ggc_cross_count_first5": sum(now_crosses[:5]),
        "ggc_trend_up_short": trend_up_s,
        "ggc_trend_up_long": trend_up_l,
        "ggc_trend_down_short": trend_down_s,
        "ggc_trend_down_long": trend_down_l,
        "obv": obv["obv"][-1],
        "obv_fast_ema": obv["ma1"][-1],
        "obv_medium_ema": obv["ma2"][-1],
        "obv_slow_ema": obv["ma3"][-1],
        "obv_donchian_baseline": obv["baseline"][-1],
        "obv_bull": obv_bull,
        "obv_bear": obv_bear,
        "atr": atr,
        "vol_ratio": vol_ratio,
        "entry_mode": str(s["entry_mode"]),
    }
    bull_checks = {
        "gmma_gc": gc or gc_window or (regime_mode and gc_regime),
        "gc_regime": gc_regime,
        "gmma_trend": trend_up_s and trend_up_l,
        "obv": obv_bull or not require_obv,
    }
    bear_checks = {
        "gmma_dc": (dc or dc_window or (regime_mode and dc_regime)) and allow_shorts,
        "dc_regime": dc_regime and allow_shorts,
        "gmma_trend": trend_down_s and trend_down_l and allow_shorts,
        "obv": (obv_bear or not require_obv) and allow_shorts,
    }
    bull_score = float(sum(1 for ok in bull_checks.values() if ok))
    bear_score = float(sum(1 for ok in bear_checks.values() if ok))
    max_score = 4.0
    buy = bull_score == max_score
    sell = bear_score == max_score
    if not buy and not sell:
        return None, {
            "reason": "GMMA_GOLD_CROSS_ENTRY_CHECK_FAILED",
            "bull_score": bull_score,
            "bear_score": bear_score,
            "max_score": max_score,
            "bull_checks": bull_checks,
            "bear_checks": bear_checks,
            "obv": obv["obv"][-1],
            "obv_slow": obv["ma3"][-1],
            "indicators": indicator_values,
        }

    side = "BUY" if buy else "SELL"
    score = bull_score if buy else bear_score
    direction = 1.0 if buy else -1.0
    stop = close - atr * float(s["sl_mult"]) if buy else close + atr * float(s["sl_mult"])
    risk = abs(close - stop)
    if risk <= 0:
        return None, {"reason": "GMMA_GOLD_CROSS_BAD_RISK", "indicators": indicator_values}

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
    return signal, {
        "reason": "CUSTOM_SIGNAL_OK",
        "timeframe": str(s["timeframe"]),
        "bull_score": bull_score,
        "bear_score": bear_score,
        "bull_checks": bull_checks,
        "bear_checks": bear_checks,
        "obv": obv["obv"][-1],
        "obv_slow": obv["ma3"][-1],
        "indicators": indicator_values,
        **signal.to_dict(),
    }


def _pivot_highs(values: Sequence[float], left_right: int) -> List[tuple[int, float]]:
    span = max(1, int(left_right))
    out: List[tuple[int, float]] = []
    for i in range(span, len(values) - span):
        window = values[i - span : i + span + 1]
        if values[i] == max(window):
            out.append((i, float(values[i])))
    return out


def _pivot_lows(values: Sequence[float], left_right: int) -> List[tuple[int, float]]:
    span = max(1, int(left_right))
    out: List[tuple[int, float]] = []
    for i in range(span, len(values) - span):
        window = values[i - span : i + span + 1]
        if values[i] == min(window):
            out.append((i, float(values[i])))
    return out


def _gk_trend(candles: Sequence[Dict[str, Any]], length: int, mult: float, atr_len: int, confirm: int) -> Dict[str, Any]:
    closes = [_num(x.get("close"), 0) for x in candles]
    lag = max(int((max(1, length) - 1) // 2), 0)
    zl_src = []
    for i, close in enumerate(closes):
        zl_src.append(close + (close - closes[i - lag]) if lag > 0 and i >= lag else close)
    zl = _ema(zl_src, max(1, int(length)))
    atr = _rma(_true_ranges(candles), max(1, int(atr_len)))
    up = [z + a * float(mult) for z, a in zip(zl, atr)]
    dn = [z - a * float(mult) for z, a in zip(zl, atr)]
    tr = 0
    trends: List[int] = []
    c = max(1, min(3, int(confirm)))
    for i in range(len(candles)):
        bull = i >= c and closes[i] > up[i] and closes[i - 1] > up[i - 1] and closes[i - c + 1] > up[i - c + 1] and zl[i] > zl[i - 1]
        bear = i >= c and closes[i] < dn[i] and closes[i - 1] < dn[i - 1] and closes[i - c + 1] < dn[i - c + 1] and zl[i] < zl[i - 1]
        tr = 1 if bull else -1 if bear else tr
        trends.append(tr)
    return {
        "trend": trends[-1] if trends else 0,
        "zl": zl[-1] if zl else 0.0,
        "up": up[-1] if up else 0.0,
        "dn": dn[-1] if dn else 0.0,
    }


def _gk_trend_series(
    candles: Sequence[Dict[str, Any]],
    length: int,
    mult: float,
    atr_len: int,
    confirm: int,
) -> Dict[str, List[Any]]:
    closes = [_num(x.get("close"), 0) for x in candles]
    lag = max(int((max(1, length) - 1) // 2), 0)
    zl_src = [close + (close - closes[i - lag]) if lag > 0 and i >= lag else close for i, close in enumerate(closes)]
    zl = _ema(zl_src, max(1, int(length)))
    atr = _rma(_true_ranges(candles), max(1, int(atr_len)))
    up = [z + a * float(mult) for z, a in zip(zl, atr)]
    dn = [z - a * float(mult) for z, a in zip(zl, atr)]
    c = max(1, min(3, int(confirm)))
    tr = 0
    trends: List[int] = []
    bulls: List[bool] = []
    bears: List[bool] = []
    flips: List[bool] = []
    for i in range(len(candles)):
        ready = i >= max(1, c - 1)
        bull = bool(ready and closes[i] > up[i] and closes[i - 1] > up[i - 1] and closes[i - c + 1] > up[i - c + 1] and zl[i] > zl[i - 1])
        bear = bool(ready and closes[i] < dn[i] and closes[i - 1] < dn[i - 1] and closes[i - c + 1] < dn[i - c + 1] and zl[i] < zl[i - 1])
        previous = tr
        tr = 1 if bull else -1 if bear else tr
        trends.append(tr)
        bulls.append(bull)
        bears.append(bear)
        flips.append(bool(tr != previous and tr != 0))
    return {"zl": zl, "atr": atr, "up": up, "dn": dn, "trend": trends, "bull": bulls, "bear": bears, "flip": flips}


def gvk_trend_required_candles(cfg: Dict[str, Any]) -> int:
    s = resolve_gvk_trend_settings(cfg)
    return max(int(s["gk_len"]), int(s["gk_atr_len"]), int(s["atr_len"])) + 5


def evaluate_gvk_trend_strategy(
    candles: Sequence[Dict[str, Any]],
    cfg: Dict[str, Any],
) -> tuple[Optional[StrategySignal], Dict[str, Any]]:
    s = resolve_gvk_trend_settings(cfg)
    minimum = gvk_trend_required_candles(cfg)
    if len(candles) < minimum:
        return None, {"reason": "GVK_TREND_NOT_ENOUGH_CANDLES", "required": minimum, "received": len(candles)}

    closes = [_num(x.get("close"), 0) for x in candles]
    highs = [_num(x.get("high"), 0) for x in candles]
    lows = [_num(x.get("low"), 0) for x in candles]
    if any(x <= 0 for x in closes[-2:]):
        return None, {"reason": "GVK_TREND_BAD_CANDLE_DATA"}

    ribbon = _gk_trend_series(
        candles,
        int(s["gk_len"]),
        float(s["gk_mult"]),
        int(s["gk_atr_len"]),
        int(s["gk_confirm_bars"]),
    )
    atr_values = _rma(_true_ranges(candles), int(s["atr_len"]))
    i = len(candles) - 1
    trend = int(ribbon["trend"][i])
    prev_trend = int(ribbon["trend"][i - 1]) if i > 0 else 0
    flip = bool(ribbon["flip"][i])
    bull_confirm = bool(ribbon["bull"][i])
    bear_confirm = bool(ribbon["bear"][i])
    zl = float(ribbon["zl"][i])
    prev_zl = float(ribbon["zl"][i - 1]) if i > 0 else zl
    upper = float(ribbon["up"][i])
    lower = float(ribbon["dn"][i])
    close = closes[i]
    atr = atr_values[i]
    atr_average = _sma_last(atr_values, 42) or atr
    vol_ratio = atr / atr_average if atr_average > 0 else 1.0
    volatility = "High" if vol_ratio > 1.3 else "Low" if vol_ratio < 0.7 else "Normal"
    entry_mode = str(s["entry_mode"])
    flip_only = entry_mode == "Trend Flip Only"

    bull_checks = {
        "flip_buy": flip and trend == 1 and prev_trend != 1,
        "bull_confirm": bull_confirm,
        "close_above_upper": close > upper,
        "zl_rising": zl > prev_zl,
        "trend_mode": trend == 1,
    }
    bear_checks = {
        "flip_sell": flip and trend == -1 and prev_trend != -1,
        "bear_confirm": bear_confirm,
        "close_below_lower": close < lower,
        "zl_falling": zl < prev_zl,
        "trend_mode": trend == -1,
    }
    bull_score = float(sum(1 for ok in bull_checks.values() if ok))
    bear_score = float(sum(1 for ok in bear_checks.values() if ok))
    max_score = 5.0
    buy = bull_checks["flip_buy"] if flip_only else (trend == 1 and bull_score >= float(s["min_score"]))
    sell = bear_checks["flip_sell"] if flip_only else (trend == -1 and bear_score >= float(s["min_score"]))
    indicators = {
        "gvk_zl": zl,
        "gvk_upper": upper,
        "gvk_lower": lower,
        "gvk_trend": trend,
        "gvk_prev_trend": prev_trend,
        "gvk_flip": flip,
        "gvk_bull_confirm": bull_confirm,
        "gvk_bear_confirm": bear_confirm,
        "gvk_atr_band": float(ribbon["atr"][i]),
        "atr": atr,
        "vol_ratio": vol_ratio,
        "entry_mode": entry_mode,
    }
    if not buy and not sell:
        return None, {
            "reason": "GVK_TREND_ENTRY_CHECK_FAILED",
            "bull_score": bull_score,
            "bear_score": bear_score,
            "max_score": max_score,
            "bull_checks": bull_checks,
            "bear_checks": bear_checks,
            "indicators": indicators,
        }

    side = "BUY" if buy else "SELL"
    direction = 1.0 if buy else -1.0
    score = bull_score if buy else bear_score
    raw_stop = lower if buy else upper
    atr_stop = close - atr * float(s["sl_mult"]) if buy else close + atr * float(s["sl_mult"])
    stop = min(raw_stop, atr_stop) if buy else max(raw_stop, atr_stop)
    if buy and stop >= close:
        stop = close - atr * float(s["sl_mult"])
    if sell and stop <= close:
        stop = close + atr * float(s["sl_mult"])
    risk = abs(close - stop)
    if risk <= 0:
        return None, {"reason": "GVK_TREND_BAD_RISK", "indicators": indicators}

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
    return signal, {
        "reason": "CUSTOM_SIGNAL_OK",
        "timeframe": str(s["timeframe"]),
        "bull_score": bull_score,
        "bear_score": bear_score,
        "bull_checks": bull_checks,
        "bear_checks": bear_checks,
        "indicators": indicators,
        **signal.to_dict(),
    }


def _nearest_sr_zones(candles: Sequence[Dict[str, Any]], pivot_span: int, min_swing_atr: float) -> Dict[str, Any]:
    highs = [_num(x.get("high"), 0) for x in candles]
    lows = [_num(x.get("low"), 0) for x in candles]
    closes = [_num(x.get("close"), 0) for x in candles]
    atr = _rma(_true_ranges(candles), 14)[-1] if candles else 0.0
    trend = _ema(closes, 50)
    close = closes[-1] if closes else 0.0
    supports: List[tuple[float, float]] = []
    resistances: List[tuple[float, float]] = []
    for idx, price in _pivot_lows(lows, pivot_span):
        local = min(lows[max(0, idx - 1)], lows[min(len(lows) - 1, idx + 1)])
        swing = max((local - price) / atr, 0.0) if atr > 0 else 0.0
        if swing >= min_swing_atr:
            trend_score = 1.0 if price > trend[idx] else 0.0
            supports.append((price, min(100.0, 40.0 + swing * 28.0 + trend_score * 12.0)))
    for idx, price in _pivot_highs(highs, pivot_span):
        local = max(highs[max(0, idx - 1)], highs[min(len(highs) - 1, idx + 1)])
        swing = max((price - local) / atr, 0.0) if atr > 0 else 0.0
        if swing >= min_swing_atr:
            trend_score = 1.0 if price < trend[idx] else 0.0
            resistances.append((price, min(100.0, 40.0 + swing * 28.0 + trend_score * 12.0)))
    nearest_support = max([p for p, _s in supports if p < close], default=0.0)
    nearest_resistance = min([p for p, _s in resistances if p > close], default=0.0)
    return {
        "nearest_support": nearest_support,
        "nearest_resistance": nearest_resistance,
        "support_count": len(supports),
        "resistance_count": len(resistances),
    }


def evaluate_liquidity_sweep_strategy(
    candles: Sequence[Dict[str, Any]],
    cfg: Dict[str, Any],
) -> tuple[Optional[StrategySignal], Dict[str, Any]]:
    s = resolve_liquidity_sweep_settings(cfg)
    swing_len = int(s["swing_len"])
    minor_len = int(s["minor_len"])
    minimum = max(
        swing_len * 2 + int(s["lookback_bars"]) // 2,
        minor_len * 2 + int(s["confirm_window"]) + 5,
        max(int(s["gk_len"]), int(s["gk_atr_len"]) + 5) if s["use_gk_filter"] else 0,
        int(s["vol_len"]) + int(s["atr_len"]) + 5,
    )
    if len(candles) < minimum:
        return None, {"reason": "LIQUIDITY_NOT_ENOUGH_CANDLES", "required": minimum, "received": len(candles)}

    highs = [_num(x.get("high"), 0) for x in candles]
    lows = [_num(x.get("low"), 0) for x in candles]
    closes = [_num(x.get("close"), 0) for x in candles]
    volumes = [_num(x.get("volume"), 0) for x in candles]
    atr_values = _rma(_true_ranges(candles), int(s["atr_len"]))
    vol_sma = [_sma_last(volumes[: i + 1], int(s["vol_len"])) for i in range(len(volumes))]
    htf_ema = _ema(closes, int(s["htf_ema_len"]))
    swing_highs = _pivot_highs(highs, swing_len)
    swing_lows = _pivot_lows(lows, swing_len)
    minor_highs = _pivot_highs(highs, minor_len)
    minor_lows = _pivot_lows(lows, minor_len)
    gk = _gk_trend(candles, int(s["gk_len"]), float(s["gk_mult"]), int(s["gk_atr_len"]), int(s["gk_confirm_bars"]))
    sr = _nearest_sr_zones(candles, int(s["sr_pivot_span"]), float(s["sr_min_swing_atr"]))

    pending: Optional[Dict[str, Any]] = None
    last_meta: Dict[str, Any] = {}
    fire: Optional[Dict[str, Any]] = None
    lookback = int(s["lookback_bars"])

    def score_at(i: int, direction: int, level: float) -> tuple[float, Dict[str, float]]:
        atr = max(atr_values[i], 0.000001)
        penetration = (level - lows[i]) / atr if direction == 1 else (highs[i] - level) / atr
        reclaim = (closes[i] - level) / atr if direction == 1 else (level - closes[i]) / atr
        vol_ratio = volumes[i] / vol_sma[i] if vol_sma[i] > 0 else 1.0
        vol_comp = min(vol_ratio / float(s["vol_mult"]), 1.0) if s["use_volume"] else 1.0
        htf_ok = closes[i] > htf_ema[i] if direction == 1 else closes[i] < htf_ema[i]
        htf_comp = 1.0 if (not s["use_htf_bias"] or htf_ok) else 0.0
        raw = min(max(penetration, 0.0), 1.5) / 1.5 * 35.0
        raw += min(max(reclaim, 0.0), 1.0) * 25.0
        raw += vol_comp * 20.0
        raw += htf_comp * 10.0
        raw += 10.0
        return max(0.0, min(100.0, raw)), {
            "penetration_atr": penetration,
            "reclaim_atr": reclaim,
            "volume_ratio": vol_ratio,
            "vol_comp": vol_comp,
            "htf_comp": htf_comp,
        }

    for i in range(minimum - 1, len(candles)):
        confirmed_highs = [(idx, price) for idx, price in swing_highs if idx <= i - swing_len and i - idx <= lookback]
        confirmed_lows = [(idx, price) for idx, price in swing_lows if idx <= i - swing_len and i - idx <= lookback]
        last_minor_high = next((price for idx, price in reversed(minor_highs) if idx <= i - minor_len), 0.0)
        last_minor_low = next((price for idx, price in reversed(minor_lows) if idx <= i - minor_len), 0.0)
        bull_level = next((price for _idx, price in reversed(confirmed_lows) if lows[i] < price < closes[i]), 0.0)
        bear_level = next((price for _idx, price in reversed(confirmed_highs) if highs[i] > price > closes[i]), 0.0)
        bull_score, bull_parts = score_at(i, 1, bull_level) if bull_level else (0.0, {})
        bear_score, bear_parts = score_at(i, -1, bear_level) if bear_level else (0.0, {})
        bull_q = bull_level > 0 and bull_score >= float(s["min_score"])
        bear_q = bear_level > 0 and bear_score >= float(s["min_score"])

        if pending and i - int(pending["bar"]) > int(s["confirm_window"]):
            pending = None
        if bull_q:
            pending = {"dir": 1, "bar": i, "level": bull_level, "wick": lows[i], "score": bull_score, "parts": bull_parts}
        if bear_q:
            pending = {"dir": -1, "bar": i, "level": bear_level, "wick": highs[i], "score": bear_score, "parts": bear_parts}

        fire = None
        if bool(s["require_choch"]):
            if pending and pending["dir"] == 1 and last_minor_high > 0 and closes[i] > last_minor_high:
                fire = dict(pending, choch_level=last_minor_high, signal_bar=i)
                pending = None
            elif pending and pending["dir"] == -1 and last_minor_low > 0 and closes[i] < last_minor_low:
                fire = dict(pending, choch_level=last_minor_low, signal_bar=i)
                pending = None
        else:
            if bull_q:
                fire = {"dir": 1, "bar": i, "level": bull_level, "wick": lows[i], "score": bull_score, "parts": bull_parts, "choch_level": 0.0, "signal_bar": i}
            elif bear_q:
                fire = {"dir": -1, "bar": i, "level": bear_level, "wick": highs[i], "score": bear_score, "parts": bear_parts, "choch_level": 0.0, "signal_bar": i}

        if i == len(candles) - 1:
            sr_near = float(s["sr_near_atr"]) * max(atr_values[i], 0.000001)
            bull_sr = (not s["use_sr_filter"]) or (sr["nearest_support"] > 0 and bull_level > 0 and abs(bull_level - sr["nearest_support"]) <= sr_near)
            bear_sr = (not s["use_sr_filter"]) or (sr["nearest_resistance"] > 0 and bear_level > 0 and abs(bear_level - sr["nearest_resistance"]) <= sr_near)
            bull_gk = (not s["use_gk_filter"]) or gk["trend"] == 1
            bear_gk = (not s["use_gk_filter"]) or gk["trend"] == -1
            last_meta = {
                "reason": "LIQUIDITY_ENTRY_CHECK_FAILED",
                "bull_score": bull_score,
                "bear_score": bear_score,
                "max_score": 100.0,
                "bull_checks": {
                    "sweep_reclaim": bull_level > 0,
                    "score": bull_q,
                    "choch": bool(fire and fire["dir"] == 1) if s["require_choch"] else bull_q,
                    "gk_trend": bull_gk,
                    "sr_context": bull_sr,
                },
                "bear_checks": {
                    "sweep_reclaim": bear_level > 0,
                    "score": bear_q,
                    "choch": bool(fire and fire["dir"] == -1) if s["require_choch"] else bear_q,
                    "gk_trend": bear_gk,
                    "sr_context": bear_sr,
                },
                "indicators": {
                    "bull_sweep_level": bull_level,
                    "bear_sweep_level": bear_level,
                    "bull_score": bull_score,
                    "bear_score": bear_score,
                    "atr": atr_values[i],
                    "volume_sma": vol_sma[i],
                    "volume_ratio": (volumes[i] / vol_sma[i]) if vol_sma[i] > 0 else 1.0,
                    "htf_ema": htf_ema[i],
                    "last_minor_high": last_minor_high,
                    "last_minor_low": last_minor_low,
                    "gk_trend": gk["trend"],
                    "gk_zl": gk["zl"],
                    "gk_upper": gk["up"],
                    "gk_lower": gk["dn"],
                    **sr,
                },
            }

    if not fire or int(fire.get("signal_bar", -1)) != len(candles) - 1:
        return None, last_meta or {"reason": "LIQUIDITY_ENTRY_CHECK_FAILED"}

    direction = int(fire["dir"])
    gk_ok = (not s["use_gk_filter"]) or (gk["trend"] == direction)
    sr_near = float(s["sr_near_atr"]) * max(atr_values[-1], 0.000001)
    sr_ok = True
    if s["use_sr_filter"]:
        sr_ok = (
            sr["nearest_support"] > 0 and abs(float(fire["level"]) - sr["nearest_support"]) <= sr_near
            if direction == 1
            else sr["nearest_resistance"] > 0 and abs(float(fire["level"]) - sr["nearest_resistance"]) <= sr_near
        )
    if not gk_ok or not sr_ok:
        last_meta["reason"] = "LIQUIDITY_FILTER_FAILED"
        return None, last_meta

    close = closes[-1]
    atr = atr_values[-1]
    side = "BUY" if direction == 1 else "SELL"
    stop = float(fire["wick"]) - atr * float(s["sl_buffer_atr"]) if direction == 1 else float(fire["wick"]) + atr * float(s["sl_buffer_atr"])
    risk = abs(close - stop)
    if risk <= 0:
        stop = close - atr * 0.5 if direction == 1 else close + atr * 0.5
        risk = abs(close - stop)
    stamp = candles[-1].get("date")
    candle_time = stamp.isoformat() if isinstance(stamp, datetime) else str(stamp or "")
    sign = 1.0 if direction == 1 else -1.0
    signal = StrategySignal(
        side=side,
        signal_price=close,
        stop_loss=stop,
        tp1=close + sign * risk * float(s["tp1_mult"]),
        tp2=close + sign * risk * float(s["tp2_mult"]),
        tp3=close + sign * risk * float(s["tp3_mult"]),
        trail_price=stop,
        score=float(fire["score"]),
        max_score=100.0,
        grade=_grade(float(fire["score"]), 100.0),
        preset=str(s["preset"]),
        volatility="Normal",
        candle_time=candle_time,
    )
    return signal, {**last_meta, "reason": "CUSTOM_SIGNAL_OK", **signal.to_dict()}


def pure_liquidity_required_candles(cfg: Dict[str, Any]) -> int:
    s = resolve_pure_liquidity_sweep_settings(cfg)
    return max(
        int(s["swing_len"]) * 2,
        int(s["minor_len"]) * 2 + int(s["confirm_window"]) + 5,
        int(s["vol_len"]) + int(s["atr_len"]) + 5,
        int(s["htf_ema_len"]) + 5 if s["use_htf_bias"] else 0,
        50,
    )


def evaluate_pure_liquidity_sweep_strategy(
    candles: Sequence[Dict[str, Any]],
    cfg: Dict[str, Any],
) -> tuple[Optional[StrategySignal], Dict[str, Any]]:
    s = resolve_pure_liquidity_sweep_settings(cfg)
    minimum = pure_liquidity_required_candles(cfg)
    if len(candles) < minimum:
        return None, {"reason": "PURE_LIQUIDITY_NOT_ENOUGH_CANDLES", "required": minimum, "received": len(candles)}

    highs = [_num(x.get("high"), 0) for x in candles]
    lows = [_num(x.get("low"), 0) for x in candles]
    opens = [_num(x.get("open"), 0) for x in candles]
    closes = [_num(x.get("close"), 0) for x in candles]
    volumes = [_num(x.get("volume"), 0) for x in candles]
    atr_values = _rma(_true_ranges(candles), int(s["atr_len"]))
    vol_sma = [_sma_last(volumes[: i + 1], int(s["vol_len"])) for i in range(len(volumes))]
    htf_ema = _ema(closes, int(s["htf_ema_len"]))
    swing_highs = _pivot_highs(highs, int(s["swing_len"]))
    swing_lows = _pivot_lows(lows, int(s["swing_len"]))
    minor_highs = _pivot_highs(highs, int(s["minor_len"]))
    minor_lows = _pivot_lows(lows, int(s["minor_len"]))

    mode = str(s["sweep_mode"])
    allow_wicks = mode in {"Only Wicks", "Wicks + Outbreaks & Retest"}
    allow_retests = mode in {"Only Outbreaks & Retest", "Wicks + Outbreaks & Retest"}
    lookback = int(s["lookback_bars"])
    used_highs: set[int] = set()
    used_lows: set[int] = set()
    broken_highs: set[int] = set()
    broken_lows: set[int] = set()
    pending: Optional[Dict[str, Any]] = None
    fire: Optional[Dict[str, Any]] = None
    last_meta: Dict[str, Any] = {}

    def score_at(i: int, direction: int, level: float) -> tuple[float, Dict[str, float]]:
        atr = max(atr_values[i], 0.000001)
        rng = max(highs[i] - lows[i], 0.000001)
        wick = min(opens[i], closes[i]) - lows[i] if direction == 1 else highs[i] - max(opens[i], closes[i])
        reclaim = closes[i] - level if direction == 1 else level - closes[i]
        close_pos = (closes[i] - lows[i]) / rng
        cp_comp = close_pos if direction == 1 else 1.0 - close_pos
        wick_comp = min(max(wick, 0.0) / atr, 1.0)
        reclaim_comp = min(max(reclaim, 0.0) / atr, 1.0)
        vol_ratio = volumes[i] / vol_sma[i] if vol_sma[i] > 0 else 1.0
        if s["use_volume"]:
            vol_comp = min(max((vol_ratio - 1.0) / max(float(s["vol_mult"]) - 1.0, 0.1), 0.0), 1.0)
        else:
            vol_comp = 0.5
        htf_ok = closes[i] > htf_ema[i] if direction == 1 else closes[i] < htf_ema[i]
        htf_comp = 0.5 if not s["use_htf_bias"] else (1.0 if htf_ok else 0.0)
        score = (wick_comp * 0.30 + reclaim_comp * 0.25 + cp_comp * 0.20 + vol_comp * 0.15 + htf_comp * 0.10) * 100.0
        return max(0.0, min(100.0, score)), {
            "wick_comp": wick_comp,
            "reclaim_comp": reclaim_comp,
            "close_position_comp": cp_comp,
            "volume_ratio": vol_ratio,
            "volume_comp": vol_comp,
            "htf_comp": htf_comp,
        }

    for i in range(minimum - 1, len(candles)):
        last_minor_high = next((price for idx, price in reversed(minor_highs) if idx <= i - int(s["minor_len"])), 0.0)
        last_minor_low = next((price for idx, price in reversed(minor_lows) if idx <= i - int(s["minor_len"])), 0.0)
        bull_event: Optional[Dict[str, Any]] = None
        bear_event: Optional[Dict[str, Any]] = None

        for idx, level in reversed(swing_lows):
            if idx in used_lows or i - idx > lookback or idx > i - int(s["swing_len"]):
                continue
            if closes[i] < level:
                broken_lows.add(idx)
                if not allow_retests:
                    used_lows.add(idx)
                continue
            if allow_wicks and lows[i] < level < closes[i]:
                used_lows.add(idx)
                bull_event = {"dir": 1, "bar": i, "level": level, "wick": lows[i], "kind": "wick_sweep", "origin_bar": idx}
                break
            if allow_retests and idx in broken_lows and highs[i] > level > closes[i]:
                used_lows.add(idx)
                bear_event = {"dir": -1, "bar": i, "level": level, "wick": highs[i], "kind": "outbreak_retest", "origin_bar": idx}
                break

        for idx, level in reversed(swing_highs):
            if idx in used_highs or i - idx > lookback or idx > i - int(s["swing_len"]):
                continue
            if closes[i] > level:
                broken_highs.add(idx)
                if not allow_retests:
                    used_highs.add(idx)
                continue
            if allow_wicks and highs[i] > level > closes[i]:
                used_highs.add(idx)
                bear_event = {"dir": -1, "bar": i, "level": level, "wick": highs[i], "kind": "wick_sweep", "origin_bar": idx}
                break
            if allow_retests and idx in broken_highs and lows[i] < level < closes[i]:
                used_highs.add(idx)
                bull_event = {"dir": 1, "bar": i, "level": level, "wick": lows[i], "kind": "outbreak_retest", "origin_bar": idx}
                break

        bull_score, bull_parts = score_at(i, 1, float(bull_event["level"])) if bull_event else (0.0, {})
        bear_score, bear_parts = score_at(i, -1, float(bear_event["level"])) if bear_event else (0.0, {})
        bull_q = bool(bull_event and bull_score >= float(s["min_score"]))
        bear_q = bool(bear_event and bear_score >= float(s["min_score"]))

        if pending and i - int(pending["bar"]) > int(s["confirm_window"]):
            pending = None
        if bull_q and not bear_q:
            pending = {**bull_event, "score": bull_score, "parts": bull_parts}  # type: ignore[arg-type]
        if bear_q and not bull_q:
            pending = {**bear_event, "score": bear_score, "parts": bear_parts}  # type: ignore[arg-type]

        fire = None
        if bool(s["require_choch"]):
            if pending and pending["dir"] == 1 and last_minor_high > 0 and closes[i] > last_minor_high:
                fire = {**pending, "choch_level": last_minor_high, "signal_bar": i}
                pending = None
            elif pending and pending["dir"] == -1 and last_minor_low > 0 and closes[i] < last_minor_low:
                fire = {**pending, "choch_level": last_minor_low, "signal_bar": i}
                pending = None
        else:
            if bull_q and not bear_q:
                fire = {**bull_event, "score": bull_score, "parts": bull_parts, "choch_level": 0.0, "signal_bar": i}  # type: ignore[arg-type]
            elif bear_q and not bull_q:
                fire = {**bear_event, "score": bear_score, "parts": bear_parts, "choch_level": 0.0, "signal_bar": i}  # type: ignore[arg-type]

        if i == len(candles) - 1:
            bull_checks = {
                "sweep_or_retest": bool(bull_event),
                "score": bull_q,
                "choch": bool(fire and fire["dir"] == 1) if s["require_choch"] else bull_q,
                "volume": (not s["use_volume"]) or (bull_parts.get("volume_ratio", 0.0) >= float(s["vol_mult"]) if bull_parts else False),
                "htf_bias": (not s["use_htf_bias"]) or closes[i] > htf_ema[i],
            }
            bear_checks = {
                "sweep_or_retest": bool(bear_event),
                "score": bear_q,
                "choch": bool(fire and fire["dir"] == -1) if s["require_choch"] else bear_q,
                "volume": (not s["use_volume"]) or (bear_parts.get("volume_ratio", 0.0) >= float(s["vol_mult"]) if bear_parts else False),
                "htf_bias": (not s["use_htf_bias"]) or closes[i] < htf_ema[i],
            }
            last_meta = {
                "reason": "PURE_LIQUIDITY_ENTRY_CHECK_FAILED",
                "bull_score": bull_score,
                "bear_score": bear_score,
                "max_score": 100.0,
                "bull_checks": bull_checks,
                "bear_checks": bear_checks,
                "indicators": {
                    "pure_bull_level": bull_event.get("level", 0.0) if bull_event else 0.0,
                    "pure_bear_level": bear_event.get("level", 0.0) if bear_event else 0.0,
                    "pure_bull_kind": bull_event.get("kind", "") if bull_event else "",
                    "pure_bear_kind": bear_event.get("kind", "") if bear_event else "",
                    "pure_bull_score": bull_score,
                    "pure_bear_score": bear_score,
                    "atr": atr_values[i],
                    "volume_sma": vol_sma[i],
                    "volume_ratio": volumes[i] / vol_sma[i] if vol_sma[i] > 0 else 1.0,
                    "htf_ema": htf_ema[i],
                    "last_minor_high": last_minor_high,
                    "last_minor_low": last_minor_low,
                    "pending_dir": pending.get("dir", 0) if pending else 0,
                    "pending_level": pending.get("level", 0.0) if pending else 0.0,
                    "sweep_mode": mode,
                    **{f"bull_{k}": v for k, v in bull_parts.items()},
                    **{f"bear_{k}": v for k, v in bear_parts.items()},
                },
            }

    if not fire or int(fire.get("signal_bar", -1)) != len(candles) - 1:
        return None, last_meta or {"reason": "PURE_LIQUIDITY_ENTRY_CHECK_FAILED"}

    direction = int(fire["dir"])
    close = closes[-1]
    atr = atr_values[-1]
    side = "BUY" if direction == 1 else "SELL"
    stop = float(fire["wick"]) - atr * float(s["sl_buffer_atr"]) if direction == 1 else float(fire["wick"]) + atr * float(s["sl_buffer_atr"])
    risk = abs(close - stop)
    if risk <= 0:
        stop = close - atr * 0.5 if direction == 1 else close + atr * 0.5
        risk = abs(close - stop)
    stamp = candles[-1].get("date")
    candle_time = stamp.isoformat() if isinstance(stamp, datetime) else str(stamp or "")
    sign = 1.0 if direction == 1 else -1.0
    signal = StrategySignal(
        side=side,
        signal_price=close,
        stop_loss=stop,
        tp1=close + sign * risk * float(s["tp1_mult"]),
        tp2=close + sign * risk * float(s["tp2_mult"]),
        tp3=close + sign * risk * float(s["tp3_mult"]),
        trail_price=stop,
        score=float(fire["score"]),
        max_score=100.0,
        grade=_grade(float(fire["score"]), 100.0),
        preset=str(s["preset"]),
        volatility=str(fire.get("kind") or "Sweep"),
        candle_time=candle_time,
    )
    return signal, {
        **last_meta,
        "reason": "CUSTOM_SIGNAL_OK",
        "side": side,
        "score": float(fire["score"]),
        "max_score": 100.0,
        "sweep_level": float(fire["level"]),
        "sweep_kind": str(fire.get("kind") or ""),
        "choch_level": float(fire.get("choch_level") or 0.0),
        **signal.to_dict(),
    }
