import os
import re

from datetime import datetime, timedelta, timezone

import jwt
import requests as http_requests

from dotenv import load_dotenv
from google.auth.transport import requests
from google.oauth2 import id_token
from sqlalchemy.orm import Session

from app.database.models import User
from app.services.auth_config import auth_settings


load_dotenv()

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
# Loading the shared policy validates configuration at API/worker import.
GOOGLE_REDIRECT_URI = auth_settings().google_redirect_uri

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
GOOGLE_CLOCK_SKEW_SECONDS = int(
    os.getenv("GOOGLE_CLOCK_SKEW_SECONDS", "10")
)

if not GOOGLE_CLIENT_ID:
    raise RuntimeError("GOOGLE_CLIENT_ID is not set")

if not JWT_SECRET_KEY:
    raise RuntimeError("JWT_SECRET_KEY is not set")


def verify_google_token(
    credential: str,
) -> dict:
    try:
        google_user = id_token.verify_oauth2_token(
            credential,
            requests.Request(),
            GOOGLE_CLIENT_ID,
            clock_skew_in_seconds=GOOGLE_CLOCK_SKEW_SECONDS,
        )

    except ValueError as error:
        raise ValueError(
            "Invalid Google credential"
        ) from error

    except Exception as error:
        raise ValueError(
            "Could not verify Google credential"
        ) from error

    google_sub = google_user.get("sub")
    email = google_user.get("email")

    if not isinstance(google_sub, str) or not google_sub.strip():
        raise ValueError("Google account ID is missing")

    if not isinstance(email, str) or not email.strip():
        raise ValueError("Google account email is missing")

    if google_user.get("email_verified") is False:
        raise ValueError("Google account email is not verified")

    return google_user


def exchange_google_code(
    code: str,
) -> dict:
    if not GOOGLE_CLIENT_SECRET:
        raise RuntimeError(
            "GOOGLE_CLIENT_SECRET is not set"
        )

    if not GOOGLE_REDIRECT_URI:
        raise RuntimeError(
            "GOOGLE_REDIRECT_URI is not set"
        )

    try:
        response = http_requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": GOOGLE_REDIRECT_URI,
                "grant_type": "authorization_code",
            },
            timeout=20,
        )

    except http_requests.RequestException as error:
        raise ValueError(
            "Could not contact Google OAuth"
        ) from error

    try:
        payload = response.json()

    except ValueError as error:
        raise ValueError(
            "Google OAuth returned an invalid response"
        ) from error

    if not response.ok:
        raise ValueError("Google OAuth code exchange failed")

    credential = payload.get("id_token")

    if not isinstance(credential, str) or not credential.strip():
        raise ValueError(
            "Google OAuth did not return an ID token"
        )

    return verify_google_token(credential)


def get_or_create_user(
    db: Session,
    google_user: dict,
) -> User:
    google_sub = google_user["sub"]
    email = google_user["email"]

    user = (
        db.query(User)
        .filter(User.google_sub == google_sub)
        .first()
    )

    if user:
        user.email = email
        user.name = google_user.get("name")
        user.picture = google_user.get("picture")

        db.commit()
        db.refresh(user)

        return user

    user = User(
        google_sub=google_sub,
        email=email,
        name=google_user.get("name"),
        picture=google_user.get("picture"),
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    return user


def create_access_token(
    user: User,
) -> str:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(
        seconds=auth_settings().session_seconds
    )

    payload = {
        "sub": str(user.id),
        "email": user.email,
        "iat": now,
        "exp": expires_at,
    }

    return jwt.encode(
        payload,
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )


def decode_access_token(
    token: str,
) -> dict:
    try:
        payload = jwt.decode(
            token,
            JWT_SECRET_KEY,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
        subject = payload["sub"]
        if (not isinstance(subject, str) or not re.fullmatch(r"[1-9][0-9]{0,9}", subject)
                or int(subject) >= 2**31 or type(payload["exp"]) is not int
                or ("iat" in payload and type(payload["iat"]) is not int)):
            raise ValueError("Invalid access token")
        return payload

    except jwt.ExpiredSignatureError as error:
        raise ValueError(
            "Access token has expired"
        ) from error

    except (jwt.InvalidTokenError, ValueError, TypeError, OverflowError) as error:
        raise ValueError(
            "Invalid access token"
        ) from error
