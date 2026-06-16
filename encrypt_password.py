import os
from app.crypto import init_encryption
from dotenv import load_dotenv

def encrypt_password():
    print("🔐 Password Encryption Tool")
    print("===========================")
    
    # Load env to get ENCRYPTION_KEY
    load_dotenv()
    
    if not os.getenv("ENCRYPTION_KEY"):
        print("❌ Error: ENCRYPTION_KEY not found in .env")
        print("Run 'python init_encryption.py' first!")
        return

    password = input("\nEnter your App Password to encrypt: ").strip()
    
    if not password:
        print("❌ Password cannot be empty")
        return

    # Encrypt
    manager = init_encryption()
    encrypted = manager.encrypt(password)
    
    print("\n✅ Encrypted Password:")
    print("-" * 50)
    print(encrypted)
    print("-" * 50)
    print("\n📋 Instructions:")
    print("1. Copy the encrypted string above")
    print("2. Open your .env file")
    print("3. Replace SMTP_PASSWORD with this value")
    print(f"   SMTP_PASSWORD={encrypted}")
    print("\nYour email service will automatically decrypt it!")

if __name__ == "__main__":
    encrypt_password()
