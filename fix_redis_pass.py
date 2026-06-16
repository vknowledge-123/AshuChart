
import redis

try:
    # Try connecting without password
    r = redis.Redis(host='localhost', port=6379, db=0)
    if r.ping():
        print("Connected to Redis without password.")
        # Set the password
        password = "uH-miM3uuBR7GNgOrtzVfBwBkFU0bxYRKG_sxO570q8"
        r.config_set("requirepass", password)
        print(f"Successfully set requirepass to {password[:5]}...")
    else:
        print("Ping failed.")
except redis.exceptions.AuthenticationError:
    print("Redis already requires a password.")
except Exception as e:
    print(f"Error: {e}")
