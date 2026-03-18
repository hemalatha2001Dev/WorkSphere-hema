import requests
from app.core.config import settings
from fastapi import HTTPException

def get_access_token():
    url = f"https://login.microsoftonline.com/{settings.TENANT_ID}/oauth2/v2.0/token"
    data = {
        "grant_type": "client_credentials",
        "client_id": settings.CLIENT_ID,
        "client_secret": settings.CLIENT_SECRET,
        "scope": "https://graph.microsoft.com/.default"
    }
    response = requests.post(url, data=data)
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="Failed to get access token")
    return response.json()["access_token"]

def send_user_credentials_email(to_email: str, username: str, password: str):
    access_token = get_access_token()
    email_msg = {
        "message": {
            "subject": "Your Account Credentials",
            "body": {
                "contentType": "HTML",
                "content": f"""
                <p>Hello,</p>
                <p>Your account has been created successfully.</p>
                <p><b>Username:</b> {username}</p>
                <p><b>Password:</b> {password}</p>
                <p>Please login and change your password.</p>
                """
            },
            "toRecipients": [
                {"emailAddress": {"address": to_email}}
            ]
        }
    }

    send_url = f"https://graph.microsoft.com/v1.0/users/{settings.SENDER_EMAIL}/sendMail"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    response = requests.post(send_url, headers=headers, json=email_msg)
    if response.status_code not in (200, 202):
        raise HTTPException(status_code=500, detail="Failed to send credentials email")
