"""API security headers, using the existing blocked-network/DB import harness.

Run: python -B tests/test_security_headers.py
"""

import asyncio
import importlib
import unittest
from unittest.mock import patch

import test_resource_admission as harness


EXPECTED = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
    "content-security-policy": "frame-ancestors 'none'",
}


def run_asgi(app, scope):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    return sent


class SecurityHeaderTests(unittest.TestCase):
    __test__ = __name__ == "__main__"

    @classmethod
    def setUpClass(cls):
        harness.ResourceAdmissionTests.setUpClass.__func__(cls)
        cls.obs = importlib.import_module("app.services.observability")
        cls.headers = importlib.import_module("app.services.security_headers")
        with patch.object(cls.obs, "configure_logging"):
            cls.main = importlib.import_module("main")

    def setUp(self):
        harness.ResourceAdmissionTests.setUp(self)

    def assert_security_headers(self, response, *, hsts=False):
        for name, value in EXPECTED.items():
            self.assertEqual(response.headers.get(name), value, name)
        self.assertIn("camera=()", response.headers["permissions-policy"])
        self.assertEqual("strict-transport-security" in response.headers, hsts)

    def client(self, app):
        from fastapi.testclient import TestClient
        return self.stack.enter_context(TestClient(app))

    def test_real_app_success_rejection_and_preflight_responses_carry_headers(self):
        client = self.client(self.main.app)
        self.assert_security_headers(client.get("/health"))
        denied = client.post("/auth/logout", headers={"Origin": "https://untrusted.invalid"})
        self.assertEqual(denied.status_code, 403)
        self.assert_security_headers(denied)
        preflight = client.options("/chats", headers={
            "Origin": self.main.allowed_origins[0], "Access-Control-Request-Method": "POST",
        })
        self.assertEqual(preflight.headers["access-control-allow-origin"], self.main.allowed_origins[0])
        self.assert_security_headers(preflight)

    def test_unhandled_500_carries_headers(self):
        from fastapi.testclient import TestClient
        app = self.main.ObservedFastAPI()

        @app.get("/failure")
        def failure():
            raise RuntimeError("synthetic failure")

        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/failure")
        self.assertEqual(response.status_code, 500)
        self.assert_security_headers(response)

    def test_hsts_only_in_production(self):
        from fastapi import FastAPI
        app = FastAPI()
        app.get("/ok")(lambda: {"ok": True})
        self.assert_security_headers(self.client(self.headers.SecurityHeadersMiddleware(app, production=False)).get("/ok"))
        production = self.client(self.headers.SecurityHeadersMiddleware(app, production=True)).get("/ok")
        self.assert_security_headers(production, hsts=True)
        self.assertEqual(production.headers["strict-transport-security"], "max-age=63072000")
        for environment, expected in (("production", True), ("staging", True), ("development", False), ("test", False)):
            with self.subTest(environment=environment), patch.dict("os.environ", {"ENVIRONMENT": environment}):
                middleware = self.headers.SecurityHeadersMiddleware(app)
                self.assertEqual(any(name == b"strict-transport-security" for name, _ in middleware.headers), expected)

    def test_route_set_headers_are_not_overridden(self):
        from fastapi import FastAPI
        from fastapi.responses import JSONResponse
        app = FastAPI()
        app.get("/own")(lambda: JSONResponse({}, headers={"Referrer-Policy": "same-origin", "X-Frame-Options": "SAMEORIGIN"}))
        response = self.client(self.headers.SecurityHeadersMiddleware(app, production=False)).get("/own")
        self.assertEqual(response.headers["referrer-policy"], "same-origin")
        self.assertEqual(response.headers["x-frame-options"], "SAMEORIGIN")
        self.assertEqual(response.headers.get_list("referrer-policy"), ["same-origin"])
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    def test_streamed_bodies_pass_through_unbuffered(self):
        async def streaming_app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"application/x-ndjson")]})
            for index in range(3):
                await send({"type": "http.response.body", "body": b'{"n":%d}\n' % index, "more_body": index < 2})

        sent = run_asgi(self.headers.SecurityHeadersMiddleware(streaming_app, production=False),
                        {"type": "http", "method": "POST", "path": "/stream", "headers": []})
        self.assertEqual([message["type"] for message in sent],
                         ["http.response.start"] + ["http.response.body"] * 3)
        self.assertEqual([message.get("more_body") for message in sent[1:]], [True, True, False])
        names = [name for name, _ in sent[0]["headers"]]
        self.assertIn(b"x-content-type-options", names)
        self.assertIn(b"content-type", names)

    def test_file_download_keeps_disposition_and_gains_headers(self):
        from fastapi import FastAPI
        from fastapi.responses import Response
        app = FastAPI()
        app.get("/file")(lambda: Response(b"%PDF-1.7", media_type="application/pdf",
                                          headers={"Content-Disposition": 'attachment; filename="synthetic.pdf"'}))
        response = self.client(self.headers.SecurityHeadersMiddleware(app, production=False)).get("/file")
        self.assertEqual(response.content, b"%PDF-1.7")
        self.assertEqual(response.headers["content-disposition"], 'attachment; filename="synthetic.pdf"')
        self.assert_security_headers(response)

    def test_non_http_scopes_pass_through_untouched(self):
        calls = []

        async def lifespan_app(scope, receive, send):
            calls.append(scope["type"])

        asyncio.run(self.headers.SecurityHeadersMiddleware(lifespan_app, production=False)(
            {"type": "lifespan"}, None, None))
        self.assertEqual(calls, ["lifespan"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
