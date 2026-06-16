from __future__ import annotations

import hashlib
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .models import OTP, Session, User
from .redis_store import norm_alert_name, norm_symbol


class InMemoryStore:
    """
    Minimal async store used for integration tests and local development.

    This intentionally implements only the subset of RedisStore that is used by:
      - app.main routes/startup
      - app.auth.AuthService
    """

    def __init__(self) -> None:
        self._credentials: Dict[int, Dict[str, str]] = {}
        self._access_tokens: Dict[int, str] = {}
        self._brokers: Dict[int, str] = {}
        self._dhan_credentials: Dict[int, Dict[str, str]] = {}
        self._kill: Dict[int, bool] = {}
        self._alert_configs: Dict[int, Dict[str, Dict[str, Any]]] = {}
        self._alerts: Dict[int, List[Dict[str, Any]]] = {}
        self._positions: Dict[int, Dict[str, Dict[str, Any]]] = {}
        self._pnl_exit_cfg: Dict[int, Dict[str, Any]] = {}
        self._locks: Dict[str, float] = {}
        self._open_trades: Dict[str, str] = {}
        self._trade_counts: Dict[str, int] = {}
        self._symbol_tokens: Dict[str, int] = {}

        self._auto_sq_off_enabled: Dict[int, bool] = {}
        self._auto_sq_off_ran_ymd: Dict[int, str] = {}

        # Auth data
        self._users_by_email: Dict[str, Dict[str, Any]] = {}
        self._user_id_by_email: Dict[str, int] = {}
        self._otp_by_email: Dict[str, Dict[str, Any]] = {}
        self._otp_requests: Dict[str, List[float]] = {}  # email -> timestamps
        self._sessions_by_token: Dict[str, Dict[str, Any]] = {}

    # -------------------------
    # Compatibility / lifecycle
    # -------------------------
    async def close(self) -> None:
        return

    async def ping(self) -> bool:
        return True

    async def init_scripts(self) -> None:
        return

    # -------------------------
    # Credentials + tokens
    # -------------------------
    async def save_credentials(self, user_id: int, api_key: str, api_secret: str) -> None:
        self._credentials[int(user_id)] = {"api_key": api_key, "api_secret": api_secret}

    async def load_credentials(self, user_id: int) -> Dict[str, str]:
        return dict(self._credentials.get(int(user_id), {}))

    async def save_access_token(self, user_id: int, access_token: str) -> None:
        self._access_tokens[int(user_id)] = str(access_token or "")

    async def load_access_token(self, user_id: int) -> str:
        return str(self._access_tokens.get(int(user_id), ""))

    async def save_broker(self, user_id: int, broker: str) -> str:
        selected = str(broker or "ZERODHA").strip().upper()
        if selected not in {"ZERODHA", "DHAN"}:
            raise ValueError("UNSUPPORTED_BROKER")
        self._brokers[int(user_id)] = selected
        return selected

    async def load_broker(self, user_id: int) -> str:
        return self._brokers.get(int(user_id), "ZERODHA")

    async def save_dhan_credentials(
        self, user_id: int, client_id: str, access_token: str
    ) -> None:
        self._dhan_credentials[int(user_id)] = {
            "client_id": str(client_id or ""),
            "access_token": str(access_token or ""),
        }

    async def load_dhan_credentials(self, user_id: int) -> Dict[str, str]:
        return dict(self._dhan_credentials.get(int(user_id), {}))

    # -------------------------
    # Kill switch
    # -------------------------
    async def set_kill(self, user_id: int, enabled: bool) -> None:
        self._kill[int(user_id)] = bool(enabled)

    async def is_kill(self, user_id: int) -> bool:
        return bool(self._kill.get(int(user_id), False))

    # -------------------------
    # P&L exit config (MTM-based)
    # -------------------------
    async def get_pnl_exit_config(self, user_id: int) -> Dict[str, Any]:
        return dict(self._pnl_exit_cfg.get(int(user_id), {"enabled": False, "max_profit": 0.0, "max_loss": 0.0}))

    async def set_pnl_exit_config(self, user_id: int, cfg: Dict[str, Any]) -> Dict[str, Any]:
        enabled = bool(cfg.get("enabled", False))
        max_profit = float(cfg.get("max_profit", 0.0) or 0.0)
        max_loss = float(cfg.get("max_loss", 0.0) or 0.0)
        if max_profit < 0:
            max_profit = abs(max_profit)
        if max_loss < 0:
            max_loss = abs(max_loss)
        payload = {"enabled": enabled, "max_profit": max_profit, "max_loss": max_loss}
        self._pnl_exit_cfg[int(user_id)] = dict(payload)
        return payload

    # -------------------------
    # Alert config
    # -------------------------
    async def list_alert_configs(self, user_id: int) -> Dict[str, Dict[str, Any]]:
        cfg = self._alert_configs.get(int(user_id), {})
        return {key: dict(value) for key, value in cfg.items()}

    async def get_alert_config(self, user_id: int, alert_name: str) -> Optional[Dict[str, Any]]:
        value = self._alert_configs.get(int(user_id), {}).get(norm_alert_name(alert_name))
        return dict(value) if value else None

    async def save_alert_config(self, user_id: int, payload: Dict[str, Any]) -> None:
        uid = int(user_id)
        alert_name = norm_alert_name(str(payload.get("alert_name") or ""))
        if not alert_name:
            return
        self._alert_configs.setdefault(uid, {})[alert_name] = dict(payload)

    async def delete_alert_config(self, user_id: int, alert_name: str) -> bool:
        uid = int(user_id)
        key = norm_alert_name(alert_name)
        if not key:
            return False
        cfg = self._alert_configs.get(uid)
        if not cfg or key not in cfg:
            return False
        del cfg[key]
        return True

    # -------------------------
    # Alerts history
    # -------------------------
    async def save_alert(self, user_id: int, payload: Dict[str, Any]) -> None:
        uid = int(user_id)
        item = dict(payload)
        items = self._alerts.setdefault(uid, [])
        for index, existing in enumerate(items):
            if existing.get("alert_name") == item.get("alert_name") and existing.get("time") == item.get("time"):
                merged = dict(existing)
                merged.update(item)
                items[index] = merged
                return
        items.insert(0, item)
        del items[200:]

    async def get_recent_alerts(self, user_id: int, limit: int = 100) -> List[Dict[str, Any]]:
        uid = int(user_id)
        items = self._alerts.get(uid, [])
        limit_n = max(0, int(limit))
        if limit_n <= 0:
            return []
        return list(items[:limit_n])

    async def update_alert_status(
        self,
        user_id: int,
        alert_time: str,
        symbol: str,
        new_status: str,
        reason: str = "",
        alert_name: str = "",
    ) -> bool:
        target_symbol = norm_symbol(symbol)
        target_name = norm_alert_name(alert_name) if alert_name else ""
        for alert in self._alerts.get(int(user_id), []):
            if alert.get("time") != alert_time:
                continue
            if target_name and norm_alert_name(alert.get("alert_name", "")) != target_name:
                continue
            for result in alert.get("result") or []:
                if norm_symbol(result.get("symbol", "")) == target_symbol:
                    result["status"] = str(new_status)
                    if reason:
                        result["reason"] = str(reason)
                    return True
        return False

    async def delete_alerts(self, user_id: int) -> None:
        self._alerts[int(user_id)] = []

    # -------------------------
    # Positions
    # -------------------------
    async def upsert_position(self, user_id: int, symbol: str, position: Dict[str, Any]) -> None:
        uid = int(user_id)
        sym = norm_symbol(symbol)
        if not sym:
            return
        self._positions.setdefault(uid, {})[sym] = dict(position)

    async def list_positions(self, user_id: int) -> List[Dict[str, Any]]:
        uid = int(user_id)
        return list(self._positions.get(uid, {}).values())

    async def delete_position(self, user_id: int, symbol: str) -> None:
        self._positions.get(int(user_id), {}).pop(norm_symbol(symbol), None)

    def _guard_key(self, user_id: int, symbol: str, action: str = "") -> str:
        return f"{int(user_id)}:{norm_symbol(symbol)}:{action.strip().lower()}"

    async def acquire_lock(self, user_id: int, symbol: str, action: str, ttl_ms: int = 1200) -> int:
        if action.strip().lower() != "exit" and await self.is_kill(user_id):
            return -2
        key = self._guard_key(user_id, symbol, action)
        now = time.time()
        if self._locks.get(key, 0.0) > now:
            return 0
        self._locks[key] = now + max(1, int(ttl_ms)) / 1000.0
        return 1

    async def release_lock(self, user_id: int, symbol: str, action: str) -> None:
        self._locks.pop(self._guard_key(user_id, symbol, action), None)

    async def allow_trade(self, user_id: int, alert_name: str, limit: int) -> bool:
        if int(limit) <= 0:
            return True
        ymd = datetime.utcnow().strftime("%Y%m%d")
        key = f"{int(user_id)}:{ymd}:{norm_alert_name(alert_name)}"
        count = self._trade_counts.get(key, 0)
        if count >= int(limit):
            return False
        self._trade_counts[key] = count + 1
        return True

    async def mark_open(self, user_id: int, symbol: str, trade_id: str, ttl_sec: int = 60 * 60 * 8) -> None:
        self._open_trades[self._guard_key(user_id, symbol)] = str(trade_id)

    async def get_open(self, user_id: int, symbol: str) -> str:
        return self._open_trades.get(self._guard_key(user_id, symbol), "")

    async def clear_open(self, user_id: int, symbol: str) -> None:
        self._open_trades.pop(self._guard_key(user_id, symbol), None)

    async def set_symbol_token(self, symbol: str, token: int) -> None:
        self._symbol_tokens[norm_symbol(symbol)] = int(token)

    async def get_symbol_token(self, symbol: str) -> Optional[int]:
        return self._symbol_tokens.get(norm_symbol(symbol))

    # -------------------------
    # Auto Square Off
    # -------------------------
    async def is_auto_sq_off_enabled(self, user_id: int) -> bool:
        return bool(self._auto_sq_off_enabled.get(int(user_id), False))

    async def set_auto_sq_off_enabled(self, user_id: int, enabled: bool) -> None:
        self._auto_sq_off_enabled[int(user_id)] = bool(enabled)

    async def has_auto_sq_off_run(self, user_id: int) -> bool:
        uid = int(user_id)
        ymd = datetime.utcnow().strftime("%Y%m%d")
        return self._auto_sq_off_ran_ymd.get(uid) == ymd

    async def mark_auto_sq_off_run(self, user_id: int) -> None:
        uid = int(user_id)
        ymd = datetime.utcnow().strftime("%Y%m%d")
        self._auto_sq_off_ran_ymd[uid] = ymd

    async def list_all_user_ids(self) -> List[int]:
        uids = (
            set(self._credentials.keys())
            | set(self._access_tokens.keys())
            | set(self._dhan_credentials.keys())
            | set(self._brokers.keys())
            | set(self._kill.keys())
        )
        return sorted(uids)

    # -------------------------
    # Auth: users
    # -------------------------
    @staticmethod
    def _stable_user_id(email: str) -> int:
        return int(hashlib.md5(email.encode()).hexdigest()[:8], 16) % 100000

    async def save_user(self, user: Any) -> None:
        u = user if isinstance(user, User) else User(**user)
        self._users_by_email[u.email] = u.to_dict()
        self._user_id_by_email[u.email] = self._stable_user_id(u.email)

    async def get_user_by_email(self, email: str) -> Optional[Any]:
        raw = self._users_by_email.get(email)
        if not raw:
            return None
        try:
            return User.from_dict(dict(raw))
        except Exception:
            return None

    async def get_user_id_by_email(self, email: str) -> int:
        if email in self._user_id_by_email:
            return int(self._user_id_by_email[email])
        uid = self._stable_user_id(email)
        self._user_id_by_email[email] = uid
        return uid

    # -------------------------
    # Auth: OTP
    # -------------------------
    async def save_otp(self, email: str, otp: Any) -> None:
        o = otp if isinstance(otp, OTP) else OTP(**otp)
        self._otp_by_email[email] = o.to_dict()

    async def get_otp(self, email: str) -> Optional[Any]:
        raw = self._otp_by_email.get(email)
        if not raw:
            return None
        try:
            otp = OTP.from_dict(dict(raw))
        except Exception:
            return None
        if datetime.utcnow() >= otp.expires_at:
            self._otp_by_email.pop(email, None)
            return None
        return otp

    async def delete_otp(self, email: str) -> None:
        self._otp_by_email.pop(email, None)

    async def check_otp_rate_limit(self, email: str) -> bool:
        # max 3 per hour
        now = time.time()
        window = 3600.0
        times = self._otp_requests.setdefault(email, [])
        times[:] = [t for t in times if (now - t) < window]
        if len(times) >= 3:
            return False
        times.append(now)
        return True

    # -------------------------
    # Auth: sessions
    # -------------------------
    async def save_session(self, token: str, session: Any) -> None:
        s = session if isinstance(session, Session) else Session(**session)
        self._sessions_by_token[token] = s.to_dict()

    async def get_session(self, token: str) -> Optional[Any]:
        raw = self._sessions_by_token.get(token)
        if not raw:
            return None
        try:
            sess = Session.from_dict(dict(raw))
        except Exception:
            return None
        if datetime.utcnow() >= sess.expires_at:
            self._sessions_by_token.pop(token, None)
            return None
        return sess

    async def delete_session(self, token: str) -> bool:
        existed = token in self._sessions_by_token
        self._sessions_by_token.pop(token, None)
        return existed
