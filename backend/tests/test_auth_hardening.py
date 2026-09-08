"""Batch 10: real JWT/HTTP/cookie code, synthetic users, no external services.

Run: python -B tests/test_auth_hardening.py
"""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie
import importlib
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import jwt
import test_resource_admission as admission_harness


class AuthHardeningTests(unittest.TestCase):
    __test__ = __name__ == "__main__"

    @classmethod
    def setUpClass(cls):
        admission_harness.ResourceAdmissionTests.setUpClass.__func__(cls)
        cls.service = importlib.import_module("app.services.auth_service")
        cls.config = importlib.import_module("app.services.auth_config")
        cls.old_tests = importlib.import_module("test_auth")

    def setUp(self):
        admission_harness.ResourceAdmissionTests.setUp(self)
        self.config.auth_settings.cache_clear()
        self.addCleanup(self.config.auth_settings.cache_clear)
        self.stack.enter_context(patch.object(self.service, "JWT_SECRET_KEY", "synthetic-test-signing-key-" * 3))
        self.user = SimpleNamespace(id=1, email="synthetic@example.test", name="Synthetic", picture=None,
                                    created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.google = {"sub": "synthetic-google", "email": self.user.email, "email_verified": True}
        self.db = MagicMock()
        self.db.get.side_effect = lambda *args: self.user
        self.stack.enter_context(patch.object(self.auth, "GOOGLE_REDIRECT_URI", "http://testserver/auth/google/callback"))

    def configure(self, **values):
        self.stack.enter_context(patch.dict(os.environ, {key: str(value) for key, value in values.items()}))
        self.config.auth_settings.cache_clear()
        return self.config.auth_settings()

    def client(self, *, app=None, base_url="http://testserver"):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        if app is None:
            app = FastAPI()
            app.include_router(self.auth.router)
            app.add_exception_handler(self.admission.ResourceRejected, self.admission.resource_error_response)
        self.stack.enter_context(patch.dict(app.dependency_overrides, {self.database.get_db: lambda: self.db}))
        return self.stack.enter_context(TestClient(app, base_url=base_url))

    def encoded(self, payload=None, **kwargs):
        if payload is None:
            payload = {"sub": "1", "exp": int(time.time()) + 120}
        return jwt.encode(payload, kwargs.get("key", self.service.JWT_SECRET_KEY),
                          algorithm=kwargs.get("algorithm", self.service.JWT_ALGORITHM))

    def cookie(self, response, name="access_token"):
        cookies = SimpleCookie()
        for header in response.headers.get_list("set-cookie"):
            cookies.load(header)
        return cookies[name]

    def login(self, client):
        with patch.object(self.auth, "verify_google_token", return_value=self.google), \
             patch.object(self.auth, "get_or_create_user", return_value=self.user):
            return client.post("/auth/google", json={"credential": "synthetic-credential"})

    def assert_invalid(self, token):
        client = self.client()
        before = self.db.get.call_count
        response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Invalid access token"})
        self.assertEqual(self.db.get.call_count, before)

    def test_valid_issued_jwt_cookie_and_bearer_authenticate(self):
        token = self.service.create_access_token(self.user)
        payload = self.service.decode_access_token(token)
        self.assertEqual(set(payload), {"sub", "email", "iat", "exp"})
        self.assertEqual(payload["exp"] - payload["iat"], 604800)
        client = self.client()
        self.assertEqual(client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["id"], 1)
        self.assertEqual(self.login(client).status_code, 200)
        self.assertEqual(client.get("/auth/me").json()["id"], 1)

    def test_bearer_precedence_is_preserved(self):
        client = self.client()
        self.login(client)
        self.assertEqual(client.get("/auth/me", headers={"Authorization": "Bearer invalid"}).status_code, 401)

    def test_expired_token_rejected(self):
        self.assert_invalid(self.encoded({"sub": "1", "exp": int(time.time()) - 1}))

    def test_invalid_signature_rejected(self):
        self.assert_invalid(self.encoded(key="different-synthetic-signing-secret"))

    def test_wrong_algorithm_and_unsigned_token_rejected(self):
        self.assert_invalid(self.encoded(algorithm="HS384"))
        self.assert_invalid(jwt.encode({"sub": "1", "exp": int(time.time()) + 120}, "", algorithm="none"))

    def test_missing_exp_and_sub_rejected(self):
        for payload in ({"sub": "1"}, {"exp": int(time.time()) + 120}, {"sub": None, "exp": int(time.time()) + 120}):
            self.assert_invalid(self.encoded(payload))

    def test_malformed_subjects_rejected_before_user_admission(self):
        for subject in (1, True, "", "0", "-1", "1.5", "01", "²", "١", "2147483648", "9" * 100):
            self.assert_invalid(self.encoded({"sub": subject, "exp": int(time.time()) + 120}))

    def test_malformed_expiry_and_issued_at_are_safe(self):
        for claim in ("exp", "iat"):
            for value in (None, [], {}, True, "private-value", float("inf")):
                payload = {"sub": "1", "exp": int(time.time()) + 120, claim: value}
                self.assert_invalid(self.encoded(payload))

    def test_missing_optional_email_and_iat_remain_compatible(self):
        self.assertEqual(self.service.decode_access_token(self.encoded())["sub"], "1")

    def test_malformed_token_and_decode_details_never_escape(self):
        self.assert_invalid("private-malformed-token")
        with patch.object(self.auth, "decode_access_token", side_effect=ValueError("private JWT /key/path")):
            self.assert_invalid("synthetic")

    def test_unknown_user_has_same_failure_as_invalid_token(self):
        self.db.get.side_effect = lambda *args: None
        response = self.client().get("/auth/me", headers={"Authorization": f"Bearer {self.encoded()}"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Invalid access token"})

    def test_configured_jwt_and_cookie_lifetimes_match(self):
        self.configure(JWT_EXPIRE_MINUTES=90)
        response = self.login(self.client())
        cookie = self.cookie(response)
        payload = self.service.decode_access_token(cookie.value)
        self.assertEqual(payload["exp"] - payload["iat"], 5400)
        self.assertEqual(cookie["max-age"], "5400")

    def test_development_cookie_flags_and_no_domain(self):
        cookie = self.cookie(self.login(self.client()))
        self.assertTrue(cookie["httponly"])
        self.assertFalse(cookie["secure"])
        self.assertEqual(cookie["samesite"], "lax")
        self.assertEqual(cookie["path"], "/")
        self.assertEqual(cookie["domain"], "")

    def test_production_and_staging_secure_cookies(self):
        for environment in ("production", "staging"):
            self.configure(ENVIRONMENT=environment, FRONTEND_URL="https://app.example.test",
                           GOOGLE_REDIRECT_URI="https://api.example.test/auth/google/callback")
            cookie = self.cookie(self.login(self.client(base_url="https://testserver")))
            self.assertTrue(cookie["secure"])
            self.assertTrue(cookie["httponly"])

    def test_configurable_samesite_keeps_state_lax(self):
        for samesite in ("lax", "strict", "none"):
            self.configure(COOKIE_SAMESITE=samesite, COOKIE_SECURE="true")
            client = self.client(base_url="https://testserver")
            self.assertEqual(self.cookie(self.login(client))["samesite"], samesite)
            state = self.cookie(client.get("/auth/google/start", follow_redirects=False), "google_oauth_state")
            self.assertEqual(state["samesite"], "lax")
            self.assertEqual(state["max-age"], "600")
            self.assertTrue(state["httponly"] and state["secure"])

    def test_invalid_configuration_fails_without_echoing_values(self):
        cases = [dict(ENVIRONMENT="prod-typo-private"), dict(COOKIE_SECURE="private-value"),
                 dict(COOKIE_SAMESITE="private-value"), dict(COOKIE_SAMESITE="none", COOKIE_SECURE="false"),
                 dict(ENVIRONMENT="production", COOKIE_SECURE="false"),
                 dict(ENVIRONMENT="production", FRONTEND_URL="http://app.example.test"),
                 dict(ENVIRONMENT="production", FRONTEND_URL="https://app.example.test", GOOGLE_REDIRECT_URI="http://api.example.test/callback"),
                 dict(FRONTEND_URLS="*"), dict(FRONTEND_URLS=""),
                 dict(JWT_EXPIRE_MINUTES="0"), dict(JWT_EXPIRE_MINUTES="-1"), dict(JWT_EXPIRE_MINUTES="private-value")]
        for settings in cases:
            with self.subTest(settings=settings), patch.dict(os.environ, settings):
                self.config.auth_settings.cache_clear()
                with self.assertRaises(RuntimeError) as error:
                    self.config.auth_settings()
                self.assertNotIn("private", str(error.exception))
        self.config.auth_settings.cache_clear()

    def test_logout_matches_cookie_flags_and_expires_both_cookies(self):
        self.configure(COOKIE_SECURE="true", COOKIE_SAMESITE="none")
        client = self.client(base_url="https://testserver")
        issued = self.cookie(self.login(client))
        client.get("/auth/google/start", follow_redirects=False)
        response = client.post("/auth/logout")
        self.assertEqual(response.status_code, 200)
        deleted = self.cookie(response)
        for flag in ("httponly", "secure", "samesite", "path", "domain"):
            self.assertEqual(deleted[flag], issued[flag])
        self.assertEqual(deleted["max-age"], "0")
        self.assertLessEqual(parsedate_to_datetime(deleted["expires"]), datetime.now(timezone.utc))
        self.assertEqual(self.cookie(response, "google_oauth_state")["max-age"], "0")
        self.assertEqual(client.get("/auth/me").status_code, 401)

    def test_logout_is_idempotent_without_valid_auth_cookie(self):
        client = self.client()
        self.assertEqual(client.post("/auth/logout").status_code, 200)
        self.assertEqual(client.post("/auth/logout").status_code, 200)

    def test_oauth_state_is_random_bounded_and_matches_redirect(self):
        client = self.client()
        first = client.get("/auth/google/start", follow_redirects=False)
        second = client.get("/auth/google/start", follow_redirects=False)
        state = self.cookie(first, "google_oauth_state")
        self.assertGreaterEqual(len(state.value), 40)
        self.assertNotEqual(state.value, self.cookie(second, "google_oauth_state").value)
        self.assertEqual(state["max-age"], "600")
        query = parse_qs(urlsplit(first.headers["location"]).query)
        self.assertEqual(query["state"], [state.value])

    def test_missing_mismatched_or_non_ascii_state_rejected_and_cleared(self):
        client = self.client()
        with patch.object(self.auth, "exchange_google_code") as exchange:
            for query in ("code=x", "code=x&state=mismatch", "code=x&state=%C3%A9", "error=denied"):
                client.get("/auth/google/start", follow_redirects=False)
                response = client.get("/auth/google/callback?" + query, follow_redirects=False)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.cookie(response, "google_oauth_state")["max-age"], "0")
            exchange.assert_not_called()

    def test_missing_state_cookie_and_missing_code_rejected(self):
        client = self.client()
        with patch.object(self.auth, "exchange_google_code") as exchange:
            response = client.get("/auth/google/callback?code=x&state=synthetic", follow_redirects=False)
            self.assertEqual(response.status_code, 400)
            start = client.get("/auth/google/start", follow_redirects=False)
            state = self.cookie(start, "google_oauth_state").value
            response = client.get("/auth/google/callback", params={"state": state}, follow_redirects=False)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(self.cookie(response, "google_oauth_state")["max-age"], "0")
            exchange.assert_not_called()

    def test_valid_callback_issues_session_and_clears_state(self):
        client = self.client()
        start = client.get("/auth/google/start", follow_redirects=False)
        state = self.cookie(start, "google_oauth_state").value
        with patch.object(self.auth, "exchange_google_code", return_value=self.google) as exchange, \
             patch.object(self.auth, "get_or_create_user", return_value=self.user):
            response = client.get("/auth/google/callback", params={"state": state, "code": "synthetic"}, follow_redirects=False)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.headers["location"], "http://localhost:3000/chat")
            exchange.assert_called_once_with("synthetic")
        self.assertEqual(self.cookie(response, "google_oauth_state")["max-age"], "0")
        self.assertEqual(client.get("/auth/me").status_code, 200)

    def test_oauth_cancellation_and_provider_failure_clear_state(self):
        client = self.client()
        for cancelled in (True, False):
            start = client.get("/auth/google/start", follow_redirects=False)
            state = self.cookie(start, "google_oauth_state").value
            with patch.object(self.auth, "exchange_google_code", side_effect=ValueError("private-provider-detail")) as exchange:
                response = client.get("/auth/google/callback", params={"state": state, **({"error": "denied"} if cancelled else {"code": "x"})}, follow_redirects=False)
                self.assertEqual(response.status_code, 302 if cancelled else 401)
                if cancelled: exchange.assert_not_called()
                self.assertNotIn("private", response.text)
            self.assertEqual(self.cookie(response, "google_oauth_state")["max-age"], "0")

    def test_google_signature_verifier_receives_configured_audience(self):
        with patch.object(self.service.id_token, "verify_oauth2_token", return_value=self.google) as verify:
            self.assertEqual(self.service.verify_google_token("synthetic"), self.google)
        self.assertEqual(verify.call_args.args[0], "synthetic")
        self.assertEqual(verify.call_args.args[2], self.service.GOOGLE_CLIENT_ID)

    def test_invalid_google_credential_has_safe_public_error(self):
        with patch.object(self.service.id_token, "verify_oauth2_token", side_effect=ValueError("private signature audience")):
            response = self.client().post("/auth/google", json={"credential": "synthetic-credential"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Could not authenticate with Google"})

    def test_google_identity_fields_still_required(self):
        for data in ({"email": "a"}, {"sub": "g"}, {"sub": 2, "email": "a"}, {"sub": "g", "email": "a", "email_verified": False}):
            with patch.object(self.service.id_token, "verify_oauth2_token", return_value=data), self.assertRaises(ValueError):
                self.service.verify_google_token("synthetic")

    def test_code_exchange_uses_verified_id_token_and_redacts_provider_error(self):
        response = MagicMock(ok=True)
        response.json.return_value = {"id_token": "synthetic-id-token"}
        with patch.object(self.service, "GOOGLE_CLIENT_SECRET", "synthetic-secret"), \
             patch.object(self.service, "GOOGLE_REDIRECT_URI", "http://testserver/auth/google/callback"), \
             patch.object(self.service.http_requests, "post", return_value=response) as post, \
             patch.object(self.service, "verify_google_token", return_value=self.google) as verify:
            self.assertEqual(self.service.exchange_google_code("synthetic-code"), self.google)
            verify.assert_called_once_with("synthetic-id-token")
            self.assertEqual(post.call_args.kwargs["data"]["client_id"], self.service.GOOGLE_CLIENT_ID)
            response.ok = False
            response.json.return_value = {"error_description": "private-client-secret"}
            with self.assertRaises(ValueError) as error:
                self.service.exchange_google_code("synthetic-code")
            self.assertNotIn("private", str(error.exception))

    def test_actual_app_cors_credentials_use_explicit_origins(self):
        main = importlib.import_module("main")
        client = self.client(app=main.app)
        response = client.options("/auth/logout", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"})
        self.assertEqual(response.headers["access-control-allow-origin"], "http://localhost:3000")
        self.assertEqual(response.headers["access-control-allow-credentials"], "true")
        rejected = client.post("/auth/logout", headers={"Origin": "https://untrusted.example.test"})
        self.assertEqual(rejected.status_code, 403)
        self.assertNotIn("set-cookie", rejected.headers)

    def test_auth_rate_limit_blocks_every_login_and_logout_path(self):
        client = self.client()
        for _ in range(self.limits.resource_limits().rates["auth"].limit):
            self.admission.consume_rate("ip:unknown", "auth")
        with patch.object(self.auth, "verify_google_token") as verify, patch.object(self.auth, "exchange_google_code") as exchange:
            responses = [client.post("/auth/google", json={"credential": "synthetic"}),
                         client.get("/auth/google/start", follow_redirects=False),
                         client.get("/auth/google/callback?code=x&state=x", follow_redirects=False),
                         client.post("/auth/logout", headers={"X-Forwarded-For": "198.51.100.1"})]
        self.assertEqual([response.status_code for response in responses], [429] * 4)
        self.assertNotIn("set-cookie", responses[-1].headers)
        verify.assert_not_called()
        exchange.assert_not_called()

    def test_logout_admission_outage_is_safe_and_does_not_claim_cookie_deletion(self):
        with patch.object(self.rates, "eval", side_effect=RuntimeError("private Redis credentials")):
            response = self.client().post("/auth/logout")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("set-cookie", response.headers)
        self.assertNotIn("private", response.text)

    def test_auth_failures_consume_auth_rate_and_forwarded_headers_do_not_change_cookie_policy(self):
        client = self.client()
        response = client.get("/auth/google/callback?code=x", follow_redirects=False)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.rates.calls, 1)
        response = client.get("/auth/google/start", headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "untrusted.test"}, follow_redirects=False)
        self.assertFalse(self.cookie(response, "google_oauth_state")["secure"])

    def test_existing_authentication_tests_with_isolated_session_adapter(self):
        client = self.client()
        self.old_tests.test_auth_me_requires_authentication(client)
        self.old_tests.test_auth_me_rejects_invalid_token(client)
        def add(user):
            user.id = 1
            user.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
            self.user = user
        self.db.add.side_effect = add
        self.old_tests.test_auth_me_accepts_valid_token(client, self.db)


def test_auth_hardening_in_isolated_process():
    result = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve())],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == "__main__":
    unittest.main(verbosity=2)
