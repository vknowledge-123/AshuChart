# app/middleware.py
from fastapi import Request, HTTPException, status
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)

class SecurityHeadersMiddleware:
    """
    Middleware to add security headers to every response.
    Protects against XSS, Clickjacking, MIME-sniffing, etc.
    """
    def __init__(self, app, csp_header: str):
        self.app = app
        self.csp_header = csp_header

    async def __call__(self, scope: Dict[str, Any], receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                
                # Add Security Headers
                security_headers = {
                    "X-Frame-Options": "DENY",
                    "X-Content-Type-Options": "nosniff",
                    "X-XSS-Protection": "1; mode=block",
                    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
                    "Content-Security-Policy": self.csp_header,
                    "Referrer-Policy": "strict-origin-when-cross-origin",
                }
                # Remove existing security headers if any to avoid duplicates
                existing_keys = {k.decode("latin-1").lower() for k, v in headers}
                for key, value in security_headers.items():
                    if key.lower() not in existing_keys:
                        headers.append((key.encode("latin-1"), value.encode("latin-1")))
                
                message["headers"] = headers
            
            await send(message)

        await self.app(scope, receive, send_wrapper)

class CloudflareProxyMiddleware:
    """
    Optional: Verifies that the request came from Cloudflare.
    If using Cloudflare Zero Trust, you might check for 'CF-Access-Jwt-Assertion'.
    Here we can check CF-Connecting-IP presence as a basic check if configured.
    """
    def __init__(self, app):
        self.app = app
    
    async def __call__(self, scope: Dict[str, Any], receive, send):
        # Implementation of strict Cloudflare checks can go here
        # For now, we pass through, as TrustedHost is the primary IP defense.
        return await self.app(scope, receive, send)


class AuthMiddleware:
    """Middleware to verify authentication tokens"""
    
    def __init__(self, store):
        self.store = store
    
    async def __call__(self, request: Request) -> Optional[Dict[str, Any]]:
        """
        Bypass authentication checks as Cloudflare Zero Trust handles security.
        Defaults to user_id=1.
        """
        # We assume the request is authenticated by Cloudflare Zero Trust.
        # We can optionally log the user email from headers if available.
        cf_email = request.headers.get("Cf-Access-Authenticated-User-Email", "admin@localhost")
        
        # logger.info(f"Access by: {cf_email}")

        return {
            "user_id": 1,  # Default User
            "email": cf_email,
            "token": "cloudflare-managed"
        }


async def get_current_user(request: Request, store) -> Dict[str, Any]:
    """
    Dependency function to get current authenticated user
    
    Usage in route:
        @app.get("/api/protected")
        async def protected(user: dict = Depends(lambda r: get_current_user(r, store))):
            user_id = user["user_id"]
    """
    middleware = AuthMiddleware(store)
    return await middleware(request)
