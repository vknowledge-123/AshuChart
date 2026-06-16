import asyncio
import json
import unittest

from app.websocket_manager import WebSocketManager


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages = []

    async def accept(self) -> None:
        return

    async def send_text(self, message: str) -> None:
        self.messages.append(json.loads(message))


class WebSocketManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_broadcast_nowait_delivers_first_tick(self) -> None:
        manager = WebSocketManager()
        socket = FakeWebSocket()
        await manager.connect(1, socket)

        manager.broadcast_nowait(
            1,
            {"type": "tick", "symbol": "SBIN", "ltp": 625.5},
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        self.assertEqual(len(socket.messages), 1)
        self.assertEqual(socket.messages[0]["symbol"], "SBIN")

    async def test_broadcast_nowait_still_throttles_rapid_duplicate_ticks(self) -> None:
        manager = WebSocketManager()
        socket = FakeWebSocket()
        await manager.connect(1, socket)

        manager.broadcast_nowait(1, {"type": "tick", "symbol": "SBIN", "ltp": 625.5})
        manager.broadcast_nowait(1, {"type": "tick", "symbol": "SBIN", "ltp": 625.6})
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        self.assertEqual(len(socket.messages), 1)


if __name__ == "__main__":
    unittest.main()
