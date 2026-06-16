"""Daily reset of ephemeral trading state.

Schedule this script before the trading session. It preserves alert
configurations, Kite credentials, access tokens, users, and sessions.
"""

import asyncio
import logging
import os
import sys
from datetime import datetime

import pytz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.redis_store import RedisStore


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)


async def daily_cleanup(user_id: int = 1) -> None:
    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.now(ist)
    redis_url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    store = RedisStore(redis_url)

    log.info("Daily cleanup started at %s for user %s", now.strftime("%Y-%m-%d %H:%M:%S %Z"), user_id)
    try:
        if not await store.ping():
            raise RuntimeError(f"Redis is not reachable at {redis_url}")

        positions = await store.list_positions(user_id)
        alerts = await store.get_recent_alerts(user_id, limit=1000)
        cleanup = await store.clear_daily_trading_state(user_id)
        configs = await store.list_alert_configs(user_id)

        log.info(
            "Daily cleanup completed: positions=%s alerts=%s deleted_keys=%s configs_preserved=%s",
            len(positions or []),
            len(alerts or []),
            cleanup.get("deleted_keys", 0),
            len(configs or {}),
        )
    finally:
        await store.close()


async def main() -> int:
    try:
        await daily_cleanup(int(os.getenv("USER_ID", "1")))
        return 0
    except Exception:
        log.exception("Daily cleanup failed")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
