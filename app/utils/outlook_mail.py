# app/utils/outlook_mail.py
import requests
from app.core.config import settings

# Auth setup - constants are computed lazily inside functions
# DO NOT initialize ConfidentialClientApplication at module level.
# When TENANT_ID is empty (e.g. missing env var on Cloud Run), it crashes the entire server.

SCOPES = ["https://graph.microsoft.com/.default"]
GRAPH_API_ENDPOINT = "https://graph.microsoft.com/v1.0"


def _get_msal_app():
    """Lazily create the MSAL client only when an email needs to be sent."""
    from msal import ConfidentialClientApplication
    if not settings.TENANT_ID or not settings.CLIENT_ID or not settings.CLIENT_SECRET:
        raise Exception("Email credentials (TENANT_ID, CLIENT_ID, CLIENT_SECRET) are not configured.")
    authority = f"https://login.microsoftonline.com/{settings.TENANT_ID}"
    return ConfidentialClientApplication(
        settings.CLIENT_ID,
        authority=authority,
        client_credential=settings.CLIENT_SECRET,
    )


def get_access_token():
    msal_app = _get_msal_app()
    token = msal_app.acquire_token_for_client(scopes=SCOPES)
    if "access_token" in token:
        return token["access_token"]
    raise Exception(f"Could not acquire token: {token}")


def send_otp_email(recipient_email: str, otp: str):
    try:
        access_token = get_access_token()
    except Exception as e:
        print(f"[Email] Skipping OTP email - credentials not configured: {e}")
        return

    message = {
        "message": {
            "subject": "Your OTP Code",
            "body": {
                "contentType": "Text",
                "content": f"Your OTP is {otp}. It will expire in 10 minutes."
            },
            "toRecipients": [
                {"emailAddress": {"address": recipient_email}}
            ]
        },
        "saveToSentItems": "true"
    }

    response = requests.post(
        f"{GRAPH_API_ENDPOINT}/users/{settings.SENDER_EMAIL}/sendMail",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        },
        json=message
    )

    if response.status_code != 202:
        raise Exception(f"Failed to send email: {response.status_code} {response.text}")
