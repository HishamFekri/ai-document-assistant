"""Small, validated session/cookie policy; no secrets or request proxy headers."""

from dataclasses import dataclass
from functools import lru_cache
import os
from urllib.parse import urlsplit

from dotenv import load_dotenv
from app.services.environment import is_production_environment

load_dotenv()

AUTH_COOKIE_NAME = "access_token"
GOOGLE_OAUTH_STATE_COOKIE = "google_oauth_state"
COOKIE_PATH = "/"
OAUTH_STATE_SECONDS = 600
DEFAULT_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173"


def checked_url(value, setting, *, https=False, origin=False):
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in ({"https"} if https else {"http", "https"})
                 and parsed.hostname and not parsed.username and not parsed.password
                 and not parsed.query and not parsed.fragment and "*" not in value
                 and not any(character.isspace() for character in value)
                 and (not origin or parsed.path in {"", "/"}))
        if parsed.port is not None and not 0 < parsed.port <= 65535:
            valid = False
    except ValueError:
        valid = False
    if not valid:
        raise RuntimeError(f"{setting} must be a valid {'HTTPS' if https else 'HTTP(S)'} {'origin' if origin else 'URL'}")
    return value.rstrip("/") if origin else value


@dataclass(frozen=True)
class AuthSettings:
    session_seconds: int
    secure: bool
    samesite: str
    frontend_url: str
    allowed_origins: tuple[str, ...]
    google_redirect_uri: str | None

    def cookie_options(self, *, state=False):
        return {"httponly": True, "secure": self.secure,
                "samesite": "lax" if state else self.samesite, "path": COOKIE_PATH}


@lru_cache(maxsize=1)
def auth_settings():
    production = is_production_environment()
    secure_setting = os.getenv("COOKIE_SECURE", "true" if production else "false").strip().lower()
    if secure_setting not in {"true", "false"}:
        raise RuntimeError("COOKIE_SECURE must be true or false")
    secure = secure_setting == "true"
    if production and not secure:
        raise RuntimeError("Production/staging requires COOKIE_SECURE=true")
    samesite = os.getenv("COOKIE_SAMESITE", "lax").strip().lower()
    if samesite not in {"lax", "strict", "none"}:
        raise RuntimeError("COOKIE_SAMESITE must be lax, strict or none")
    if samesite == "none" and not secure:
        raise RuntimeError("COOKIE_SAMESITE=none requires COOKIE_SECURE=true")
    try:
        minutes = int(os.getenv("JWT_EXPIRE_MINUTES", "10080"))
        if not 0 < minutes <= 525600:
            raise ValueError()
    except ValueError:
        raise RuntimeError("JWT_EXPIRE_MINUTES must be between 1 and 525600") from None
    frontend = checked_url(os.getenv("FRONTEND_URL", "http://localhost:3000").strip(),
                           "FRONTEND_URL", https=production, origin=True)
    origins = tuple(checked_url(value.strip(), "FRONTEND_URLS", https=production, origin=True)
                    for value in os.getenv("FRONTEND_URLS", frontend if production else DEFAULT_ORIGINS).split(",")
                    if value.strip())
    if not origins or frontend not in origins:
        raise RuntimeError("FRONTEND_URLS must include FRONTEND_URL and contain explicit origins")
    redirect = os.getenv("GOOGLE_REDIRECT_URI")
    if redirect:
        redirect = checked_url(redirect.strip(), "GOOGLE_REDIRECT_URI", https=production)
    return AuthSettings(minutes * 60, secure, samesite, frontend, origins, redirect)


def set_auth_cookie(response, token):
    policy = auth_settings()
    response.set_cookie(AUTH_COOKIE_NAME, token, max_age=policy.session_seconds, **policy.cookie_options())


def delete_auth_cookie(response):
    response.delete_cookie(AUTH_COOKIE_NAME, **auth_settings().cookie_options())


def set_state_cookie(response, state):
    response.set_cookie(GOOGLE_OAUTH_STATE_COOKIE, state, max_age=OAUTH_STATE_SECONDS,
                        **auth_settings().cookie_options(state=True))


def delete_state_cookie(response):
    response.delete_cookie(GOOGLE_OAUTH_STATE_COOKIE, **auth_settings().cookie_options(state=True))
