import os

from datetime import datetime, timedelta, timezone

import jwt
import requests as http_requests

from dotenv import load_dotenv
from google.auth.transport import requests
from google.oauth2 import id_token
from sqlalchemy.orm import Session

from app.database.models import User


load_dotenv()

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI")

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "10080"))
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
            f"Invalid Google credential: {error}"
        ) from error

    except Exception as error:
        raise ValueError(
            "Could not verify Google credential"
        ) from error

    google_sub = google_user.get("sub")
    email = google_user.get("email")

    if not google_sub:
        raise ValueError("Google account ID is missing")

    if not email:
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
        description = (
            payload.get("error_description")
            or payload.get("error")
            or "Google OAuth code exchange failed"
        )
        raise ValueError(str(description))

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
        minutes=JWT_EXPIRE_MINUTES
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
        return jwt.decode(
            token,
            JWT_SECRET_KEY,
            algorithms=[JWT_ALGORITHM],
        )

    except jwt.ExpiredSignatureError as error:
        raise ValueError(
            "Access token has expired"
        ) from error

    except jwt.InvalidTokenError as error:
        raise ValueError(
            "Invalid access token"
        ) from error
