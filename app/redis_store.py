# app/redis_store.py
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

import redis.asyncio as redis

if TYPE_CHECKING:
    from app.crypto import EncryptionManager

try:
    import pytz  # type: ignore
except Exception:
    pytz = None


# =========================
# Regex / Normalizers
# =========================
_ZERO_WIDTH = re.compile(r"[\u200B-\u200D\uFEFF]")
_WS = re.compile(r"\s+")


def now_ist() -> datetime:
    """Aware IST datetime when pytz is available, else local naive datetime."""
    if pytz is None:
        return datetime.now()
    return datetime.now(pytz.timezone("Asia/Kolkata"))


def now_ist_date() -> str:
    """Daily key (IST): YYYYMMDD"""
    return now_ist().strftime("%Y%m%d")


def seconds_until_next_ist_day(extra_grace_sec: int = 6 * 60 * 60) -> int:
    """TTL for per-day keys: expire after next midnight IST (+ grace)."""
    t = now_ist()
    next_midnight = (t.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1))
    delta = int((next_midnight - t).total_seconds())
    return max(60, delta + int(extra_grace_sec))


def norm_symbol(s: str) -> str:
    """
    Normalize symbols coming from alerts / UI / instruments:
      - "NSE:SBIN"    -> "SBIN"
      - "SBIN-EQ"     -> "SBIN"
      - "nse:infy-eq" -> "INFY"
      - "NIFTY BANK"  -> "NIFTY BANK" (Indices kept as is, but stripped)
    """
    x = (s or "").strip().upper()
    x = _ZERO_WIDTH.sub("", x).strip()

    # Remove exchange prefix like "NSE:" / "BSE:" / "NFO:"
    if ":" in x:
        x = x.split(":", 1)[1].strip()

    # Remove common suffixes
    if x.endswith("-EQ"):
        x = x[:-3].strip()

    # Collapse internal whitespace
    x = _WS.sub(" ", x).strip()
    return x


def norm_alert_name(s: str) -> str:
    """
    Normalize alert names for consistent Redis keys:
      - remove zero-width chars
      - lowercase
      - "_" / "-" -> space
      - collapse whitespace
    """
    x = "" if s is None else str(s)
    x = _ZERO_WIDTH.sub("", x).strip().lower()
    x = x.replace("_", " ").replace("-", " ")
    x = _WS.sub(" ", x).strip()
    return x


# Backward-compatible alias
def normalize_alert_name(s: str) -> str:
    return norm_alert_name(s)


# =========================
# Redis keys
# =========================
def k_creds(user_id: int) -> str:
    return f"kite:creds:{int(user_id)}"


def k_access(user_id: int) -> str:
    return f"kite:access:{int(user_id)}"


def k_broker(user_id: int) -> str:
    return f"broker:selected:{int(user_id)}"


def k_dhan_creds(user_id: int) -> str:
    return f"dhan:creds:{int(user_id)}"


def k_creds_pattern() -> str:
    return "kite:creds:*"


def k_kill(user_id: int) -> str:
    return f"kill:{int(user_id)}"


def k_alert_cfg(user_id: int) -> str:
    return f"cfg:alerts:{int(user_id)}"


def k_alert_cfg_legacy(user_id: int) -> str:
    return f"u:{int(user_id)}:alert_cfg"


def k_positions(user_id: int) -> str:
    return f"positions:{int(user_id)}"


def k_trade_open(user_id: int, symbol: str) -> str:
    return f"trade:open:{int(user_id)}:{norm_symbol(symbol)}"


def k_lock(user_id: int, symbol: str, action: str) -> str:
    return f"lock:{int(user_id)}:{norm_symbol(symbol)}:{(action or '').strip().lower()}"


def k_trade_count_alert(user_id: int, ymd: str, alert_name: str) -> str:
    return f"trade:count:{int(user_id)}:{ymd}:{norm_alert_name(alert_name)}"


