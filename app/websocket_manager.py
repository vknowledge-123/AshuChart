# app/websocket_manager.py
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from fastapi import WebSocket


class WebSocketManager:
    """
    Goals:
    - Store per-user websocket connections safely (even if WebSocket is unhashable)
    - Provide broadcast() (await) and broadcast_nowait() (non-blocking)
    - Thread-safe: KiteTicker callbacks often run in a different thread -> schedule onto APP loop
    - Throttle tick broadcasts (avoid UI overload)
    - Keep dependencies intact: only stdlib + fastapi.WebSocket
    """

    def __init__(self) -> None:
        # user_id -> list of (conn_id, websocket)
        self._conns: Dict[int, List[Tuple[str, WebSocket]]] = {}
        self._lock = asyncio.Lock()

        # Main event loop reference (set from FastAPI startup)
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Throttle tick broadcasts: max N updates/sec per user per symbol
        self._last_tick_time: Dict[Tuple[int, str], float] = {}
        self._tick_throttle_sec: float = 0.05  # 50ms => 20 updates/sec
        self._last_log_time: Dict[Tuple[int, str], float] = {}
        self._log_throttle_sec: float = 1.0  # max 1 log/sec per (user,type)

    # -----------------------
    # Loop binding (important)
    # -----------------------
    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """
        Call once during startup (inside event loop):
            ws_mgr.set_loop(asyncio.get_running_loop())
        So broadcast_nowait can schedule tasks safely from other threads.
        """
        self._loop = loop

    # -----------------------
    # Connection management
    # -----------------------
    async def connect(self, user_id: int, ws: WebSocket) -> None:
        """
        Accept and register websocket for the user.
        Uses generated conn_id so we don't depend on ws hashability.
        """
        await ws.accept()
        uid = int(user_id)
        conn_id = uuid.uuid4().hex
        async with self._lock:
            self._conns.setdefault(uid, []).append((conn_id, ws))

    async def disconnect(self, user_id: int, ws: WebSocket) -> None:
        """
        Remove websocket from registry.
        Removes by object identity (not hash).
        """
        uid = int(user_id)
        async with self._lock:
            rows = self._conns.get(uid)
            if not rows:
                return
            new_rows: List[Tuple[str, WebSocket]] = [(cid, w) for (cid, w) in rows if w is not ws]
            if new_rows:
                self._conns[uid] = new_rows
            else:
                self._conns.pop(uid, None)

    async def count(self, user_id: int) -> int:
        uid = int(user_id)
        async with self._lock:
            return len(self._conns.get(uid, []))

    async def close_all(self, user_id: int) -> None:
        """
        Force-close all sockets for a user.
        """
        uid = int(user_id)
        async with self._lock:
            rows = self._conns.pop(uid, [])
        for _cid, ws in rows:
            try:
                await ws.close()
            except Exception:
                pass

    async def close_everyone(self) -> None:
        """
        Force-close all sockets for all users.
        """
        async with self._lock:
            all_rows = self._conns
            self._conns = {}
        sockets: List[WebSocket] = []
        for _uid, rows in all_rows.items():
            for _cid, ws in rows:
                sockets.append(ws)
        for ws in sockets:
            try:
                await ws.close()
            except Exception:
                pass

    # -----------------------
    # Internal helpers
    # -----------------------
    async def _snapshot(self, user_id: int) -> List[Tuple[str, WebSocket]]:
        uid = int(user_id)
        async with self._lock:
            return list(self._conns.get(uid, []))

    def _should_throttle_tick(self, user_id: int, payload: Dict[str, Any]) -> bool:
        """
        Tick throttling per (user_id, symbol).
        Works in both broadcast and broadcast_nowait paths.
        """
        if payload.get("type") != "tick":
            return False

        sym = str(payload.get("symbol") or "")
        if not sym:
            return False

        key = (int(user_id), sym)
        now = time.time()
        last = self._last_tick_time.get(key, 0.0)
        if now - last < self._tick_throttle_sec:
            return True

        self._last_tick_time[key] = now
        return False

    # -----------------------
    # Broadcasting
    # -----------------------
    async def broadcast(self, user_id: int, payload: Dict[str, Any]) -> None:
        """
        Awaited broadcast (safe inside event loop).
        Tick payloads are throttled.
        Removes dead sockets.
        """
        uid = int(user_id)

        if self._should_throttle_tick(uid, payload):
            return

        msg = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)

        conns = await self._snapshot(uid)
        if not conns:
            return

        dead_ids: List[str] = []
        for cid, ws in conns:
            try:
                await ws.send_text(msg)
            except Exception:
                dead_ids.append(cid)

        if dead_ids:
            async with self._lock:
                rows = self._conns.get(uid, [])
                self._conns[uid] = [(cid, w) for (cid, w) in rows if cid not in dead_ids]
                if not self._conns[uid]:
                    self._conns.pop(uid, None)

        # Reduce console noise: log only non-tick, throttled
        if payload.get("type") != "tick":
            ptype = str(payload.get("type"))
            key = (uid, ptype)
            now = time.time()
            last = self._last_log_time.get(key, 0.0)
            if now - last >= self._log_throttle_sec:
                self._last_log_time[key] = now
                print("[WS] broadcast user", uid, "clients", len(conns), "type", ptype)

    def broadcast_nowait(self, user_id: int, payload: Dict[str, Any]) -> None:
        """
        Non-blocking broadcast.

        - If called from the event loop: schedule task directly.
        - If called from another thread (e.g., KiteTicker thread): schedule onto saved loop.

        NOTE: If set_loop() is not called, the message is dropped (safe fail).
        """
        uid = int(user_id)

        # Case 1: We are already inside an event loop
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.broadcast(uid, payload))
            return
        except RuntimeError:
            pass  # not in event loop thread

        # Case 2: Called from another thread
        loop = self._loop
        if loop is None:
            return  # safe drop if startup didn't bind loop

        # Schedule coroutine onto the main loop thread safely
        loop.call_soon_threadsafe(asyncio.create_task, self.broadcast(uid, payload))
