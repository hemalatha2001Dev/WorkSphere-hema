# app/utils/outlook_mail.py
import requests
from msal import ConfidentialClientApplication
from app.core.config import settings

# Auth setup
AUTHORITY = f"https://login.microsoftonline.com/{settings.TENANT_ID}"
SCOPES = ["https://graph.microsoft.com/.default"]
GRAPH_API_ENDPOINT = "https://graph.microsoft.com/v1.0"

# MSAL client
msal_app = ConfidentialClientApplication(
    settings.CLIENT_ID,
    authority=AUTHORITY,
    client_credential=settings.CLIENT_SECRET,
)

def get_access_token():
    token = msal_app.acquire_token_for_client(scopes=SCOPES)
    if "access_token" in token:
        return token["access_token"]
    raise Exception(f"Could not acquire token: {token}")

def send_otp_email(recipient_email: str, otp: str):
    access_token = get_access_token()

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
