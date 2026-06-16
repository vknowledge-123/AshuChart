
import asyncio
import os
from app.redis_store import RedisStore
from app.trade_engine import TradeEngine
from app.crypto import init_encryption
from dotenv import load_dotenv

async def check():
    load_dotenv()
    
    # Initialize encryption
    encryption_manager = init_encryption()
    
    store = RedisStore(
        os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
        encryption_manager=encryption_manager
    )
    user_id = 6565 # Actual user ID found in Redis
    
    # 1. Check Redis positions
    redis_positions = await store.list_positions(user_id)
    print(f"--- Redis Positions ({len(redis_positions)}) ---")
    for p in redis_positions:
        print(f"Sym: {p.get('symbol')}, Status: {p.get('status')}, Qty: {p.get('qty')}")
    
    # 2. Check Zerodha positions
    eng = TradeEngine(user_id=user_id, store=store)
    await eng.configure_kite()
    
    ok = await eng._ensure_kite_ready()
    if not ok:
        print("❌ Kite not ready")
        return
        
    try:
        data = await eng._kite_positions()
        rows = list(data.get("net") or []) + list(data.get("day") or [])
        print("\n--- Zerodha Positions ---")
        found_any = False
        for r in rows:
            qty = int(r.get("quantity") or 0)
            if qty != 0:
                sym = r.get("tradingsymbol")
                print(f"Sym: {sym}, Qty: {qty}, Product: {r.get('product')}")
                found_any = True
        
        if not found_any:
            print("No active positions found in Zerodha.")
    except Exception as e:
        print(f"❌ Error fetching Kite positions: {e}")

if __name__ == "__main__":
    asyncio.run(check())
