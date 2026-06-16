# Quick Encryption Setup Checklist

## ✅ What You Need to Do (3 Steps)

### Step 1: Check if `.env` has encryption key

```bash
# Open .env file
notepad .env

# Look for this line:
ENCRYPTION_KEY=gAAAAABn...
```

**If FOUND:** ✅ You're done! Encryption already active!  
**If NOT FOUND:** Run Step 2 ⬇️

---

### Step 2: Generate Encryption Key

```bash
cd C:\Users\Acer\OneDrive\Desktop\Ashutosh_Chartink_Cilent-2
python init_encryption.py
```

**Expected Output:**
```
============================================================
  AshAlgo Trading - Encryption Initialization
============================================================

✅ Created/Updated .env file with encryption key
   Encryption Key: gAAAAABn...
✅ .env already in .gitignore
🔬 Testing encryption...
✅ Encryption test PASSED

============================================================
  Initialization Complete!
============================================================
```

---

### Step 3: Restart Application

**Windows:**
- Stop the current server (Ctrl+C)
- Start again: `uvicorn app.main:app --reload`

**Production:**
```bash
sudo systemctl restart algoedge
```

---

## 🔍 Verify Encryption is Working

### Check 1: Startup Message

When app starts, you should see:
```
🔐 Encryption enabled - API credentials will be encrypted
```

**If you see:**
```
⚠️ Encryption disabled - Set ENCRYPTION_KEY in .env
```
→ Go back to Step 1

---

### Check 2: Re-enter API Credentials

1. Open dashboard
2. Go to settings
3. Re-enter Zerodha API Key & Secret
4. Save

**From now on, they're encrypted in Redis!** ✅

---

### Check 3: Verify in Redis (Optional)

```bash
# Open Redis CLI
redis-cli

# Check stored API key
GET user:1:kite_api_key

# Should see encrypted text:
"Z0FBQUFBQm5..."  ← Encrypted! ✅

# NOT plain text:
"abc123xyz"  ← Would be ERROR ❌
```

---

## ⚠️ IMPORTANT Security Notes

1. **NEVER commit `.env` to Git**
   - Already in `.gitignore` ✅
   
2. **Backup your encryption key**
   - Copy `ENCRYPTION_KEY` value from `.env`
   - Store in password manager
   
3. **If you lose the key**
   - Encrypted data unrecoverable
   - Just re-enter API credentials
   - They'll be encrypted with new key

---

## 📊 What Gets Encrypted

| Data Type | Encrypted? |
|-----------|------------|
| Zerodha API Key | ✅ YES |
| Zerodha API Secret | ✅ YES |
| Access Tokens | ✅ YES |
| Alert Configs | ✅ YES |
| Position Data | ✅ YES |
| Symbol Names | ❌ No (public data) |
| LTP Prices | ❌ No (public data) |

---

## ✅ Done!

**Your data is now end-to-end encrypted!**

🔐 Algorithm: **AES-256-GCM** (bank-grade)  
🔒 At Rest: **Encrypted in Redis**  
🌐 In Transit: **HTTPS (SSL)**

**Total Setup Time:** 2 minutes ⏱️
