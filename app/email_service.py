# app/email_service.py
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
import logging
from dotenv import load_dotenv
from app.crypto import EncryptionManager

# Load environment variables immediately
load_dotenv()

logger = logging.getLogger(__name__)


class EmailService:
    """Email service for sending OTP verification emails"""
    
    def __init__(self, user_subdomain: Optional[str] = None):
        """
        Initialize email service.
        decryption enabled for secure password storage.
        All settings loaded strictly from environment variables.
        """
        # Load settings from environment variables
        self.smtp_host = os.getenv("SMTP_HOST", "")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = os.getenv("SMTP_USER", "")
        
        # Secure Password Handling
        raw_password = os.getenv("SMTP_PASSWORD", "")
        try:
            # Attempt to decrypt password if it's encrypted
            manager = EncryptionManager()
            # decrypt() returns original string if decryption fails (e.g. if plain text)
            self.smtp_password = manager.decrypt(raw_password)
        except Exception:
            # Fallback to raw password
            self.smtp_password = raw_password
            
        self.smtp_from = os.getenv("SMTP_FROM", "")
        self.app_name = os.getenv("APP_NAME", "AlgoEdge Trading")
        
    def send_otp(self, to_email: str, otp_code: str) -> bool:
        """
        Send OTP verification email
        
        Args:
            to_email: Recipient email address
            otp_code: 6-digit OTP code
            
        Returns:
            True if email sent successfully, False otherwise
        """
        if not self.smtp_user or not self.smtp_password:
            logger.error("SMTP credentials not configured")
            return False
        
        try:
            # Create message
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f'Your {self.app_name} Verification Code'
            msg['From'] = self.smtp_from
            msg['To'] = to_email
            
            # Create HTML content
            html_content = self._create_otp_email_html(otp_code)
            
            # Attach HTML part
            html_part = MIMEText(html_content, 'html')
            msg.attach(html_part)
            
            # Send email
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                server.send_message(msg)
            logger.info(f"\033[92m ✉️  OTP email sent successfully to {to_email}\033[0m")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send OTP email to {to_email}: {e}")
            return False
    
    def _create_otp_email_html(self, otp_code: str) -> str:
        """Create premium HTML email template for OTP"""
        return f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
        
        body {{
            margin: 0;
            padding: 0;
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 50%, #0f172a 100%);
            min-height: 100vh;
            padding: 40px 20px;
        }}
        .container {{
            max-width: 560px;
            margin: 0 auto;
            background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
            border-radius: 24px;
            overflow: hidden;
            box-shadow: 0 25px 80px rgba(0,0,0,0.4), 0 0 1px rgba(0,0,0,0.1);
        }}
        .header {{
            background: linear-gradient(135deg, #0ea5e9 0%, #2563eb 50%, #6366f1 100%);
            padding: 48px 40px;
            text-align: center;
            position: relative;
            overflow: hidden;
        }}
        .header::before {{
            content: '';
            position: absolute;
            top: -50%;
            left: -50%;
            width: 200%;
            height: 200%;
            background: radial-gradient(circle, rgba(255,255,255,0.1) 0%, transparent 70%);
            animation: shimmer 3s ease-in-out infinite;
        }}
        @keyframes shimmer {{
            0%, 100% {{ transform: translate(-25%, -25%) rotate(0deg); }}
            50% {{ transform: translate(-25%, -25%) rotate(180deg); }}
        }}
        .logo {{
            width: 72px;
            height: 72px;
            background: linear-gradient(135deg, #ffffff 0%, #e0f2fe 100%);
            border-radius: 20px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            margin-bottom: 16px;
            font-size: 40px;
            font-weight: 800;
            background-clip: text;
            -webkit-background-clip: text;
            color: #0369a1;
            box-shadow: 0 8px 32px rgba(0,0,0,0.15);
            position: relative;
            z-index: 1;
        }}
        .header h1 {{
            margin: 0;
            color: white;
            font-size: 32px;
            font-weight: 800;
            letter-spacing: -1px;
            position: relative;
            z-index: 1;
            text-shadow: 0 2px 8px rgba(0,0,0,0.2);
        }}
        .header p {{
            margin: 8px 0 0 0;
            color: rgba(255,255,255,0.9);
            font-size: 14px;
            font-weight: 500;
            letter-spacing: 0.5px;
            text-transform: uppercase;
            position: relative;
            z-index: 1;
        }}
        .content {{
            padding: 48px 40px;
        }}
        .greeting {{
            font-size: 18px;
            font-weight: 600;
            color: #0f172a;
            margin: 0 0 16px 0;
        }}
        .text {{
            color: #475569;
            line-height: 1.7;
            font-size: 16px;
            margin: 0 0 24px 0;
        }}
        .otp-box {{
            background: linear-gradient(135deg, #f0f9ff 0%, #e0f2fe 100%);
            border: 3px solid #0ea5e9;
            border-radius: 20px;
            padding: 40px 30px;
            text-align: center;
            margin: 32px 0;
            box-shadow: 0 4px 24px rgba(14,165,233,0.15), inset 0 1px 0 rgba(255,255,255,0.5);
        }}
        .otp-label {{
            margin: 0 0 12px 0;
            color: #0369a1;
            font-size: 13px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 1.5px;
        }}
        .otp-code {{
            font-size: 48px;
            font-weight: 900;
            color: #0c4a6e;
            letter-spacing: 12px;
            font-family: 'Courier New', monospace;
            margin: 16px 0;
            text-shadow: 0 2px 4px rgba(0,0,0,0.1);
            background: linear-gradient(135deg, #0369a1 0%, #0c4a6e 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }}
        .otp-validity {{
            margin: 12px 0 0 0;
            color: #64748b;
            font-size: 14px;
            font-weight: 600;
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }}
        .time-icon {{
            display: inline-block;
            width: 16px;
            height: 16px;
            background: #64748b;
            border-radius: 50%;
            position: relative;
        }}
        .time-icon::before {{
            content: '';
            position: absolute;
            top: 50%;
            left: 50%;
            width: 2px;
            height: 6px;
            background: white;
            transform: translate(-50%, -100%);
        }}
        .time-icon::after {{
            content: '';
            position: absolute;
            top: 50%;
            left: 50%;
            width: 6px;
            height: 2px;
            background: white;
            transform: translate(-50%, -50%);
        }}
        .warning {{
            background: linear-gradient(135deg, #fef3c7 0%, #fde68a 100%);
            border-left: 5px solid #f59e0b;
            padding: 20px 24px;
            margin: 32px 0;
            border-radius: 12px;
            box-shadow: 0 4px 16px rgba(245,158,11,0.15);
        }}
        .warning p {{
            margin: 0;
            color: #78350f;
            font-size: 14px;
            line-height: 1.6;
            font-weight: 500;
        }}
        .warning strong {{
            display: block;
            font-size: 15px;
            font-weight: 700;
            margin-bottom: 6px;
            color: #92400e;
        }}
        .footer {{
            background: linear-gradient(180deg, #f1f5f9 0%, #e2e8f0 100%);
            padding: 32px 40px;
            text-align: center;
            border-top: 1px solid #cbd5e1;
        }}
        .footer-brand {{
            font-size: 18px;
            font-weight: 800;
            background: linear-gradient(135deg, #0ea5e9 0%, #6366f1 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            margin: 0 0 4px 0;
        }}
        .footer-tagline {{
            margin: 4px 0 20px 0;
            color: #64748b;
            font-size: 13px;
            font-weight: 600;
        }}
        .footer-copyright {{
            margin: 0;
            color: #94a3b8;
            font-size: 12px;
            font-weight: 500;
        }}
        .divider {{
            height: 1px;
            background: linear-gradient(90deg, transparent 0%, #cbd5e1 50%, transparent 100%);
            margin: 24px 0;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="logo">🔐</div>
            <h1>{self.app_name}</h1>
            <p>Secure Authentication</p>
        </div>
        
        <div class="content">
            <p class="greeting">Hello Trader! 👋</p>
            
            <p class="text">
                You requested secure access to your trading dashboard. Use the verification code below to complete your login and start trading.
            </p>
            
            <div class="otp-box">
                <p class="otp-label">Your Verification Code</p>
                <div class="otp-code">{otp_code}</div>
                <p class="otp-validity">
                    <span class="time-icon"></span>
                    Valid for 5 minutes
                </p>
            </div>
            
            <p class="text">
                Enter this code on the login page to access your dashboard and manage your trades.
            </p>
            
            <div class="warning">
                <strong>⚠️ Security Notice</strong>
                <p>Never share this code with anyone. Our team will never ask for your verification code via email, phone, or any other channel.</p>
            </div>
            
            <div class="divider"></div>
            
            <p class="text" style="font-size: 14px; color: #64748b;">
                <strong>Didn't request this code?</strong><br>
                If you didn't request this verification code, you can safely ignore this email. Your account security has not been compromised.
            </p>
        </div>
        
        <div class="footer">
            <p class="footer-brand">{self.app_name}</p>
            <p class="footer-tagline">Professional Trading Platform</p>
            <p class="footer-copyright">© 2026 All Rights Reserved • Secure & Encrypted</p>
        </div>
    </div>
</body>
</html>
"""



# Global instance
email_service = EmailService()
