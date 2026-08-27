import os
import secrets
from urllib.parse import urlencode

from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    HTTPException,
    Query,
    Response,
)
from fastapi.responses import RedirectResponse
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
)
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.database.models import User
from app.schemas.schemas import (
    GoogleAuthRequest,
    UserResponse,
)
from app.services.auth_service import (
    create_access_token,
    decode_access_token,
    exchange_google_code,
    get_or_create_user,
    verify_google_token,
)


router = APIRouter(
    prefix="/auth",
    tags=["Auth"],
)

security = HTTPBearer(auto_error=False)

AUTH_COOKIE_NAME = "access_token"
GOOGLE_OAUTH_STATE_COOKIE = "google_oauth_state"

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI")
FRONTEND_URL = os.getenv(
    "FRONTEND_URL",
    "http://localhost:3000",
).rstrip("/")

IS_PRODUCTION = (
    os.getenv("ENVIRONMENT", "development").lower()
    == "production"
)

COOKIE_SECURE = IS_PRODUCTION
COOKIE_SAMESITE = "lax"


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(
        security
    ),
    access_token_cookie: str | None = Cookie(
        default=None,
        alias=AUTH_COOKIE_NAME,
    ),
    db: Session = Depends(get_db),
) -> User:
    token = (
        credentials.credentials
        if credentials
        else access_token_cookie
    )

    if not token:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
        )

    try:
        payload = decode_access_token(token)

    except ValueError as error:
        raise HTTPException(
            status_code=401,
            detail=str(error),
        )

    user_id = payload.get("sub")

    if (
        not isinstance(user_id, str)
        or not user_id.isdigit()
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid access token",
        )

    user = db.get(User, int(user_id))

    if not user:
        raise HTTPException(
            status_code=401,
            detail="User not found",
        )

    return user


@router.post("/google")
def login_with_google(
    data: GoogleAuthRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    try:
        google_user = verify_google_token(
            data.credential
        )

        user = get_or_create_user(
            db=db,
            google_user=google_user,
        )

        access_token = create_access_token(user)

        response.set_cookie(
            key=AUTH_COOKIE_NAME,
            value=access_token,
            httponly=True,
            secure=COOKIE_SECURE,
            samesite=COOKIE_SAMESITE,
            max_age=60 * 60 * 24 * 7,
            path="/",
        )

        return {
            "user":
                UserResponse.model_validate(
                    user
                )
        }

    except ValueError as error:
        raise HTTPException(
            status_code=401,
            detail=str(error),
        )


@router.get("/google/start")
def start_google_oauth():
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(
            status_code=500,
            detail="Google Client ID is not configured",
        )

    if not GOOGLE_REDIRECT_URI:
        raise HTTPException(
            status_code=500,
            detail="Google redirect URI is not configured",
        )

    state = secrets.token_urlsafe(32)

    query = urlencode(
        {
            "client_id": GOOGLE_CLIENT_ID,
            "redirect_uri": GOOGLE_REDIRECT_URI,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "prompt": "select_account",
            "include_granted_scopes": "true",
        }
    )

    response = RedirectResponse(
        url=(
            "https://accounts.google.com/"
            "o/oauth2/v2/auth?"
            f"{query}"
        ),
        status_code=302,
    )

    response.set_cookie(
        key=GOOGLE_OAUTH_STATE_COOKIE,
        value=state,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        max_age=600,
        path="/",
    )

    return response


@router.get("/google/callback")
def google_oauth_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    state_cookie: str | None = Cookie(
        default=None,
        alias=GOOGLE_OAUTH_STATE_COOKIE,
    ),
    db: Session = Depends(get_db),
):
    if error:
        return RedirectResponse(
            url=f"{FRONTEND_URL}/?google_login=cancelled",
            status_code=302,
        )

    if (
        not code
        or not state
        or not state_cookie
        or not secrets.compare_digest(
            state,
            state_cookie,
        )
    ):
        raise HTTPException(
            status_code=400,
            detail="Invalid Google OAuth state",
        )

    try:
        google_user = exchange_google_code(code)

        user = get_or_create_user(
            db=db,
            google_user=google_user,
        )

        access_token = create_access_token(user)

    except ValueError as error:
        raise HTTPException(
            status_code=401,
            detail=str(error),
        )

    response = RedirectResponse(
        url=f"{FRONTEND_URL}/chat",
        status_code=302,
    )

    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=access_token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        max_age=60 * 60 * 24 * 7,
        path="/",
    )

    response.delete_cookie(
        key=GOOGLE_OAUTH_STATE_COOKIE,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
    )

    return response


@router.get(
    "/me",
    response_model=UserResponse,
)
def get_me(
    current_user: User = Depends(
        get_current_user
    ),
):
    return current_user


@router.post("/logout")
def logout(
    response: Response,
):
    response.delete_cookie(
        key=AUTH_COOKIE_NAME,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        path="/",
    )

    return {
        "message":
            "Logged out successfully"
    }
