"""Offline guards plus opt-in live Redis tests. No application imports.

Run: python -B tests/test_resource_admission_redis.py
Live tests require TEST_REDIS_URL pointing to a dedicated loopback test Redis DB
3 or higher. Only UUID-prefixed keys created by this test are deleted.
"""

import ast
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import re
import time
import unittest
from urllib.parse import urlsplit
from uuid import uuid4

from dotenv import dotenv_values
from redis import Redis
from redis.backoff import NoBackoff
from redis.retry import Retry


BACKEND = Path(__file__).resolve().parents[1]


def validate_test_redis_url(value, application_urls=()):
    if not value:
        raise ValueError("TEST_REDIS_URL is required; no application URL fallback.")
    try:
        url = urlsplit(value)
        port = url.port if url.port is not None else 6379
        if (url.scheme not in {"redis", "rediss"}
                or url.hostname not in {"127.0.0.1", "::1", "localhost"}
                or not 1 <= port <= 65535 or url.query or url.fragment
                or not re.fullmatch(r"/[0-9]+", url.path)
                or int(url.path[1:]) < 3):
            raise ValueError()
        for application in application_urls:
            if not application:
                continue
            other = urlsplit(application)
            if other.scheme not in {"redis", "rediss"}:
                continue  # A non-Redis Celery transport cannot be this Redis DB.
            # Conservative even when application/test hosts differ.
            if int(other.path.lstrip("/") or "0") == int(url.path[1:]):
                raise ValueError()
    except Exception:
        raise ValueError("Test Redis must use a separate loopback database numbered 3 or higher, without URL options.") from None
    return value


def rate_script():
    tree = ast.parse((BACKEND / "app/services/resource_admission.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "RATE_SCRIPT" for target in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("Production rate script missing")


class RedisTestSafetyTests(unittest.TestCase):
    def test_missing_url_never_uses_application_fallback(self):
        with self.assertRaises(ValueError):
            validate_test_redis_url(None, ["redis://127.0.0.1:6379/15"])

    def test_unsafe_urls_and_options_rejected_without_credentials(self):
        for url in ("redis://:secret@remote.example/15", "redis://:secret@127.0.0.1/0",
                    "redis://127.0.0.1/2", "redis://127.0.0.1/15?db=0",
                    "redis://127.0.0.1/15#fragment", "redis://127.0.0.1:bad/15",
                    "redis://127.0.0.1:0/15",
                    "unix:///tmp/redis.sock", "redis://127.0.0.1", "secret"):
            with self.subTest(url=url), self.assertRaises(ValueError) as error:
                validate_test_redis_url(url)
            self.assertNotIn("secret", str(error.exception))

    def test_application_database_is_rejected_even_with_other_host(self):
        with self.assertRaises(ValueError):
            validate_test_redis_url("redis://127.0.0.1/15", ["rediss://example.invalid/15"])

    def test_explicit_separate_loopback_target_allowed(self):
        value = "redis://127.0.0.1:6379/15"
        self.assertEqual(validate_test_redis_url(value, ["redis://localhost/2"]), value)
        self.assertIn("redis.call('TIME')", rate_script())


@unittest.skipUnless(os.environ.get("TEST_REDIS_URL"), "TEST_REDIS_URL not configured; no Redis contacted")
class LiveRedisAdmissionTests(unittest.TestCase):
    def setUp(self):
        configured = dotenv_values(BACKEND / ".env")  # Never loads credentials into the environment.
        names = ("RESOURCE_REDIS_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND")
        application_urls = [mapping.get(name) for mapping in (os.environ, configured) for name in names]
        value = validate_test_redis_url(os.environ.get("TEST_REDIS_URL"), application_urls)
        self.clients = [Redis.from_url(value, socket_connect_timeout=1, socket_timeout=1,
                                      retry=Retry(NoBackoff(), 0), max_connections=8) for _ in range(2)]
        self.keys = []
        self.addCleanup(self.cleanup)
        self.script = rate_script()

    def cleanup(self):
        try:
            if self.keys:
                self.clients[0].delete(*self.keys)
        finally:
            for client in self.clients:
                client.close()

    def key(self):
        key = f"resource-test:{uuid4().hex}"
        self.keys.append(key)
        return key

    def consume(self, key, limit=2, window=60, client=0):
        return self.clients[client].eval(self.script, 1, key, window, limit, uuid4().hex)

    def test_real_lua_is_atomic_across_independent_clients(self):
        key = self.key()
        with ThreadPoolExecutor(max_workers=4) as executor:
            outcomes = list(executor.map(lambda index: self.consume(key, client=index % 2), range(8)))
        self.assertEqual(sum(int(result[0]) for result in outcomes), 2)
        self.assertTrue(all(1 <= int(result[1]) <= 60 for result in outcomes if not result[0]))
        self.assertEqual(self.clients[1].zcard(key), 2)
        self.assertGreater(self.clients[1].pttl(key), 0)

    def test_real_lua_keys_are_independent(self):
        first, second = self.key(), self.key()
        self.assertEqual(self.consume(first, limit=1)[0], 1)
        self.assertEqual(self.consume(first, limit=1, client=1)[0], 0)
        self.assertEqual(self.consume(second, limit=1, client=1)[0], 1)

    def test_real_lua_window_expires(self):
        key = self.key()
        self.assertEqual(self.consume(key, limit=1, window=1)[0], 1)
        self.assertEqual(self.consume(key, limit=1, window=1), [0, 1])
        time.sleep(1.1)
        self.assertEqual(self.consume(key, limit=1, window=1, client=1)[0], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
