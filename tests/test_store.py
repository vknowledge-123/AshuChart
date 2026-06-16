import unittest
from unittest.mock import AsyncMock

from app.redis_store import RedisStore, k_alert_cfg, k_alert_cfg_legacy


class FakeRedis:
    def __init__(self) -> None:
        self.deleted = []
        self.hdeleted = []
        self.scan_patterns = []

    async def hdel(self, key, field):
        self.hdeleted.append((key, field))
        return 1

    async def delete(self, *keys):
        self.deleted.extend(keys)
        return len(keys)

    async def scan_iter(self, match):
        self.scan_patterns.append(match)
        if match.startswith("trade:open:"):
            yield "trade:open:1:SBIN"
        if match.startswith("trade:count:"):
            yield "trade:count:1:20260614:test"

    async def close(self):
        return None


class RedisStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_delete_alert_config_removes_new_and_legacy_entries(self) -> None:
        store = RedisStore("redis://unused")
        fake = FakeRedis()
        store.redis = fake

        deleted = await store.delete_alert_config(1, "My_Strategy")

        self.assertTrue(deleted)
        self.assertEqual(
            fake.hdeleted,
            [
                (k_alert_cfg(1), "my strategy"),
                (k_alert_cfg_legacy(1), "my strategy"),
            ],
        )

    async def test_daily_state_cleanup_removes_guards_counters_and_snapshots(self) -> None:
        store = RedisStore("redis://unused")
        fake = FakeRedis()
        store.redis = fake

        result = await store.clear_daily_trading_state(1)

        self.assertEqual(result["scanned_keys"], 2)
        self.assertIn("positions:1", fake.deleted)
        self.assertIn("alerts:1", fake.deleted)
        self.assertIn("kill:1", fake.deleted)
        self.assertIn("trade:open:1:SBIN", fake.deleted)
        self.assertIn("trade:count:1:20260614:test", fake.deleted)
        self.assertIn("lock:1:*", fake.scan_patterns)


if __name__ == "__main__":
    unittest.main()
