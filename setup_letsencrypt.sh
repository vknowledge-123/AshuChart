#!/bin/bash
#
# Automated Let's Encrypt SSL Setup for Utho Cloud Server
# This script installs SSL certificates and configures nginx for HTTPS
#
# Usage: bash setup_letsencrypt.sh your-domain.com your@email.com
#

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "=========================================="
echo "  Let's Encrypt SSL Setup for Utho Cloud"
echo "=========================================="
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}❌ Please run as root (use sudo)${NC}"
  exit 1
fi

# Get domain and email
DOMAIN=$1
EMAIL=$2

if [ -z "$DOMAIN" ]; then
  read -p "Enter your domain name (e.g., trading.yourdomain.com): " DOMAIN
fi

if [ -z "$EMAIL" ]; then
  read -p "Enter your email address: " EMAIL
fi

echo -e "${GREEN}Domain: $DOMAIN${NC}"
echo -e "${GREEN}Email: $EMAIL${NC}"
echo ""

# Confirm
read -p "Continue with this configuration? (y/N): " CONFIRM
if [ "$CONFIRM" != "y" ] && [ "$CONFIRM" != "Y" ]; then
  echo "Aborted."
  exit 0
fi

# Step 1: Update packages
echo -e "${YELLOW}[1/7] Updating package list...${NC}"
apt update -qq

# Step 2: Install certbot and nginx
echo -e "${YELLOW}[2/7] Installing Certbot and nginx...${NC}"
apt install -y certbot nginx python3-certbot-nginx

# Step 3: Create nginx configuration
echo -e "${YELLOW}[3/7] Creating nginx configuration...${NC}"
NGINX_CONF="/etc/nginx/sites-available/trading"

cat > $NGINX_CONF << 'EOF'
# HTTP server - redirect to HTTPS
server {
    listen 80;
    server_name DOMAIN_PLACEHOLDER;
    
    # Let's Encrypt validation
    location /.well-known/acme-challenge/ {root /var/www/html;
    }
    
    # Redirect all other traffic to HTTPS
    location / {
        return 301 https://$server_name$request_uri;
    }
}

# HTTPS server
server {
    listen 443 ssl http2;
    server_name DOMAIN_PLACEHOLDER;

    # SSL certificate paths (will be configured by certbot)
    ssl_certificate /etc/letsencrypt/live/DOMAIN_PLACEHOLDER/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/DOMAIN_PLACEHOLDER/privkey.pem;
    
    # SSL settings
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers 'ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384';
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;

    # Security headers
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;

    # Proxy to FastAPI application
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # WebSocket support
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        
        # Timeouts
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }

    # Logging
    access_log /var/log/nginx/trading_access.log;
    error_log /var/log/nginx/trading_error.log;
}
EOF

# Replace domain placeholder
sed -i "s/DOMAIN_PLACEHOLDER/$DOMAIN/g" $NGINX_CONF

# Enable site
ln -sf $NGINX_CONF /etc/nginx/sites-enabled/trading

# Disable default site if exists
rm -f /etc/nginx/sites-enabled/default

echo -e "${GREEN}✅ nginx configuration created${NC}"

# Step 4: Test nginx configuration
echo -e "${YELLOW}[4/7] Testing nginx configuration...${NC}"
nginx -t

# Step 5: Obtain SSL certificate
echo -e "${YELLOW}[5/7] Obtaining SSL certificate from Let's Encrypt...${NC}"
echo -e "${YELLOW}This may take a minute...${NC}"

# Stop nginx temporarily for standalone mode
systemctl stop nginx

# Obtain certificate in standalone mode
certbot certonly \
  --standalone \
  --non-interactive \
  --agree-tos \
  --email "$EMAIL" \
  -d "$DOMAIN"

echo -e "${GREEN}✅ SSL certificate obtained${NC}"

# Step 6: Start nginx
echo -e "${YELLOW}[6/7] Starting nginx...${NC}"
systemctl start nginx
systemctl enable nginx

# Step 7: Setup auto-renewal
echo -e "${YELLOW}[7/7] Setting up auto-renewal...${NC}"

# Test renewal (dry run)
certbot renew --dry-run

echo -e "${GREEN}✅ Auto-renewal configured${NC}"

echo ""
echo "=========================================="
echo -e "${GREEN}  SSL Setup Complete!${NC}"
echo "=========================================="
echo ""
echo "✅ SSL certificate installed for: $DOMAIN"
echo "✅ nginx configured and running"
echo "✅ Auto-renewal enabled"
echo ""
echo "📝 Next Steps:"
echo "   1. Start your application on port 8000:"
echo "      cd /path/to/your/app"
echo "      python -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
echo ""
echo "   2. Test your site:"
echo "      https://$DOMAIN"
echo ""
echo "   3. Configure Zerodha webhook:"
echo "      https://$DOMAIN/webhook/chartink?user_id=1"
echo ""
echo "   4. Monitor nginx logs:"
echo "      tail -f /var/log/nginx/trading_access.log"
echo "      tail -f /var/log/nginx/trading_error.log"
echo ""
echo "⚠️  IMPORTANT:"
echo "   - Make sure your application is running on port 8000"
echo "   - Certificates will auto-renew (check: sudo systemctl status certbot.timer)"
echo "   - nginx restart: sudo systemctl restart nginx"
echo ""