def k_symbol_token(symbol: str) -> str:
    return f"symbol_token:{norm_symbol(symbol)}"


def k_alerts(user_id: int) -> str:
    return f"alerts:{int(user_id)}"


def k_auto_sq_off_config(user_id: int) -> str:
    return f"config:auto_sq_off:{int(user_id)}"


def k_auto_sq_off_ran(user_id: int, ymd: str) -> str:
    return f"status:auto_sq_off_ran:{int(user_id)}:{ymd}"


def k_pnl_exit_config(user_id: int) -> str:
    return f"config:pnl_exit:{int(user_id)}"


# =========================
# Lua scripts
# =========================
LUA_LOCK = r"""
-- KEYS[1] = lock_key
-- KEYS[2] = kill_key
-- ARGV[1] = ttl_ms
-- ARGV[2] = now_ms
-- ARGV[3] = action (e.g. "entry", "exit")
local action = tostring(ARGV[3] or "")
if action ~= "exit" and redis.call('EXISTS', KEYS[2]) == 1 then
  return -2
end
if redis.call('EXISTS', KEYS[1]) == 1 then
  return 0
end
redis.call('PSETEX', KEYS[1], ARGV[1], ARGV[2])
return 1
"""

LUA_TRADE_LIMIT = r"""
-- KEYS[1] = count_key
-- ARGV[1] = limit
-- ARGV[2] = ttl_sec
local limit = tonumber(ARGV[1])
if limit <= 0 then
  return 1
end
local cur = tonumber(redis.call('GET', KEYS[1]) or "0")
if cur >= limit then
  return 0
end
cur = redis.call('INCR', KEYS[1])
if cur == 1 then
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
end
return 1
"""


