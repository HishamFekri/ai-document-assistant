"""Security headers on every API response, including generated error responses.

Pure ASGI so streamed (NDJSON) and file responses are never buffered. Headers a
route sets itself are left unchanged.
"""

from app.services.environment import is_production_environment


API_SECURITY_HEADERS = (
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
    # Enforced, but limited to framing: the API also serves /docs, which loads
    # external assets that a full default-src policy would block.
    (b"content-security-policy", b"frame-ancestors 'none'"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=(), usb=()"),
)
# Production and staging are deployed behind HTTPS; development and test are not.
HSTS_HEADER = (b"strict-transport-security", b"max-age=63072000")


def security_headers(production):
    return API_SECURITY_HEADERS + ((HSTS_HEADER,) if production else ())


class SecurityHeadersMiddleware:
    def __init__(self, app, *, production=None):
        self.app = app
        self.headers = security_headers(is_production_environment() if production is None else production)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                headers.extend(header for header in self.headers if header[0] not in present)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)
