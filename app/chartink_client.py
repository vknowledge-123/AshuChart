# app/chartink_client.py
from __future__ import annotations

from typing import Any, Dict, List, Tuple, Optional
from datetime import datetime
import json
import re
import html

try:
    import pytz  # type: ignore
except ImportError:
    pytz = None

from .redis_store import norm_symbol as _norm_symbol, norm_alert_name as _norm_alert_name

# ============================================================
# Normalizers (MUST match RedisStore + TradeEngine usage)
# ============================================================
# - alert keys: normalized lowercase, "_" and "-" -> space, collapse spaces
# - symbols: normalized Zerodha/NSE EQ style like "SBIN", "M&M", "BAJAJ-AUTO"
#   (remove exchange prefixes like "NSE:", remove "-EQ", remove ".NS")
#   keep only letters/digits/-/&
# ============================================================

_ZERO_WIDTH = re.compile(r"[\u200B-\u200D\uFEFF]")
_WS = re.compile(r"\s+")

def _now_ist_str() -> str:
    """
    Return current IST time string in stable format.
    """
    if pytz is None:
        # local time fallback
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S").replace(" ", "T")
    ist = pytz.timezone("Asia/Kolkata")
    return datetime.now(ist).strftime("%Y-%m-%d %H:%M:%S").replace(" ", "T")

def normalize_alert_name(name: Any) -> str:
    return _norm_alert_name(str(name))


def _strip_exchange_prefix(s: str) -> str:
    # "NSE:SBIN" -> "SBIN"
    # "BSE:SBIN" -> "SBIN"
    if ":" in s:
        s = s.split(":", 1)[1].strip()
    return s


def _strip_common_suffixes(s: str) -> str:
    # chartink / other feeds may send these
    # "SBIN-EQ" -> "SBIN"
    # "SBIN.NS" -> "SBIN"
    if s.endswith(".NS"):
        s = s[:-3]
    if s.endswith("-EQ"):
        s = s[:-3]
    return s


def normalize_symbol(sym: Any) -> str:
    """
    Normalize symbols coming from Chartink / UI:
    Uses redis_store.norm_symbol as base, plus legacy char whitelist for safety.
    """
    s = _norm_symbol(str(sym))
    if not s:
        return ""

    # Keep only allowed chars for Zerodha tradingsymbol (Legacy safety)
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-& ")
    s = "".join(ch for ch in s if ch in allowed).strip()
    
    if s in {"NSE", "BSE"}:
        return ""
    return s


def normalize_symbols(symbols: Any) -> List[str]:
    """
    Accepts:
    - list/tuple/set
    - comma-separated string
    - JSON list string: '["SBIN","TCS"]'
    - python-like list string: "['SBIN','TCS']"
    - also handles newlines
    Dedupe preserving order.
    """
    if not symbols:
        return []

    raw_items: List[Any] = []

    if isinstance(symbols, str):
        raw_items = _as_list(symbols)
    elif isinstance(symbols, (list, tuple, set)):
        for item in list(symbols):
            raw_items.extend(_as_list(item))
    else:
        raw_items = [symbols]

    out: List[str] = []
    seen = set()

    for item in raw_items:
        if item is None:
            continue
        # each item might contain comma/newline separated values
        for part in str(item).replace("\n", ",").split(","):
            n = normalize_symbol(part)
            if n and n not in seen:
                seen.add(n)
                out.append(n)

    return out


# ============================================================
# Parsing helpers
# ============================================================

def _try_json(x: str) -> Optional[Any]:
    try:
        return json.loads(x)
    except Exception:
        return None


