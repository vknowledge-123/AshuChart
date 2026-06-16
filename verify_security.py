import requests
import time
import sys

BASE_URL = "http://127.0.0.1:8002"

def test_headers():
    print(f"--- Testing Security Headers at {BASE_URL}/login ---")
    try:
        r = requests.get(f"{BASE_URL}/login")
        headers = r.headers
        print(f"Status: {r.status_code}")
        
        required = {
            "X-Frame-Options": "DENY",
            "X-Content-Type-Options": "nosniff",
            "X-XSS-Protection": "1; mode=block",
            "Strict-Transport-Security": "max-age=31536000; includeSubDomains"
        }
        
        all_passed = True
        for h, expected in required.items():
            val = headers.get(h)
            if val and expected in val: # loose match for STS
                print(f"[PASS] {h}: {val}")
            elif val == expected:
                 print(f"[PASS] {h}: {val}")
            else:
                print(f"[FAIL] {h}: Expected '{expected}', got '{val}'")
                all_passed = False
        
        if "Content-Security-Policy" in headers:
            print(f"[PASS] CSP: {headers['Content-Security-Policy'][:50]}...")
        else:
            print("[FAIL] CSP Header missing")
            all_passed = False
            
        return all_passed
    except Exception as e:
        print(f"Failed to connect: {e}")
        return False

def test_rate_limit():
    print(f"\n--- Testing Rate Limit at {BASE_URL}/api/auth/send-otp ---")
    # Limit is 5/5minute
    url = f"{BASE_URL}/api/auth/send-otp"
    data = {"email": "security_test@example.com"}
    
    triggered = False
    for i in range(1, 10):
        try:
            r = requests.post(url, json=data)
            print(f"Req {i}: Status {r.status_code}")
            if r.status_code == 429:
                print("✅ Rate limit triggered (429 Too Many Requests)")
                triggered = True
                break
        except Exception as e:
            print(f"Req {i} failed: {e}")
        time.sleep(0.2)
        
    if not triggered:
        print("❌ Rate limit NOT triggered within 10 requests")
    return triggered

if __name__ == "__main__":
    # Wait for server to be ready
    print("Waiting for server...")
    time.sleep(5) 
    
    headers_ok = test_headers()
    rate_ok = test_rate_limit()
    
    if headers_ok and rate_ok:
        print("\n✅ ALL SECURITY TESTS PASSED")
    else:
        print("\n❌ SOME TESTS FAILED")