# =========================
# Store
# =========================
class RedisStore:
    """
    What this store provides:
    - Credentials (SET JSON): kite:creds:{user_id}
    - Access token (SET):     kite:access:{user_id}
    - Kill switch (SETEX):    kill:{user_id}
    - Alert configs (HASH):   cfg:alerts:{user_id}   field=normalized_alert_name
      plus legacy mirror key: u:{user_id}:alert_cfg
    - Positions snapshot (HASH): positions:{user_id} field=symbol
    - Open trade guard (SETEX): trade:open:{user}:{symbol}
    - Locks (Lua): lock:{user}:{symbol}:{action}
    - Per day trade limit (Lua): trade:count:{user}:{ymd}:{alert}:{symbol}
    - Symbol token cache (SET): symbol_token:{symbol}
    - Alerts history (LIST): alerts:{user_id}
    """

    def __init__(self, redis_url: str, encryption_manager: Optional['EncryptionManager'] = None) -> None:
        self.redis = redis.from_url(redis_url, decode_responses=True)
        self._sha_lock: Optional[str] = None
        self._sha_limit: Optional[str] = None
        self.encryption = encryption_manager

    async def close(self) -> None:
        try:
            await self.redis.close()
        except Exception:
            pass

    async def ping(self) -> bool:
        try:
            return bool(await self.redis.ping())
        except Exception:
            return False

    async def init_scripts(self) -> None:
        if not self._sha_lock:
            self._sha_lock = await self.redis.script_load(LUA_LOCK)
        if not self._sha_limit:
            self._sha_limit = await self.redis.script_load(LUA_TRADE_LIMIT)

    # =========================
    # Lock + trade limit
    # =========================
    async def acquire_lock(self, user_id: int, symbol: str, action: str, ttl_ms: int = 1200) -> int:
        """
        Return:
          1 acquired
          0 busy
          -2 kill switch active
        """
        await self.init_scripts()
        now_ms = int(time.time() * 1000)
        return int(
            await self.redis.evalsha(
                self._sha_lock,  # type: ignore[arg-type]
                2,
                k_lock(user_id, symbol, action),
                k_kill(user_id),
                str(int(ttl_ms)),
                str(int(now_ms)),
                str((action or "").strip().lower()),
            )
        )

    async def release_lock(self, user_id: int, symbol: str, action: str) -> None:
        try:
            await self.redis.delete(k_lock(user_id, symbol, action))
        except Exception:
            pass

    async def allow_trade(self, user_id: int, alert_name: str, limit: int) -> bool:
        """
        Per user + per day + per alert trade limit (GLOBAL for that alert).
        limit <= 0 => allow always.
        """
        await self.init_scripts()
        ymd = now_ist_date()
        ttl = seconds_until_next_ist_day(extra_grace_sec=6 * 60 * 60)
        res = int(
            await self.redis.evalsha(
                self._sha_limit,  # type: ignore[arg-type]
                1,
                k_trade_count_alert(user_id, ymd, alert_name),
                str(int(limit)),
                str(int(ttl)),
            )
        )
        return res == 1

    # =========================
    # Kill switch
    # =========================
    async def is_kill(self, user_id: int) -> bool:
        try:
            return bool(await self.redis.get(k_kill(user_id)))
        except Exception:
            return False

    async def set_kill(self, user_id: int, enabled: bool) -> None:
        if enabled:
            ttl = seconds_until_next_ist_day(extra_grace_sec=6 * 60 * 60)
            await self.redis.setex(k_kill(user_id), int(ttl), "1")
        else:
            await self.redis.delete(k_kill(user_id))

    # =========================
    # P&L exit config (MTM-based)
    # =========================
    async def get_pnl_exit_config(self, user_id: int) -> Dict[str, Any]:
        """
        User-level risk guard:
        - enabled: bool
        - max_profit: float (₹)
        - max_loss: float (₹, positive number)
        """
        try:
            raw = await self.redis.get(k_pnl_exit_config(user_id))
            if not raw:
                return {"enabled": False, "max_profit": 0.0, "max_loss": 0.0}
            d = json.loads(raw)
            return {
                "enabled": bool(d.get("enabled", False)),
                "max_profit": float(d.get("max_profit", 0.0) or 0.0),
                "max_loss": float(d.get("max_loss", 0.0) or 0.0),
            }
        except Exception:
            return {"enabled": False, "max_profit": 0.0, "max_loss": 0.0}

    async def set_pnl_exit_config(self, user_id: int, cfg: Dict[str, Any]) -> Dict[str, Any]:
        enabled = bool(cfg.get("enabled", False))
        max_profit = float(cfg.get("max_profit", 0.0) or 0.0)
        max_loss = float(cfg.get("max_loss", 0.0) or 0.0)
        if max_profit < 0:
            max_profit = abs(max_profit)
        if max_loss < 0:
            max_loss = abs(max_loss)
        payload = {"enabled": enabled, "max_profit": max_profit, "max_loss": max_loss}
        await self.redis.set(k_pnl_exit_config(user_id), json.dumps(payload))
        return payload

    # =========================
    # Credentials (SET JSON) - Encrypted
    # =========================
    async def save_credentials(self, user_id: int, api_key: str, api_secret: str) -> None:
        """Save credentials with encryption if enabled."""
        api_key = (api_key or "").strip()
        api_secret = (api_secret or "").strip()
        
        # Encrypt credentials if encryption manager is available
        if self.encryption and self.encryption.is_enabled():
            api_key, api_secret = self.encryption.encrypt_credentials(api_key, api_secret)
        
        payload = json.dumps({"api_key": api_key, "api_secret": api_secret})
        await self.redis.set(k_creds(user_id), payload)

    async def get_credentials(self, user_id: int) -> Tuple[Optional[str], Optional[str]]:
        """Get credentials with decryption if enabled."""
        raw = await self.redis.get(k_creds(user_id))
        if not raw:
            return None, None
        try:
            d = json.loads(raw)
            api_key = d.get("api_key") or None
            api_secret = d.get("api_secret") or None
            
            # Decrypt credentials if encryption manager is available
            if self.encryption and self.encryption.is_enabled() and api_key and api_secret:
                api_key, api_secret = self.encryption.decrypt_credentials(api_key, api_secret)
            
            return api_key, api_secret
        except Exception as e:
            print(f"Error loading credentials: {e}")
            return None, None

    async def load_credentials(self, user_id: int) -> Dict[str, str]:
        api_key, api_secret = await self.get_credentials(user_id)
        return {"api_key": api_key or "", "api_secret": api_secret or ""}

    # =========================
    # Access token
    # =========================
    async def save_access_token(self, user_id: int, access_token: str) -> None:
        """Save access token with 24 hour expiration"""
        await self.redis.setex(k_access(user_id), 86400, (access_token or "").strip())

    async def load_access_token(self, user_id: int) -> str:
        return str(await self.redis.get(k_access(user_id)) or "")

    async def clear_access_token(self, user_id: int) -> None:
        await self.redis.delete(k_access(user_id))

    async def save_broker(self, user_id: int, broker: str) -> str:
        selected = str(broker or "ZERODHA").strip().upper()
        if selected not in {"ZERODHA", "DHAN"}:
            raise ValueError("UNSUPPORTED_BROKER")
        await self.redis.set(k_broker(user_id), selected)
        return selected

    async def load_broker(self, user_id: int) -> str:
        selected = str(await self.redis.get(k_broker(user_id)) or "ZERODHA").upper()
        return selected if selected in {"ZERODHA", "DHAN"} else "ZERODHA"

    async def save_dhan_credentials(
        self, user_id: int, client_id: str, access_token: str
    ) -> None:
        client_id = str(client_id or "").strip()
        access_token = str(access_token or "").strip()
        if self.encryption and self.encryption.is_enabled():
            client_id, access_token = self.encryption.encrypt_credentials(
                client_id, access_token
            )
        await self.redis.set(
            k_dhan_creds(user_id),
            json.dumps({"client_id": client_id, "access_token": access_token}),
        )

    async def load_dhan_credentials(self, user_id: int) -> Dict[str, str]:
        raw = await self.redis.get(k_dhan_creds(user_id))
        if not raw:
            return {"client_id": "", "access_token": ""}
        try:
            data = json.loads(raw)
            client_id = str(data.get("client_id") or "")
            access_token = str(data.get("access_token") or "")
            if self.encryption and self.encryption.is_enabled() and client_id and access_token:
                client_id, access_token = self.encryption.decrypt_credentials(
                    client_id, access_token
                )
            return {"client_id": client_id, "access_token": access_token}
        except Exception:
            return {"client_id": "", "access_token": ""}

    async def list_all_user_ids(self) -> List[int]:
        """Discover all user IDs who have credentials saved."""
        keys = list(await self.redis.keys(k_creds_pattern()))
        keys.extend(await self.redis.keys("dhan:creds:*"))
        keys.extend(await self.redis.keys("broker:selected:*"))
        ids = []
        for k in keys:
            try:
                # k is "kite:creds:123"
                parts = k.split(":")
                ids.append(int(parts[-1]))
            except (ValueError, IndexError):
                continue
        return list(set(ids))

    # =========================
    # Alert config (hash)
    # =========================
    async def set_alert_config(self, user_id: int, alert_name: str, cfg: Dict[str, Any]) -> str:
        alert_key = norm_alert_name(alert_name)

        cfg2 = dict(cfg or {})
        cfg2.setdefault("alert_name_raw", str(alert_name or "").strip())
        cfg2["alert_name"] = alert_key

        payload = json.dumps(cfg2)

        # NEW
        await self.redis.hset(k_alert_cfg(user_id), alert_key, payload)
        # LEGACY mirror (helps older UI/backends)
        await self.redis.hset(k_alert_cfg_legacy(user_id), alert_key, payload)

        return alert_key

    async def get_alert_config(self, user_id: int, alert_name: str) -> Optional[Dict[str, Any]]:
        alert_key = norm_alert_name(alert_name)

        raw = await self.redis.hget(k_alert_cfg(user_id), alert_key)
        if not raw:
            raw = await self.redis.hget(k_alert_cfg_legacy(user_id), alert_key)

        if not raw:
            return None

        try:
            return json.loads(raw)
        except Exception:
            return None

    async def list_alert_configs(self, user_id: int) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}

        all_new = await self.redis.hgetall(k_alert_cfg(user_id))
        all_old = await self.redis.hgetall(k_alert_cfg_legacy(user_id))

        merged: Dict[str, str] = {}
        merged.update(all_old or {})
        merged.update(all_new or {})  # NEW wins

        for k, v in (merged or {}).items():
            try:
                out[k] = json.loads(v)
            except Exception:
                continue

        return out

    async def save_alert_config(self, user_id: int, cfg: Dict[str, Any]) -> None:
        key = normalize_alert_name(cfg.get("alert_name", ""))
        if not key:
            return
        # ensure enabled is bool
        cfg["enabled"] = bool(cfg.get("enabled", True))
        data = json.dumps(cfg)
        await self.redis.hset(k_alert_cfg(user_id), key, data)

    async def delete_alert_config(self, user_id: int, alert_name: str) -> bool:
        key = normalize_alert_name(alert_name)
        if not key:
            return False
        count_new = await self.redis.hdel(k_alert_cfg(user_id), key)
        count_legacy = await self.redis.hdel(k_alert_cfg_legacy(user_id), key)
        return (count_new + count_legacy) > 0

    # =========================
    # Positions snapshot (hash)
    # =========================

    async def upsert_position(self, user_id: int, symbol: str, pos: Dict[str, Any]) -> None:
        sym = norm_symbol(symbol)
        payload = dict(pos or {})
        payload["symbol"] = sym
        await self.redis.hset(k_positions(user_id), sym, json.dumps(payload))

    async def delete_position(self, user_id: int, symbol: str) -> None:
        sym = norm_symbol(symbol)
        await self.redis.hdel(k_positions(user_id), sym)

    async def list_positions(self, user_id: int) -> List[Dict[str, Any]]:
        rows = await self.redis.hgetall(k_positions(user_id))
        out: List[Dict[str, Any]] = []
        for _sym, raw in (rows or {}).items():
            try:
                out.append(json.loads(raw))
            except Exception:
                continue
        return out

    # =========================
    # Open-trade guard (string)
    # =========================
    async def mark_open(self, user_id: int, symbol: str, trade_id: str, ttl_sec: int = 60 * 60 * 8) -> None:
        await self.redis.setex(k_trade_open(user_id, symbol), int(ttl_sec), str(trade_id))

    async def get_open(self, user_id: int, symbol: str) -> str:
        return str(await self.redis.get(k_trade_open(user_id, symbol)) or "")

    async def clear_open(self, user_id: int, symbol: str) -> None:
        await self.redis.delete(k_trade_open(user_id, symbol))

    # =========================
    # Token cache (optional)
    # =========================
    async def set_symbol_token(self, symbol: str, token: int) -> None:
        await self.redis.set(k_symbol_token(symbol), str(int(token)))

    async def get_symbol_token(self, symbol: str) -> Optional[int]:
        v = await self.redis.get(k_symbol_token(symbol))
        if not v:
            return None
        try:
            return int(str(v))
        except Exception:
            return None

    # =========================
    # Alerts history (list)
    # =========================
    async def save_alert(self, user_id: int, alert_data: Dict[str, Any]) -> None:
        """
        Upsert alert in Redis list (by name and time).
        Keeps last 200 alerts, expire daily.
        """
        key = k_alerts(user_id)
        
        payload = dict(alert_data or {})
        payload.setdefault("type", "alert")
        # Ensure we use a stable format for time if not provided
        if not payload.get("time"):
            payload["time"] = now_ist().strftime("%Y-%m-%d %H:%M:%S").replace(" ", "T")

        # Try to find and update existing record first (prevent duplicates from re-pushes)
        raw_alerts = await self.redis.lrange(key, 0, -1)
        target_name = payload.get("alert_name")
        target_time = payload.get("time")
        
        updated = False
        new_list = []
        
        for raw in (raw_alerts or []):
            try:
                a = json.loads(raw)
                if a.get("alert_name") == target_name and a.get("time") == target_time:
                    # Update existing record with any new results or fields
                    a.update(payload)
                    updated = True
                new_list.append(json.dumps(a))
            except Exception:
                new_list.append(raw)
                
        if updated:
            await self.redis.delete(key)
            if new_list:
                await self.redis.rpush(key, *new_list)
        else:
            # New alert, push to front
            await self.redis.lpush(key, json.dumps(payload))
            await self.redis.ltrim(key, 0, 199)

        ttl = seconds_until_next_ist_day(extra_grace_sec=6 * 60 * 60)
        await self.redis.expire(key, int(ttl))

    async def get_recent_alerts(self, user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        key = k_alerts(user_id)
        raw_alerts = await self.redis.lrange(key, 0, max(0, int(limit) - 1))
        out: List[Dict[str, Any]] = []
        for raw in (raw_alerts or []):
            try:
                out.append(json.loads(raw))
            except Exception:
                continue
        return out

    async def delete_alerts(self, user_id: int) -> None:
        """Clear all alerts for a user"""
        key = k_alerts(user_id)
        await self.redis.delete(key)

    async def clear_daily_trading_state(self, user_id: int) -> Dict[str, int]:
        """Clear ephemeral trading state while preserving user configuration."""
        uid = int(user_id)
        patterns = [
            f"trade:open:{uid}:*",
            f"trade:count:{uid}:*",
            f"lock:{uid}:*",
            f"status:auto_sq_off_ran:{uid}:*",
        ]
        keys: List[str] = [k_positions(uid), k_alerts(uid), k_kill(uid)]
        scanned = 0
        for pattern in patterns:
            async for key in self.redis.scan_iter(match=pattern):
                keys.append(str(key))
                scanned += 1
        unique_keys = list(dict.fromkeys(keys))
        deleted = int(await self.redis.delete(*unique_keys)) if unique_keys else 0
        return {"deleted_keys": deleted, "scanned_keys": scanned}

    async def update_alert_status(self, user_id: int, alert_time: str, symbol: str, new_status: str, reason: str = "", alert_name: str = "") -> bool:
        """
        Find an alert in history by time (and optionally name) and update its result status for a specific symbol.
        """
        if not alert_time:
            return False
            
        key = k_alerts(user_id)
        # Fetch all alerts (capped at 200 in save_alert)
        raw_alerts = await self.redis.lrange(key, 0, -1)
        if not raw_alerts:
            return False
            
        updated = False
        new_list = []
        
        target_sym = norm_symbol(symbol)
        target_name = normalize_alert_name(alert_name) if alert_name else None
        
        for raw in raw_alerts:
            try:
                a = json.loads(raw)
                # Matches alert by exact time string and name (if provided)
                time_match = a.get("time") == alert_time
                name_match = True
                if target_name:
                    name_match = normalize_alert_name(a.get("alert_name", "")) == target_name
                
                if time_match and name_match:
                    res_list = a.get("result") or []
                    for r in res_list:
                        if norm_symbol(r.get("symbol", "")) == target_sym:
                            r["status"] = str(new_status)
                            if reason:
                                r["reason"] = str(reason)
                            updated = True
                new_list.append(json.dumps(a))
            except Exception:
                new_list.append(raw)
                
        if updated:
            # Atomic update via pipeline might be safer, but alerts are small
            await self.redis.delete(key)
            if new_list:
                await self.redis.rpush(key, *new_list)
            return True
            
        return False

    # =========================
    # Auto Square Off
    # =========================
    async def is_auto_sq_off_enabled(self, user_id: int) -> bool:
        val = await self.redis.get(k_auto_sq_off_config(user_id))
        return val == "1"

    async def set_auto_sq_off_enabled(self, user_id: int, enabled: bool) -> None:
        key = k_auto_sq_off_config(user_id)
        if enabled:
            await self.redis.set(key, "1")
        else:
            await self.redis.delete(key)

    async def has_auto_sq_off_run(self, user_id: int) -> bool:
        ymd = now_ist_date()
        return bool(await self.redis.exists(k_auto_sq_off_ran(user_id, ymd)))

    async def mark_auto_sq_off_run(self, user_id: int) -> None:
        ymd = now_ist_date()
        key = k_auto_sq_off_ran(user_id, ymd)
        ttl = seconds_until_next_ist_day(extra_grace_sec=3600)
        await self.redis.setex(key, int(ttl), "1")

    # =========================
    # Authentication - User Management
    # =========================
    async def save_user(self, user: Any) -> None:
        """Save user to Redis with email as key"""
        import hashlib
        # Generate user_id from email hash
        user_id = int(hashlib.md5(user.email.encode()).hexdigest()[:8], 16) % 100000
        
        # Save user data
        key = f"user:email:{user.email}"
        await self.redis.set(key, json.dumps(user.to_dict()))
        
        # Save email -> user_id mapping
        await self.redis.set(f"user:id:{user.email}", str(user_id))
    
    async def get_user_by_email(self, email: str) -> Optional[Any]:
        """Get user by email"""
        from .models import User
        key = f"user:email:{email}"
        raw = await self.redis.get(key)
        if not raw:
            return None
        try:
            return User.from_dict(json.loads(raw))
        except Exception:
            return None
    
    async def get_user_id_by_email(self, email: str) -> int:
        """Get user_id from email"""
        import hashlib
        raw = await self.redis.get(f"user:id:{email}")
        if raw:
            return int(raw)
        # Generate consistent user_id from email hash
        return int(hashlib.md5(email.encode()).hexdigest()[:8], 16) % 100000
    
    # =========================
    # Authentication - OTP Management
    # =========================
    async def save_otp(self, email: str, otp: Any) -> None:
        """Save OTP with 5 minute expiration"""
        key = f"otp:{email}"
        await self.redis.setex(key, 300, json.dumps(otp.to_dict()))  # 5 minutes
    
    async def get_otp(self, email: str) -> Optional[Any]:
        """Get OTP for email"""
        from .models import OTP
        key = f"otp:{email}"
        raw = await self.redis.get(key)
        if not raw:
            return None
        try:
            return OTP.from_dict(json.loads(raw))
        except Exception:
            return None
    
    async def delete_otp(self, email: str) -> None:
        """Delete OTP after successful verification"""
        key = f"otp:{email}"
        await self.redis.delete(key)
    
    async def check_otp_rate_limit(self, email: str) -> bool:
        """Check if user can request another OTP (max 3 per hour)"""
        key = f"otp:ratelimit:{email}"
        count = await self.redis.get(key)
        
        if count and int(count) >= 3:
            return False
        
        # Increment counter
        if not count:
            await self.redis.setex(key, 3600, "1")  # 1 hour
        else:
            await self.redis.incr(key)
        
        return True
    
    # =========================
    # Authentication - Session Management
    # =========================
    async def save_session(self, token: str, session: Any) -> None:
        """Save session with 24 hour expiration"""
        key = f"session:{token}"
        await self.redis.setex(key, 86400, json.dumps(session.to_dict()))  # 24 hours
    
    async def get_session(self, token: str) -> Optional[Any]:
        """Get session by token"""
        from .models import Session
        key = f"session:{token}"
        raw = await self.redis.get(key)
        if not raw:
            return None
        try:
            return Session.from_dict(json.loads(raw))
        except Exception:
            return None
    
    async def delete_session(self, token: str) -> bool:
        """Delete session (logout)"""
        key = f"session:{token}"
        result = await self.redis.delete(key)
        return bool(result)
