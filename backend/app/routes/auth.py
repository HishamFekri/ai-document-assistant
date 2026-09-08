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
    Request,
)
from fastapi.responses import JSONResponse, RedirectResponse
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

from app.services.resource_admission import (
    authentication_subject, consume_rate, consume_user_rate,
)
from app.services.resource_limits import request_policy
from app.services.auth_config import (
    AUTH_COOKIE_NAME, GOOGLE_OAUTH_STATE_COOKIE, auth_settings,
    set_auth_cookie, delete_auth_cookie, set_state_cookie, delete_state_cookie,
)


def limit_authentication(request: Request):
    consume_rate(authentication_subject(request), "auth")


router = APIRouter(
    prefix="/auth",
    tags=["Auth"],
)

security = HTTPBearer(auto_error=False)

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_REDIRECT_URI = auth_settings().google_redirect_uri
FRONTEND_URL = auth_settings().frontend_url


def get_current_user(
    request: Request,
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

    except ValueError:
        raise HTTPException(
            status_code=401,
            detail="Invalid access token",
        ) from None

    user_id = payload.get("sub")

    if (
        not isinstance(user_id, str)
        or not user_id.isdigit()
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid access token",
        )

    consume_user_rate(int(user_id), request_policy(request))
    user = db.get(User, int(user_id))

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Invalid access token",
        )

    return user


@router.post("/google", dependencies=[Depends(limit_authentication)])
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

        set_auth_cookie(response, access_token)

        return {
            "user":
                UserResponse.model_validate(
                    user
                )
        }

    except ValueError:
        raise HTTPException(
            status_code=401,
            detail="Could not authenticate with Google",
        ) from None


@router.get("/google/start", dependencies=[Depends(limit_authentication)])
def start_google_oauth():
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(
            status_code=500,
            detail="Google login is temporarily unavailable",
        )

    if not GOOGLE_REDIRECT_URI:
        raise HTTPException(
            status_code=500,
            detail="Google login is temporarily unavailable",
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

    set_state_cookie(response, state)

    return response


@router.get("/google/callback", dependencies=[Depends(limit_authentication)])
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
    if (
        not state
        or not state_cookie
        or not state.isascii()
        or not state_cookie.isascii()
        or not secrets.compare_digest(
            state,
            state_cookie,
        )
    ):
        return oauth_failure(400, "Invalid Google OAuth state")

    if error:
        response = RedirectResponse(url=f"{FRONTEND_URL}/?google_login=cancelled", status_code=302)
        delete_state_cookie(response)
        return response
    if not code:
        return oauth_failure(400, "Invalid Google OAuth state")

    try:
        google_user = exchange_google_code(code)

        user = get_or_create_user(
            db=db,
            google_user=google_user,
        )

        access_token = create_access_token(user)

    except ValueError:
        return oauth_failure(401, "Could not authenticate with Google")
    except Exception:
        return oauth_failure(500, "Google login is temporarily unavailable")

    response = RedirectResponse(
        url=f"{FRONTEND_URL}/chat",
        status_code=302,
    )

    set_auth_cookie(response, access_token)
    delete_state_cookie(response)

    return response


def oauth_failure(status, detail):
    response = JSONResponse(status_code=status, content={"detail": detail})
    delete_state_cookie(response)
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


@router.post("/logout", dependencies=[Depends(limit_authentication)])
def logout(
    response: Response,
):
    delete_auth_cookie(response)
    delete_state_cookie(response)

    return {
        "message":
            "Logged out successfully"
    }
