"""Origin checks for state-changing requests (CSRF defence in depth).

Browsers attach the session cookie automatically, so a cookie-authenticated
unsafe request must prove where it came from: an allowed Origin, or, when a
browser or proxy omits Origin, an allowed Referer. Requests authenticated only
by an Authorization header cannot be forged cross-site and are unaffected, as
are anonymous requests.
"""

from urllib.parse import urlsplit

from app.services.auth_config import AUTH_COOKIE_NAME


UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def referer_origin(referer):
    try:
        parts = urlsplit(referer)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return None
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    return f"{parts.scheme}://{host}" + (f":{port}" if port is not None else "")


def uses_cookie_session(headers, cookies):
    # get_current_user prefers a Bearer token over the cookie when both exist.
    bearer = headers.get("authorization", "").lower().startswith("bearer ")
    return AUTH_COOKIE_NAME in cookies and not bearer


def rejects_request_origin(method, headers, cookies, allowed_origins):
    if method.upper() not in UNSAFE_METHODS:
        return False
    origin = headers.get("origin")
    if origin:
        return origin not in allowed_origins
    if not uses_cookie_session(headers, cookies):
        return False
    referer = headers.get("referer")
    allowed = {value.lower() for value in allowed_origins}
    return not referer or referer_origin(referer) not in allowed
