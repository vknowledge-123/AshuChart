# app/models.py
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from pydantic import BaseModel, EmailStr, validator
import secrets
import hashlib


class User(BaseModel):
    """User model for authentication"""
    email: EmailStr
    verified: bool = False
    created_at: datetime = None
    
    def __init__(self, **data):
        if 'created_at' not in data:
            data['created_at'] = datetime.utcnow()
        super().__init__(**data)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "email": self.email,
            "verified": self.verified,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'User':
        if 'created_at' in data and isinstance(data['created_at'], str):
            data['created_at'] = datetime.fromisoformat(data['created_at'])
        return cls(**data)


class OTP(BaseModel):
    """OTP model for email verification"""
    code: str
    email: EmailStr
    expires_at: datetime
    attempts: int = 0
    
    @staticmethod
    def generate_code() -> str:
        """Generate a 6-digit OTP"""
        return ''.join([str(secrets.randbelow(10)) for _ in range(6)])
    
    @staticmethod
    def create(email: str, validity_minutes: int = 5) -> 'OTP':
        """Create a new OTP with expiration"""
        return OTP(
            code=OTP.generate_code(),
            email=email,
            expires_at=datetime.utcnow() + timedelta(minutes=validity_minutes),
            attempts=0
        )
    
    def is_valid(self) -> bool:
        """Check if OTP is still valid"""
        return datetime.utcnow() < self.expires_at and self.attempts < 3
    
    def verify(self, input_code: str) -> bool:
        """Verify the OTP code"""
        self.attempts += 1
        if not self.is_valid():
            return False
        return self.code == input_code
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "email": self.email,
            "expires_at": self.expires_at.isoformat(),
            "attempts": self.attempts
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'OTP':
        if 'expires_at' in data and isinstance(data['expires_at'], str):
            data['expires_at'] = datetime.fromisoformat(data['expires_at'])
        return cls(**data)


class Session(BaseModel):
    """Session model for JWT token management"""
    user_id: int
    email: EmailStr
    token: str
    expires_at: datetime
    
    @staticmethod
    def generate_token() -> str:
        """Generate a secure random token"""
        return secrets.token_urlsafe(32)
    
    @staticmethod
    def create(user_id: int, email: str, validity_hours: int = 24) -> 'Session':
        """Create a new session"""
        return Session(
            user_id=user_id,
            email=email,
            token=Session.generate_token(),
            expires_at=datetime.utcnow() + timedelta(hours=validity_hours)
        )
    
    def is_valid(self) -> bool:
        """Check if session is still valid"""
        return datetime.utcnow() < self.expires_at
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "email": self.email,
            "token": self.token,
            "expires_at": self.expires_at.isoformat()
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Session':
        if 'expires_at' in data and isinstance(data['expires_at'], str):
            data['expires_at'] = datetime.fromisoformat(data['expires_at'])
        return cls(**data)
