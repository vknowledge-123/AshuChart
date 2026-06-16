
import redis
import os
from dotenv import load_dotenv

load_dotenv()

redis_url = os.getenv("REDIS_URL")
if not redis_url:
    print("REDIS_URL not set")
    exit(1)

try:
    r = redis.from_url(redis_url)
    if r.ping():
        print("✅ SUCCESS: Connected to Redis using password from .env!")
    else:
        print("❌ FAIL: Ping failed (unknown reason)")
except redis.exceptions.AuthenticationError:
    print("❌ FAIL: Authentication failed with password!")
except Exception as e:
    print(f"❌ FAIL: Error: {e}")
