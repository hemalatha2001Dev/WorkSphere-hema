# app/utils/outlook_mail.py
# Replaced Microsoft MSAL / Entra ID with Gmail SMTP (App Password)
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from app.core.config import settings


def send_otp_email(recipient_email: str, otp: str):
    """Send OTP email via Gmail SMTP using App Password."""
    if not settings.SENDER_EMAIL or not settings.GMAIL_APP_PASSWORD:
        print("[Email] Gmail credentials not configured. Skipping OTP email.")
        return

    subject = "Your OTP Code - WorkSphere"
    body = f"""
Hello,

Your OTP code for WorkSphere is:

    {otp}

This OTP is valid for 10 minutes. Do not share it with anyone.

If you did not request this, please ignore this email.

Regards,
WorkSphere Team
    """

    msg = MIMEMultipart()
    msg["From"] = settings.SENDER_EMAIL
    msg["To"] = recipient_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(settings.SENDER_EMAIL, settings.GMAIL_APP_PASSWORD)
            server.sendmail(settings.SENDER_EMAIL, recipient_email, msg.as_string())
        print(f"[Email] OTP sent successfully to {recipient_email}")
    except Exception as e:
        print(f"[Email] Failed to send OTP email: {e}")
        raise Exception(f"Failed to send OTP email: {e}")
