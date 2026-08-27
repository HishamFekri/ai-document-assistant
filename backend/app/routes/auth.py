import os

from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    HTTPException,
    Response,
)

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
    get_or_create_user,
    verify_google_token,
)


router = APIRouter(
    prefix="/auth",
    tags=["Auth"],
)


security = HTTPBearer(
    auto_error=False
)


AUTH_COOKIE_NAME = (
    "access_token"
)


IS_PRODUCTION = (
    os.getenv(
        "ENVIRONMENT",
        "development",
    ).lower()
    == "production"
)


COOKIE_SECURE = (
    IS_PRODUCTION
)


COOKIE_SAMESITE = (
    "lax"
)


def get_current_user(
    credentials:
        HTTPAuthorizationCredentials
        | None = Depends(
            security
        ),
    access_token_cookie:
        str | None = Cookie(
            default=None,
            alias=AUTH_COOKIE_NAME,
        ),
    db: Session = Depends(
        get_db
    ),
) -> User:
    token = (
        credentials.credentials
        if credentials
        else access_token_cookie
    )

    if not token:
        raise HTTPException(
            status_code=401,
            detail=(
                "Authentication required"
            ),
        )

    try:
        payload = (
            decode_access_token(
                token
            )
        )

    except ValueError as error:
        raise HTTPException(
            status_code=401,
            detail=str(
                error
            ),
        )

    user_id = (
        payload.get(
            "sub"
        )
    )

    if (
        not isinstance(
            user_id,
            str,
        )
        or not user_id.isdigit()
    ):
        raise HTTPException(
            status_code=401,
            detail=(
                "Invalid access token"
            ),
        )

    user = db.get(
        User,
        int(
            user_id
        ),
    )

    if not user:
        raise HTTPException(
            status_code=401,
            detail=(
                "User not found"
            ),
        )

    return user


@router.post(
    "/google"
)
def login_with_google(
    data: GoogleAuthRequest,
    response: Response,
    db: Session = Depends(
        get_db
    ),
):
    try:
        google_user = (
            verify_google_token(
                data.credential
            )
        )

        user = (
            get_or_create_user(
                db=db,
                google_user=(
                    google_user
                ),
            )
        )

        access_token = (
            create_access_token(
                user
            )
        )

        response.set_cookie(
            key=AUTH_COOKIE_NAME,
            value=access_token,
            httponly=True,
            secure=(
                COOKIE_SECURE
            ),
            samesite=(
                COOKIE_SAMESITE
            ),
            max_age=(
                60
                * 60
                * 24
                * 7
            ),
            path="/",
        )

        return {
            "user":
                UserResponse
                .model_validate(
                    user
                ),
        }

    except ValueError as error:
        raise HTTPException(
            status_code=401,
            detail=str(
                error
            ),
        )


@router.get(
    "/me",
    response_model=(
        UserResponse
    ),
)
def get_me(
    current_user: User = Depends(
        get_current_user
    ),
):
    return current_user


@router.post(
    "/logout"
)
def logout(
    response: Response
):
    response.delete_cookie(
        key=AUTH_COOKIE_NAME,
        httponly=True,
        secure=(
            COOKIE_SECURE
        ),
        samesite=(
            COOKIE_SAMESITE
        ),
        path="/",
    )

    return {
        "message":
            "Logged out successfully"
    }