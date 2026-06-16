# app/custom_middleware.py
"""
Selective Host Validation Middleware

- Bypasses host checking for webhook endpoints (allows Chartink/external services)
  while still validating user-facing endpoints (dashboard, API).
"""

from __future__ import annotations

import logging
from typing import List, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse

logger = logging.getLogger(__name__)


class SelectiveHostMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, allowed_hosts: List[str], bypass_paths: Optional[List[str]] = None):
        super().__init__(app)
        self.allowed_hosts = allowed_hosts or ["*"]
        self.bypass_paths = bypass_paths or []

    async def dispatch(self, request, call_next):
        # Bypass host validation for selected paths (e.g. webhooks).
        for bypass_path in self.bypass_paths:
            if request.url.path.startswith(bypass_path):
                return await call_next(request)

        # Extract host from Host header (strip port if present).
        host_header = request.headers.get("host", "")
        host = host_header.split(":")[0] if host_header else ""

        # Wildcard allow
        if "*" in self.allowed_hosts:
            return await call_next(request)

        if host in self.allowed_hosts:
            return await call_next(request)

        logger.warning("Blocked invalid host header=%s path=%s", host_header, request.url.path)
        return PlainTextResponse(f"Invalid host header: {host_header}", status_code=400)

