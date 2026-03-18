# app/utils/send_email.py
# Replaced Microsoft Graph API with Gmail SMTP (App Password)
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from app.core.config import settings


def send_user_credentials_email(to_email: str, username: str, password: str):
    """Send user account credentials via Gmail SMTP using App Password."""
    if not settings.SENDER_EMAIL or not settings.GMAIL_APP_PASSWORD:
        print("[Email] Gmail credentials not configured. Skipping credentials email.")
        return

    subject = "Your WorkSphere Account Credentials"
    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; color: #333;">
        <h2 style="color: #4A90D9;">Welcome to WorkSphere!</h2>
        <p>Hello,</p>
        <p>Your account has been created successfully. Here are your login credentials:</p>
        <table style="border-collapse: collapse; margin: 16px 0;">
            <tr>
                <td style="padding: 8px 16px; background: #f5f5f5; border: 1px solid #ddd;"><b>Username</b></td>
                <td style="padding: 8px 16px; border: 1px solid #ddd;">{username}</td>
            </tr>
            <tr>
                <td style="padding: 8px 16px; background: #f5f5f5; border: 1px solid #ddd;"><b>Password</b></td>
                <td style="padding: 8px 16px; border: 1px solid #ddd;">{password}</td>
            </tr>
        </table>
        <p style="color: #e05c5c;"><b>Please login and change your password immediately.</b></p>
        <br/>
        <p>Regards,<br/>WorkSphere Team</p>
    </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["From"] = settings.SENDER_EMAIL
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html"))

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(settings.SENDER_EMAIL, settings.GMAIL_APP_PASSWORD)
            server.sendmail(settings.SENDER_EMAIL, to_email, msg.as_string())
        print(f"[Email] Credentials sent successfully to {to_email}")
    except Exception as e:
        print(f"[Email] Failed to send credentials email: {e}")
        # Don't crash the registration — just log the failure
