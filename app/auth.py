# app/auth.py
from typing import Optional, Dict, Any
from datetime import datetime
import logging
from .models import User, OTP, Session
from .redis_store import RedisStore
from .email_service import email_service

logger = logging.getLogger(__name__)


class AuthService:
    """Authentication service for user management"""
    
    def __init__(self, store: RedisStore):
        self.store = store
    
    async def register_or_login(self, email: str) -> Dict[str, Any]:
        """
        Register new user or initiate login for existing user
        
        Args:
            email: User's email address
            
        Returns:
            Dictionary with status and message
        """
        # Check if user exists
        user = await self.store.get_user_by_email(email)
        
        if not user:
            # Create new user (unverified)
            user = User(email=email, verified=False)
            await self.store.save_user(user)
            logger.info("\033[92m👤 NEW USER REGISTERED │ EMAIL=%s\033[0m",email)
        
        # Generate and send OTP
        otp = OTP.create(email)
        await self.store.save_otp(email, otp)
        
        # Send email
        email_sent = email_service.send_otp(email, otp.code)
        
        if email_sent:
            logger.info("\033[93m📩 OTP SENT │ EMAIL=%s\033[0m", email) # For debugging - remove in production
            return {
                "status": "success",
                "message": "Verification code sent to your email",
                "email": email
            }
        else:
            # If email fails, return OTP in response for development
            return {
                "status": "success",
                "message": "Email service unavailable. Use this code for testing:",
                "email": email,
                "otp_code": otp.code  # Only for development!
            }
    
    async def verify_otp_and_login(self, email: str, otp_code: str) -> Optional[Dict[str, Any]]:
        """
        Verify OTP and create session
        
        Args:
            email: User's email address
            otp_code: 6-digit OTP code
            
        Returns:
            Session data with token if successful, None otherwise
        """
        # Get OTP from store
        otp = await self.store.get_otp(email)
        
        if not otp:
            logger.warning(f"OTP not found for {email}")
            return None
        
        # Verify OTP
        if not otp.verify(otp_code):
            # Update attempts
            await self.store.save_otp(email, otp)
            logger.warning(f"Invalid OTP for {email} (attempts: {otp.attempts})")
            return None
        
        # OTP verified - delete it
        await self.store.delete_otp(email)
        
        # Get or create user
        user = await self.store.get_user_by_email(email)
        if not user:
            user = User(email=email, verified=True)
            await self.store.save_user(user)
        else:
            # Mark as verified
            user.verified = True
            await self.store.save_user(user)
        
        # Get user_id (email hash-based for uniqueness)
        user_id = await self.store.get_user_id_by_email(email)
        
        # Create session
        session = Session.create(user_id, email)
        await self.store.save_session(session.token, session)
        logger.info( "\033[96m"
        "\n============================================================\n"
        "🔐 USER LOGIN SUCCESSFULLY \n"
        "▸ USER_ID : %s\n"
        "▸ EMAIL   : %s\n"
        "============================================================"
        "\033[0m",
        user_id, email
    )
        return {
            "token": session.token,
            "user_id": user_id,
            "email": email,
            "expires_at": session.expires_at.isoformat()
        }
    
    async def verify_session(self, token: str) -> Optional[Dict[str, Any]]:
        """
        Verify session token
        
        Args:
            token: Session token
            
        Returns:
            User info if session is valid, None otherwise
        """
        session = await self.store.get_session(token)
        
        if not session:
            return None
        
        if not session.is_valid():
            await self.store.delete_session(token)
            return None
        
        return {
            "user_id": session.user_id,
            "email": session.email
        }
    
    async def logout(self, token: str) -> bool:
        """
        Logout user by invalidating session
        
        Args:
            token: Session token
            
        Returns:
            True if logout successful
        """
        return await self.store.delete_session(token)
    
    async def check_rate_limit(self, email: str) -> bool:
        """
        Check if user has exceeded OTP request rate limit
        
        Args:
            email: User's email
            
        Returns:
            True if within limit, False if exceeded
        """
        return await self.store.check_otp_rate_limit(email)
