import logging
import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from routing.config import BRAND_NAME, SUPPORT_EMAIL

logger = logging.getLogger("chiki_webhook")

# SECURITY FIX: Moved credentials to environment variables via routing/config.py
# All SMTP settings are now loaded from .env file
from routing.config import (
    SMTP_HOST,
    SMTP_PORT,
    SMTP_USERNAME,
    SMTP_PASSWORD,
)

# Fallback defaults (in case config is not loaded)
SMTP_HOST = SMTP_HOST or "smtp.gmail.com"
SMTP_PORT = SMTP_PORT or 587
FROM_EMAIL = SUPPORT_EMAIL or "noreply@example.com"


def is_smtp_configured() -> bool:
    return bool(SMTP_USERNAME and SMTP_PASSWORD)


def send_otp_email(to_email: str, otp: str, expires_minutes: int) -> bool:
    if not is_smtp_configured():
        logger.warning("SMTP not configured. OTP email not sent.")
        return False

    subject = f"{BRAND_NAME} - Password Reset OTP"
    body = (
        f"Your password reset OTP is: {otp}\n\n"
        f"This OTP will expire in {expires_minutes} minutes.\n\n"
        f"If you did not request this, please ignore this email.\n\n"
        f"- {BRAND_NAME} Team"
    )

    msg = MIMEMultipart()
    msg["From"] = FROM_EMAIL
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(FROM_EMAIL, to_email, msg.as_string())
        logger.info(f"OTP email sent to {to_email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send OTP email to {to_email}: {e}")
        return False
