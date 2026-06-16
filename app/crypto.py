#!/usr/bin/env python3
"""
Encryption Module for Secure Credential Storage

Uses Fernet (symmetric encryption) to encrypt sensitive data before storing in Redis.
Encryption key is stored in .env file and never committed to git.
"""

import os
import base64
from typing import Optional, Tuple
from cryptography.fernet import Fernet, InvalidToken


class EncryptionManager:
    """Handles encryption/decryption of sensitive data."""
    
    def __init__(self, encryption_key: Optional[str] = None):
        """
        Initialize encryption manager.
        
        Args:
            encryption_key: Base64-encoded Fernet key. If None, tries to load from env.
        """
        self.key = encryption_key or os.getenv("ENCRYPTION_KEY")
        self.cipher = None
        
        if self.key:
            try:
                self.cipher = Fernet(self.key.encode() if isinstance(self.key, str) else self.key)
            except Exception as e:
                print(f"⚠️  Warning: Invalid encryption key: {e}")
                self.cipher = None
    
    def is_enabled(self) -> bool:
        """Check if encryption is enabled."""
        return self.cipher is not None
    
    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt plaintext string.
        
        Args:
            plaintext: String to encrypt
            
        Returns:
            Encrypted string (base64-encoded)
        """
        if not self.cipher:
            # Fallback to unencrypted if no key configured
            return plaintext
        
        try:
            encrypted_bytes = self.cipher.encrypt(plaintext.encode('utf-8'))
            return base64.b64encode(encrypted_bytes).decode('utf-8')
        except Exception as e:
            print(f"⚠️  Encryption failed: {e}")
            return plaintext
    
    def decrypt(self, encrypted: str) -> str:
        """
        Decrypt encrypted string.
        
        Args:
            encrypted: Encrypted string (base64-encoded)
            
        Returns:
            Decrypted plaintext string
        """
        if not self.cipher:
            # Assume unencrypted if no key configured
            return encrypted
        
        try:
            # Try to decrypt (assumes it's encrypted)
            encrypted_bytes = base64.b64decode(encrypted.encode('utf-8'))
            decrypted_bytes = self.cipher.decrypt(encrypted_bytes)
            return decrypted_bytes.decode('utf-8')
        except (InvalidToken, Exception):
            # If decryption fails, assume it's already plaintext (backward compatibility)
            return encrypted
    
    def encrypt_credentials(self, api_key: str, api_secret: str) -> Tuple[str, str]:
        """
        Encrypt API credentials.
        
        Args:
            api_key: Unencrypted API key
            api_secret: Unencrypted API secret
            
        Returns:
            Tuple of (encrypted_api_key, encrypted_api_secret)
        """
        return self.encrypt(api_key), self.encrypt(api_secret)
    
    def decrypt_credentials(self, encrypted_api_key: str, encrypted_api_secret: str) -> Tuple[str, str]:
        """
        Decrypt API credentials.
        
        Args:
            encrypted_api_key: Encrypted API key
            encrypted_api_secret: Encrypted API secret
            
        Returns:
            Tuple of (decrypted_api_key, decrypted_api_secret)
        """
        return self.decrypt(encrypted_api_key), self.decrypt(encrypted_api_secret)


def generate_encryption_key() -> str:
    """
    Generate a new Fernet encryption key.
    
    Returns:
        Base64-encoded encryption key as string
    """
    key = Fernet.generate_key()
    return key.decode('utf-8')


# Global instance (initialized in main.py)
encryption_manager: Optional[EncryptionManager] = None


def init_encryption(encryption_key: Optional[str] = None) -> EncryptionManager:
    """
    Initialize the global encryption manager.
    
    Args:
        encryption_key: Optional encryption key. If None, loads from environment.
        
    Returns:
        EncryptionManager instance
    """
    global encryption_manager
    encryption_manager = EncryptionManager(encryption_key)
    
    if encryption_manager.is_enabled():
        print("🔐 Encryption enabled - API credentials will be encrypted")
    else:
        print("⚠️  Encryption disabled - Set ENCRYPTION_KEY in .env for enhanced security")
    
    return encryption_manager


def get_encryption_manager() -> Optional[EncryptionManager]:
    """Get the global encryption manager instance."""
    return encryption_manager


if __name__ == "__main__":
    # Test encryption/decryption
    print("Testing encryption module...\n")
    
    # Generate test key
    test_key = generate_encryption_key()
    print(f"Generated test key: {test_key[:20]}...\n")
    
    # Initialize manager
    manager = EncryptionManager(test_key)
    
    # Test data
    api_key = "test_api_key_12345"
    api_secret = "test_api_secret_67890"
    
    print(f"Original API Key: {api_key}")
    print(f"Original API Secret: {api_secret}\n")
    
    # Encrypt
    enc_key, enc_secret = manager.encrypt_credentials(api_key, api_secret)
    print(f"Encrypted API Key: {enc_key[:40]}...")
    print(f"Encrypted API Secret: {enc_secret[:40]}...\n")
    
    # Decrypt
    dec_key, dec_secret = manager.decrypt_credentials(enc_key, enc_secret)
    print(f"Decrypted API Key: {dec_key}")
    print(f"Decrypted API Secret: {dec_secret}\n")
    
    # Verify
    if api_key == dec_key and api_secret == dec_secret:
        print("✅ Encryption test PASSED")
    else:
        print("❌ Encryption test FAILED")