def _as_list(x: Any) -> List[Any]:
    """
    Convert many representations into list:
    - list/tuple/set -> list
    - JSON list string: '["SBIN","TCS"]'
    - python-like list string: "['SBIN','TCS']"
    - CSV string: "SBIN,TCS"
    - scalar -> [scalar]
    """
    if x is None:
        return []

    if isinstance(x, list):
        return x
    if isinstance(x, (tuple, set)):
        return list(x)
    if isinstance(x, dict):
        # Some integrations send symbols as an object like {"0":"SBIN","1":"TCS"}.
        # Treat dict values as the list items.
        return list(x.values())

    if isinstance(x, str):
        s = _ZERO_WIDTH.sub("", x).strip()
        if not s:
            return []

        # JSON-like list
        if s.startswith("[") and s.endswith("]"):
            j = _try_json(s)
            if isinstance(j, list):
                return j

            # python list string fallback: "['SBIN','TCS']"
            s2 = s.replace("'", '"')
            j2 = _try_json(s2)
            if isinstance(j2, list):
                return j2

        # CSV fallback
        return [p.strip() for p in s.replace("\n", ",").split(",") if p.strip()]

    # fallback scalar
    return [x]


def _first_present(payload: Dict[str, Any], keys: Tuple[str, ...]) -> Any:
    for k in keys:
        v = payload.get(k)
        if v not in (None, ""):
            return v
    return None


def _extract_indexed_stocks(payload: Dict[str, Any]) -> List[Any]:
    """
    Some forms send stocks[0], stocks[1]...
    """
    parts: List[Any] = []
    for k, v in payload.items():
        if not isinstance(k, str):
            continue
        kl = k.lower()
        if kl.startswith("stocks[") or kl.startswith("symbols["):
            if v not in (None, ""):
                parts.append(v)
    return parts


# ============================================================
# Payload parsing
# ============================================================

def parse_chartink_payload(payload: Dict[str, Any]) -> Tuple[str, List[str], str]:
    """
    Returns: (alert_name_normalized, symbols_normalized, time_str)

    Handles JSON + form-encoded variations:

    Alert name keys (common):
      - scan_name / trigger_name / scan / alert / alert_name / name

    Symbol keys (common):
      - stocks / stocks[] / symbols / symbol / stock / tradingsymbol
      - also supports "stocks[0]", "stocks[1]"... (form style)
      - values can be list, CSV string, JSON-string list, python-like list string

    IMPORTANT:
      - alert name is normalized to lower-case key (matches RedisStore normalization)
      - symbols are normalized to Zerodha-style tradingsymbol keys (upper, strip exchange prefix/suffix)
    """
    if payload is None:
        payload = {}

    # Make key lookup resilient to casing differences from webhook providers
    payload_ci: Dict[str, Any] = {}
    try:
        for k, v in (payload or {}).items():
            if isinstance(k, str):
                payload_ci[k.lower()] = v
    except Exception:
        payload_ci = payload or {}

    # -------- alert name --------
    raw_alert = _first_present(
        payload_ci,
        ("scan_name", "trigger_name", "scan", "alert", "alert_name", "name"),
    )
    alert_name = normalize_alert_name(raw_alert or "UNKNOWN_ALERT")

    # -------- timestamp --------
    # Force Server IST time to ensure dashboard shows correct relative time
    ts = _now_ist_str()

    # -------- symbols extraction --------
    raw_symbols = _first_present(payload_ci, ("stocks", "symbols", "stocks[]", "symbol", "stock", "tradingsymbol"))

    # Indexed form fallback
    if raw_symbols is None:
        indexed = _extract_indexed_stocks(payload_ci)
        if indexed:
            raw_symbols = indexed

    if raw_symbols is None:
        return alert_name, [], ts

    # Handle dict/object style symbols payloads
    if isinstance(raw_symbols, dict):
        raw_symbols = list(raw_symbols.values())

    # Convert raw -> list and normalize
    items = _as_list(raw_symbols)

    syms: List[str] = []
    for item in items:
        # item can still have commas/newlines
        for part in str(item).replace("\n", ",").split(","):
            # Unescape HTML entities (e.g. M&amp;M -> M&M)
            part = html.unescape(part)
            n = normalize_symbol(part)
            if n:
                syms.append(n)

    # dedupe preserving order
    out: List[str] = []
    seen = set()
    for s in syms:
        if s not in seen:
            seen.add(s)
            out.append(s)

    return alert_name, out, ts
