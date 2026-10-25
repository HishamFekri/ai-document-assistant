"""Authentication rate-limit client identity (Task 1.4), offline.

Run: python -B tests/test_client_ip.py
Synthetic documentation addresses only (RFC 5737 / RFC 3849); no network.
"""

import importlib
import io
import ipaddress
import logging
import os
import re
import unittest
from unittest.mock import patch

import test_resource_admission as harness


RENDER_PEER = "10.20.30.40"           # stands in for the immediate proxy (load balancer)
EDGE_PROXY = "198.51.100.7"           # stands in for an intermediate trusted proxy
USER_A, USER_B, ATTACKER = "203.0.113.10", "203.0.113.20", "192.0.2.66"
TRUSTED = "10.0.0.0/8,198.51.100.0/24"


def legacy_peer_identity(host):
    """Byte-for-byte copy of the pre-Task-1.4 authentication_subject logic."""
    peer = host if host is not None else "unknown"
    try:
        return str(ipaddress.ip_address(peer))
    except ValueError:
        return "unknown"


def request(peer=RENDER_PEER, headers=()):
    from starlette.requests import Request
    scope = {"type": "http", "method": "POST", "path": "/auth/google", "headers": [
        (name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in headers]}
    if peer is not None:
        scope["client"] = (peer, 50000)
    return Request(scope)


class ClientIpTests(unittest.TestCase):
    __test__ = __name__ == "__main__"

    @classmethod
    def setUpClass(cls):
        harness.ResourceAdmissionTests.setUpClass.__func__(cls)
        cls.client_ip = importlib.import_module("app.services.client_ip")
        cls.obs = importlib.import_module("app.services.observability")
        cls.runtime = importlib.import_module("app.services.runtime_config")
        with patch.object(cls.obs, "configure_logging"):
            cls.main = importlib.import_module("main")

    def setUp(self):
        harness.ResourceAdmissionTests.setUp(self)
        for name in ("AUTH_CLIENT_IP_SOURCE", "AUTH_TRUSTED_PROXY_CIDRS", "AUTH_TRUSTED_PROXY_HOPS"):
            self.stack.enter_context(patch.dict(os.environ, {}, clear=False))
            os.environ.pop(name, None)
        self.client_ip.client_ip_policy.cache_clear()
        self.addCleanup(self.client_ip.client_ip_policy.cache_clear)

    def policy(self, mode, cidrs=TRUSTED, hops=None):
        return self.client_ip.parse_client_ip_policy(mode, cidrs, hops)

    def identity(self, policy, peer=RENDER_PEER, headers=()):
        return self.client_ip.auth_client_identity(request(peer, headers), policy)

    def configure(self, **values):
        self.stack.enter_context(patch.dict(os.environ, values))
        self.client_ip.client_ip_policy.cache_clear()

    # ---- Default peer mode is unchanged -------------------------------------------

    def test_default_policy_is_peer_mode(self):
        self.assertEqual(self.client_ip.client_ip_policy(), self.client_ip.ClientIpPolicy())
        self.assertEqual(self.client_ip.client_ip_policy().mode, "peer")

    def test_peer_mode_matches_legacy_subject_exactly(self):
        spoof = [("X-Forwarded-For", USER_A), ("CF-Connecting-IP", USER_B), ("Forwarded", "for=192.0.2.1"),
                 ("X-Real-IP", USER_A), ("True-Client-IP", USER_B)]
        for host in (RENDER_PEER, "2001:db8::1", "2001:0db8:0000::0001", "::ffff:203.0.113.5",
                     "testclient", "", None, "not-an-ip"):
            with self.subTest(host=host):
                expected = "ip:" + legacy_peer_identity(host)
                self.assertEqual(self.admission.authentication_subject(request(host, spoof)), expected)

    def test_existing_forwarded_header_spoofing_protection_still_holds(self):
        from starlette.requests import Request
        req = Request({"type": "http", "client": ("203.0.113.4", 1234),
                       "headers": [(b"x-forwarded-for", b"198.51.100.1"), (b"forwarded", b"for=198.51.100.2")]})
        self.assertEqual(self.admission.authentication_subject(req), "ip:203.0.113.4")

    def test_uvicorn_still_ignores_proxy_headers(self):
        self.assertIn("--no-proxy-headers", self.runtime.server_arguments())

    # ---- Configuration validation ------------------------------------------------

    def test_invalid_configurations_are_rejected_without_echoing_values(self):
        cases = [
            ("bogus-mode", None, None),
            ("cf-connecting-ip", None, None),                 # forwarded mode without CIDRs
            ("x-forwarded-for", None, "2"),
            ("cf-connecting-ip", "0.0.0.0/0", None),          # trust-everything wildcard
            ("x-forwarded-for", "::/0", "1"),
            ("cf-connecting-ip", "10.0.0.1/8", None),         # host bits set: likely a typo
            ("cf-connecting-ip", "not-a-cidr-secret-marker", None),
            ("cf-connecting-ip", "10.0.0.0/8,,", None),
            ("x-forwarded-for", TRUSTED, None),               # hops required
            ("x-forwarded-for", TRUSTED, "0"),
            ("x-forwarded-for", TRUSTED, "6"),
            ("x-forwarded-for", TRUSTED, "two"),
            ("cf-connecting-ip", TRUSTED, "1"),               # hops only for x-forwarded-for
            ("peer", TRUSTED, None),                          # stray settings in peer mode
            ("peer", None, "1"),
            ("cf-connecting-ip", ",".join(f"10.{i}.0.0/16" for i in range(65)), None),
        ]
        for mode, cidrs, hops in cases:
            with self.subTest(mode=mode, cidrs=cidrs, hops=hops):
                with self.assertRaises(ValueError) as caught:
                    self.client_ip.parse_client_ip_policy(mode, cidrs, hops)
                message = str(caught.exception)
                self.assertNotIn("secret-marker", message)
                self.assertNotIn("10.0.0.1", message)
                self.assertRegex(message, r"AUTH_(CLIENT_IP_SOURCE|TRUSTED_PROXY_(CIDRS|HOPS))")

    def test_valid_configurations_parse(self):
        policy = self.policy("X-Forwarded-For", " 10.0.0.0/8 , 2001:db8:ffff::/48 ", "2")
        self.assertEqual((policy.mode, policy.proxy_hops, len(policy.trusted_proxies)), ("x-forwarded-for", 2, 2))
        self.assertEqual(self.policy("cf-connecting-ip").proxy_hops, None)

    def test_startup_validation_fails_safely_for_invalid_settings(self):
        for values in ({"AUTH_CLIENT_IP_SOURCE": "x-forwarded-for"},
                       {"AUTH_CLIENT_IP_SOURCE": "cf-connecting-ip", "AUTH_TRUSTED_PROXY_CIDRS": "0.0.0.0/0"},
                       {"AUTH_TRUSTED_PROXY_HOPS": "2"}):
            with self.subTest(values=values):
                self.configure(**values)
                with self.assertRaises(ValueError):
                    self.runtime.validate_runtime()
                for name in values:
                    os.environ.pop(name)
        self.client_ip.client_ip_policy.cache_clear()
        self.runtime.validate_runtime()  # defaults are valid

    # ---- Trust boundary: the immediate TCP peer ------------------------------------

    def test_headers_ignored_when_peer_is_not_trusted(self):
        for mode, hops, headers in (("cf-connecting-ip", None, [("CF-Connecting-IP", USER_A)]),
                                    ("x-forwarded-for", "1", [("X-Forwarded-For", USER_A)])):
            policy = self.policy(mode, hops=hops)
            for peer in (ATTACKER, "203.0.113.200", "::1", None, "testclient"):
                with self.subTest(mode=mode, peer=peer):
                    self.assertEqual(self.identity(policy, peer, headers), legacy_peer_identity(peer))

    def test_ipv4_mapped_peer_inside_trusted_ipv4_range_is_trusted(self):
        policy = self.policy("cf-connecting-ip")
        self.assertEqual(self.identity(policy, "::ffff:10.20.30.40", [("CF-Connecting-IP", USER_A)]), USER_A)

    # ---- cf-connecting-ip mode ---------------------------------------------------

    def test_cf_connecting_ip_valid_missing_malformed_duplicated_ambiguous(self):
        policy = self.policy("cf-connecting-ip")
        self.assertEqual(self.identity(policy, headers=[("CF-Connecting-IP", USER_A)]), USER_A)
        self.assertEqual(self.identity(policy, headers=[("CF-Connecting-IP", f" {USER_A} ")]), USER_A)
        for headers in ([], [("CF-Connecting-IP", "")], [("CF-Connecting-IP", "not-an-ip")],
                        [("CF-Connecting-IP", f"{USER_A}, {USER_B}")],
                        [("CF-Connecting-IP", USER_A), ("CF-Connecting-IP", USER_B)],
                        [("CF-Connecting-IP", "fe80::1%eth0")], [("CF-Connecting-IP", "x" * 100)]):
            with self.subTest(headers=headers):
                self.assertEqual(self.identity(policy, headers=headers), RENDER_PEER)

    # ---- x-forwarded-for mode ----------------------------------------------------

    def test_xff_selects_entry_appended_by_first_trusted_proxy(self):
        policy = self.policy("x-forwarded-for", hops="2")
        self.assertEqual(self.identity(policy, headers=[("X-Forwarded-For", f"{USER_A}, {EDGE_PROXY}")]), USER_A)
        one_hop = self.policy("x-forwarded-for", hops="1")
        self.assertEqual(self.identity(one_hop, headers=[("X-Forwarded-For", USER_A)]), USER_A)

    def test_xff_spoofed_leftmost_entries_are_ignored(self):
        policy = self.policy("x-forwarded-for", hops="2")
        for forged in ("1.1.1.1", "8.8.8.8, 9.9.9.9", USER_B, "garbage", ""):
            with self.subTest(forged=forged):
                chain = f"{forged}, {USER_A}, {EDGE_PROXY}"
                self.assertEqual(self.identity(policy, headers=[("X-Forwarded-For", chain)]), USER_A)

    def test_xff_hop_count_is_not_proof_without_trusted_intermediaries(self):
        policy = self.policy("x-forwarded-for", hops="2")
        for chain in (f"{USER_B}, {ATTACKER}",              # intermediate hop is not a trusted proxy
                      f"{USER_A}",                          # chain shorter than the hop count
                      f"{EDGE_PROXY}, {EDGE_PROXY}",        # "client" is itself a trusted proxy
                      f"{USER_A}, not-an-ip",               # malformed entry inside the trusted window
                      f"not-an-ip, {EDGE_PROXY}",
                      f"{USER_A},, {EDGE_PROXY}",           # empty element: ambiguous
                      f"{USER_A}, ",
                      ",".join([USER_A] * 31 + [USER_B, EDGE_PROXY])):   # too many entries
            with self.subTest(chain=chain):
                self.assertEqual(self.identity(policy, headers=[("X-Forwarded-For", chain)]), RENDER_PEER)

    def test_xff_repeated_headers_are_one_ordered_list(self):
        policy = self.policy("x-forwarded-for", hops="2")
        headers = [("X-Forwarded-For", f"1.1.1.1, {USER_A}"), ("X-Forwarded-For", EDGE_PROXY)]
        self.assertEqual(self.identity(policy, headers=headers), USER_A)
        self.assertEqual(self.identity(policy, headers=[("X-Forwarded-For", f"{USER_A}:443, [{EDGE_PROXY}]")]), RENDER_PEER)
        self.assertEqual(self.identity(policy, headers=[("X-Forwarded-For", f"{USER_A}:443, {EDGE_PROXY}:8080")]), USER_A)
        self.assertEqual(self.identity(policy, headers=[("X-Forwarded-For", f"[2001:db8::5]:443, {EDGE_PROXY}")]), "2001:db8::/64")
        self.assertEqual(self.identity(policy, headers=[]), RENDER_PEER)

    # ---- Normalization -------------------------------------------------------------

    def test_ipv4_ipv4_mapped_and_ipv6_slash64_normalization(self):
        bucket = self.client_ip.client_bucket
        address = ipaddress.ip_address
        self.assertEqual(bucket(address("203.0.113.5")), "203.0.113.5")
        self.assertEqual(bucket(address("::ffff:203.0.113.5")), "203.0.113.5")
        self.assertEqual(bucket(address("2001:db8:1:2:aaaa::1")), "2001:db8:1:2::/64")
        self.assertEqual(bucket(address("2001:db8:1:2:ffff:ffff:ffff:ffff")), "2001:db8:1:2::/64")
        self.assertNotEqual(bucket(address("2001:db8:1:3::1")), "2001:db8:1:2::/64")
        policy = self.policy("cf-connecting-ip")
        self.assertEqual(self.identity(policy, headers=[("CF-Connecting-IP", "::ffff:203.0.113.5")]), "203.0.113.5")
        self.assertEqual(self.identity(policy, headers=[("CF-Connecting-IP", "2001:DB8::1")]), "2001:db8::/64")

    # ---- Rate-limit enforcement and storage ----------------------------------------

    def exhaust(self, peer, headers):
        limit = self.limits.resource_limits().rates["auth"].limit
        for _ in range(limit):
            self.admission.consume_rate(self.admission.authentication_subject(request(peer, headers)), "auth")

    def consume(self, peer, headers):
        self.admission.consume_rate(self.admission.authentication_subject(request(peer, headers)), "auth")

    def test_peer_mode_shares_one_bucket_behind_a_proxy(self):
        self.exhaust(RENDER_PEER, [("CF-Connecting-IP", USER_A)])
        with self.assertRaises(self.admission.ResourceRejected):
            self.consume(RENDER_PEER, [("CF-Connecting-IP", USER_B)])

    def test_forwarded_mode_separates_users_behind_a_shared_proxy(self):
        self.configure(AUTH_CLIENT_IP_SOURCE="cf-connecting-ip", AUTH_TRUSTED_PROXY_CIDRS=TRUSTED)
        self.exhaust(RENDER_PEER, [("CF-Connecting-IP", USER_A)])
        with self.assertRaises(self.admission.ResourceRejected) as caught:
            self.consume(RENDER_PEER, [("CF-Connecting-IP", USER_A)])
        self.assertEqual(caught.exception.status_code, 429)
        self.consume(RENDER_PEER, [("CF-Connecting-IP", USER_B)])  # a different user is unaffected

    def test_rotating_spoofed_entries_never_creates_new_buckets(self):
        self.configure(AUTH_CLIENT_IP_SOURCE="x-forwarded-for", AUTH_TRUSTED_PROXY_CIDRS=TRUSTED,
                       AUTH_TRUSTED_PROXY_HOPS="2")
        limit = self.limits.resource_limits().rates["auth"].limit
        for index in range(limit):
            self.consume(RENDER_PEER, [("X-Forwarded-For", f"192.0.2.{index}, {ATTACKER}, {EDGE_PROXY}")])
        with self.assertRaises(self.admission.ResourceRejected):
            self.consume(RENDER_PEER, [("X-Forwarded-For", f"192.0.2.250, {ATTACKER}, {EDGE_PROXY}")])
        # Malformed headers fall back to the shared peer bucket; they cannot mint fresh buckets.
        for index in range(limit):
            self.consume(RENDER_PEER, [("X-Forwarded-For", f"junk-{index}")])
        with self.assertRaises(self.admission.ResourceRejected):
            self.consume(RENDER_PEER, [("X-Forwarded-For", "junk-final")])

    def test_redis_keys_remain_hashed_and_contain_no_addresses(self):
        self.configure(AUTH_CLIENT_IP_SOURCE="cf-connecting-ip", AUTH_TRUSTED_PROXY_CIDRS=TRUSTED)
        self.consume(RENDER_PEER, [("CF-Connecting-IP", USER_A)])
        self.consume(RENDER_PEER, [("CF-Connecting-IP", "2001:db8::77")])
        for key in self.rates.entries:
            self.assertRegex(key, r"^resource:v1:rate:auth:[0-9a-f]{64}$")
            for raw in (USER_A, "2001:db8", RENDER_PEER):
                self.assertNotIn(raw, key)

    # ---- Real app: enforcement, privacy, CSRF and auth unchanged --------------------

    def app_client(self, peer):
        from fastapi.testclient import TestClient
        from app.database import database
        self.stack.enter_context(patch.dict(self.main.app.dependency_overrides, {database.get_db: lambda: None}))
        return self.stack.enter_context(TestClient(self.main.app, client=(peer, 50000)))

    def test_real_app_rate_limits_and_never_exposes_addresses(self):
        self.configure(AUTH_CLIENT_IP_SOURCE="cf-connecting-ip", AUTH_TRUSTED_PROXY_CIDRS=TRUSTED)
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        handler.setFormatter(self.obs.SafeJSONFormatter())
        root = logging.getLogger()
        root.addHandler(handler)
        self.addCleanup(root.removeHandler, handler)
        client = self.app_client(RENDER_PEER)
        limit = self.limits.resource_limits().rates["auth"].limit
        responses = [client.post("/auth/logout", headers={"CF-Connecting-IP": USER_A}) for _ in range(limit + 1)]
        self.assertEqual({r.status_code for r in responses[:limit]}, {200})
        self.assertEqual(responses[-1].status_code, 429)
        self.assertEqual(client.post("/auth/logout", headers={"CF-Connecting-IP": USER_B}).status_code, 200)
        visible = output.getvalue() + "".join(r.text + str(dict(r.headers)) for r in responses)
        for raw in (USER_A, USER_B, RENDER_PEER):
            self.assertNotIn(raw, visible)

    def test_csrf_rejection_still_precedes_rate_limiting(self):
        client = self.app_client(RENDER_PEER)
        before = self.rates.calls
        response = client.post("/auth/logout", headers={"Origin": "https://untrusted.example.test"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.rates.calls, before)
        self.assertEqual(client.post("/auth/logout", headers={"Origin": "http://localhost:3000"}).status_code, 200)

    def test_only_authentication_routes_use_ip_identity(self):
        source = open(self.main.__file__.replace("main.py", "app/routes/auth.py"), encoding="utf-8").read()
        self.assertEqual(len(re.findall(r"Depends\(limit_authentication\)", source)), 4)
        users = open(self.admission.__file__, encoding="utf-8").read()
        self.assertIn('consume_rate(f"user:{user_id}", category)', users)


if __name__ == "__main__":
    unittest.main(verbosity=2)
