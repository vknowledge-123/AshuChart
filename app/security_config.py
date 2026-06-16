import os

# -----------------------------
# Security Configuration
# -----------------------------

# ALLOWED_HOSTS
# Defines the hostnames that this server is allowed to serve.
# To block direct IP access, set this to your domain name(s) only.
# Example: ["your-domain.com", "api.your-domain.com", "localhost"]
# Default to ["*"] if not set to avoid breaking the app immediately, 
# but User should configure this in .env.
allowed_hosts_env = os.getenv("ALLOWED_HOSTS", "*")
ALLOWED_HOSTS = [host.strip() for host in allowed_hosts_env.split(",") if host.strip()]

# RATE LIMITING
# Rate limits for various endpoints
RATE_LIMIT_AUTH_OTP = "5/5minute"     # 5 requests per 5 minutes
RATE_LIMIT_AUTH_VERIFY = "10/minute"  # 10 requests per minute
RATE_LIMIT_LOGIN = "10/minute"        # 10 requests per minute
RATE_LIMIT_GLOBAL = "100/minute"      # Global fallback

# SECURITY HEADERS Configuration
# Strict-Transport-Security
STS_MAX_AGE = 31536000 # 1 year
STS_INCLUDE_SUBDOMAINS = True

# Content Security Policy (CSP)
# A strict baseline. Modify if you use external CDNs (Bootstrap, jQuery, etc.)
CSP_POLICY = {
    "default-src": "'self'",
    "script-src": "'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net https://code.jquery.com https://cdnjs.cloudflare.com https://cdn.tailwindcss.com",
    "style-src": "'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com https://cdnjs.cloudflare.com",
    "img-src": "'self' data: https:",
    "connect-src": "'self' wss: ws: https:",
    "font-src": "'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com data:",
    "frame-ancestors": "'none'", # Clickjacking protection
    "object-src": "'none'",
}

def get_csp_header_value():
    policy_parts = []
    for directive, sources in CSP_POLICY.items():
        policy_parts.append(f"{directive} {sources}")
    return "; ".join(policy_parts)
