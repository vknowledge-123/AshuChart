#!/usr/bin/env python3
"""
Initialize Encryption for the Trading Application

This script:
1. Checks if .env file exists
2. Generates a new encryption key if needed
3. Updates .env with ENCRYPTION_KEY
4. Tests encryption functionality
"""

import os
import sys
from pathlib import Path


def create_env_file():
    """Create .env file with encryption key."""
    
    env_path = Path(".env")
    
    # Check if .env already exists
    if env_path.exists():
        with open(env_path, 'r') as f:
            content = f.read()
            if "ENCRYPTION_KEY=" in content:
                print("✅ ENCRYPTION_KEY already exists in .env")
                return True
    
    print("🔐 Generating new encryption key...")
    
    # Generate encryption key
    try:
        from app.crypto import generate_encryption_key
        encryption_key = generate_encryption_key()
    except ImportError:
        print("❌ Error: Could not import crypto module")
        print("   Make sure 'cryptography' is installed: pip install cryptography")
        return False
    
    # Prepare .env content
    env_content = []
    
    if env_path.exists():
        # Read existing content
        with open(env_path, 'r') as f:
            env_content = f.readlines()
    
    # Add encryption key if not present
    has_encryption_key = any("ENCRYPTION_KEY=" in line for line in env_content)
    
    if not has_encryption_key:
        env_content.append(f"\n# Encryption key for API credentials (NEVER commit this to git!)\n")
        env_content.append(f"ENCRYPTION_KEY={encryption_key}\n")
    
    # Add Redis URL if not present
    has_redis_url = any("REDIS_URL=" in line for line in env_content)
    
    if not has_redis_url:
        env_content.append(f"\n# Redis connection URL\n")
        env_content.append(f"REDIS_URL=redis://localhost:6379/0\n")
    
    # Write to .env
    with open(env_path, 'w') as f:
        f.writelines(env_content)
    
    print(f"✅ Created/Updated .env file with encryption key")
    print(f"   Encryption Key: {encryption_key[:20]}...")
    
    return True


def verify_gitignore():
    """Ensure .env is in .gitignore."""
    
    gitignore_path = Path(".gitignore")
    
    if not gitignore_path.exists():
        with open(gitignore_path, 'w') as f:
            f.write(".env\n")
        print("✅ Created .gitignore with .env")
        return
    
    with open(gitignore_path, 'r') as f:
        content = f.read()
    
    if ".env" not in content:
        with open(gitignore_path, 'a') as f:
            f.write("\n.env\n")
        print("✅ Added .env to .gitignore")
    else:
        print("✅ .env already in .gitignore")


def test_encryption():
    """Test encryption functionality."""
    
    print("\n🔬 Testing encryption...")
    
    try:
        # Load .env
        from dotenv import load_dotenv
        load_dotenv()
        
        from app.crypto import init_encryption
        
        # Initialize encryption
        manager = init_encryption()
        
        if not manager.is_enabled():
            print("⚠️  Encryption not enabled - check ENCRYPTION_KEY in .env")
            return False
        
        # Test encryption
        test_key = "test_api_key"
        test_secret = "test_secret"
        
        enc_key, enc_secret = manager.encrypt_credentials(test_key, test_secret)
        dec_key, dec_secret = manager.decrypt_credentials(enc_key, enc_secret)
        
        if test_key == dec_key and test_secret == dec_secret:
            print("✅ Encryption test PASSED")
            return True
        else:
            print("❌ Encryption test FAILED")
            return False
            
    except ImportError as e:
        print(f"❌ Missing dependency: {e}")
        print("   Install required packages: pip install cryptography python-dotenv")
        return False
    except Exception as e:
        print(f"❌ Encryption test failed: {e}")
        return False


def main():
    """Main initialization function."""
    
    print("=" * 60)
    print("  AshAlgo Trading - Encryption Initialization")
    print("=" * 60)
    print()
    
    # Step 1: Create/update .env
    if not create_env_file():
        sys.exit(1)
    
    # Step 2: Verify .gitignore
    verify_gitignore()
    
    # Step 3: Test encryption
    if not test_encryption():
        print("\n⚠️  Warning: Encryption test failed")
        print("   The application will still work but credentials won't be encrypted")
    
    print()
    print("=" * 60)
    print("  Initialization Complete!")
    print("=" * 60)
    print()
    print("📝 Next Steps:")
    print("   1. Start your application")
    print("   2. Open the dashboard")
    print("   3. Re-enter your API credentials in settings")
    print("   4. Credentials will now be encrypted in Redis")
    print()
    print("⚠️  IMPORTANT:")
    print("   - NEVER commit .env to git")
    print("   - Keep a secure backup of your .env file")
    print("   - If you lose the encryption key, you'll need to re-enter credentials")
    print()


if __name__ == "__main__":
    main()
