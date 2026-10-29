"""Offline tests for the deployment safety checks.

No network, Docker, Git repository or production path is touched: commands go
to an in-memory fake that records every argv (after the real safety guard has
checked it) and the identity it would run as. GitHub responses are fixtures.
Run with:

    python3 -m unittest discover -s ops/deploy/tests -v
"""

from __future__ import annotations

import email.message
import errno as errno_mod
import io
import json
import logging
import os
import stat
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import autodeploy as ad  # noqa: E402

logging.disable(logging.CRITICAL)

A, B, C = "a" * 40, "b" * 40, "c" * 40
REPO = "HishamFekri/ai-document-assistant"
REPO_DIR = "/srv/storage/projects/ai-document-assistant"
REPO_OWNER = (1000, 1000)
OLD_BACKEND = "sha256:" + "1" * 64
OLD_FRONTEND = "sha256:" + "2" * 64
NEW_BACKEND = "sha256:" + "3" * 64
NEW_FRONTEND = "sha256:" + "4" * 64
OLD = {"ai-backend": OLD_BACKEND, "ai-frontend": OLD_FRONTEND}
NEW = {"ai-backend": NEW_BACKEND, "ai-frontend": NEW_FRONTEND}


def run_fixture(sha=B, **overrides):
    run = {
        "id": 100, "run_attempt": 1, "name": "CI", "path": ".github/workflows/ci.yml",
        "head_sha": sha, "head_branch": "main", "event": "push",
        "status": "completed", "conclusion": "success",
        "repository": {"full_name": REPO}, "head_repository": {"full_name": REPO},
    }
    run.update(overrides)
    return run


def runs_payload(*runs):
    return {"total_count": len(runs), "workflow_runs": list(runs)}


class FakeGitHub:
    def __init__(self, head=B, runs=None, error=None):
        self.head = head
        self.runs = runs if runs is not None else runs_payload(run_fixture(head))
        self.error = error
        self.calls = []

    def branch_head(self):
        self.calls.append("branch")
        if self.error:
            raise self.error
        return self.head

    def ci_runs(self, sha):
        self.calls.append(("runs", sha))
        if self.error:
            raise self.error
        return self.runs


def _git_parts(argv):
    """Split a git argv into (uses -C, subcommand, rest)."""
    i, uses_c = 1, False
    while i < len(argv):
        if argv[i] == "-C":
            uses_c = True
            i += 2
        elif argv[i] == "-c":
            i += 2
        elif argv[i].startswith("-"):
            i += 1
        else:
            return uses_c, argv[i], argv[i + 1:]
    raise AssertionError(argv)


class FakeServer(ad.Runner):
    """Simulates git, docker and the storage check. Every argv passes the real guard."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.identities: list[tuple[list[str], object]] = []
        self.history = [A, B, C]
        self.head = B
        self.local_branch = "main"
        self.origin_url = f"https://github.com/{REPO}.git"
        self.dirty = ""
        self.origin_main = B
        self.remote_heads: list[str] = []  # successive ls-remote answers; default origin_main
        self.changed: dict[tuple[str, str], list[str]] = {}
        self.storage_rc = 0
        self.build_rc = 0
        self.compose_version = "2.29.7"
        self.fail_on: list[tuple] = []  # (predicate(argv), exception)
        self.ignore_recreate = False
        self.broken_tag = False
        self.no_healthcheck = False
        self.health_after_up = {"ai-backend": ["starting", "healthy"], "ai-frontend": ["healthy"]}
        self.rollback_health = {"ai-backend": "healthy", "ai-frontend": "healthy"}
        self.refs = {"assistant-backend": OLD_BACKEND, "assistant-frontend": OLD_FRONTEND}
        self.build_output = {"assistant-backend": NEW_BACKEND, "assistant-frontend": NEW_FRONTEND}
        self.existing_images = set(OLD.values()) | set(NEW.values())
        self.compose_model = {
            "services": {
                "backend": {"container_name": "ai-backend",
                            "build": {"context": REPO_DIR + "/backend"}},
                "frontend": {"container_name": "ai-frontend",
                             "build": {"context": REPO_DIR + "/frontend"}},
                "postgres": {"container_name": "hesham-postgres", "image": "pgvector/pgvector"},
            }
        }
        self.labels_override: dict[str, dict] = {}
        self.containers = {
            "ai-backend": {"service": "backend", "ref": "assistant-backend", "image": OLD_BACKEND,
                           "health": ["healthy"], "status": "running"},
            "ai-frontend": {"service": "frontend", "ref": "assistant-frontend", "image": OLD_FRONTEND,
                            "health": ["healthy"], "status": "running"},
        }
        self.tags: dict[str, str] = {}

    # -- dispatch ---------------------------------------------------------
    def run(self, argv, timeout, as_user=None):
        ad.assert_command_allowed(argv)
        self.calls.append(list(argv))
        self.identities.append((list(argv), as_user))
        for predicate, exc in self.fail_on:
            if predicate(list(argv)):
                raise exc
        prog = os.path.basename(argv[0])
        if prog == "git":
            return self._git(argv)
        if prog == "docker":
            return self._docker(argv[1:])
        if argv[0] == "/srv/docker/apps/check-storage.sh":
            return ad.Result(self.storage_rc)
        raise AssertionError(f"unexpected command {argv}")

    def _git(self, argv):
        _, sub, rest = _git_parts(argv)
        if sub == "remote":
            return ad.Result(0, self.origin_url + "\n")
        if sub == "symbolic-ref":
            return ad.Result(0 if self.local_branch else 1, (self.local_branch or "") + "\n")
        if sub == "status":
            return ad.Result(0, self.dirty)
        if sub == "rev-parse":
            ref = rest[-1]
            sha = self.head if ref.startswith("HEAD") else self.origin_main
            return ad.Result(0, sha + "\n")
        if sub == "fetch":
            return ad.Result(0)
        if sub == "ls-remote":
            sha = self.remote_heads.pop(0) if self.remote_heads else self.origin_main
            return ad.Result(0, f"{sha}\trefs/heads/main\n")
        if sub == "merge-base":
            old, new = rest[-2], rest[-1]
            if old not in self.history or new not in self.history:
                return ad.Result(1)
            return ad.Result(0 if self.history.index(old) <= self.history.index(new) else 1)
        if sub == "diff":
            old, new = rest[-3], rest[-2]
            return ad.Result(0, "".join(p + "\0" for p in self.changed.get((old, new), [])))
        if sub in ("merge", "reset"):
            self.head = rest[-1]
            return ad.Result(0)
        raise AssertionError(f"unexpected git {sub}")

    def _resolve(self, ref):
        if ref in self.tags:
            return self.tags[ref]
        if ref in self.refs:
            return self.refs[ref]
        if ref in self.existing_images:
            return ref
        return None

    def _docker(self, args):
        if args[:2] == ["compose", "version"]:
            return ad.Result(0, self.compose_version + "\n")
        if args[0] == "compose":
            assert args[1:3] == ["--project-name", "ai-document-assistant"], args
            sub = args[7]
            if sub == "config":
                assert "--no-interpolate" in args
                return ad.Result(0, json.dumps(self.compose_model))
            if sub == "build":
                if self.build_rc == 0:
                    self.refs.update(self.build_output)
                return ad.Result(self.build_rc, "", "build log line\n")
            if sub == "up":
                service = args[-1]
                name = next(n for n, c in self.containers.items() if c["service"] == service)
                c = self.containers[name]
                if self.ignore_recreate:
                    return ad.Result(0)
                assert "--force-recreate" in args
                c["image"] = self.refs[c["ref"]]
                c["status"] = "running"
                if c["image"] in NEW.values():
                    c["health"] = list(self.health_after_up[name])
                else:
                    c["health"] = [self.rollback_health[name]]
                return ad.Result(0)
        if args[0] == "inspect":
            name = args[-1]
            c = self.containers[name]
            health = c["health"].pop(0) if len(c["health"]) > 1 else c["health"][0]
            labels = {
                "com.docker.compose.project": "ai-document-assistant",
                "com.docker.compose.project.working_dir": "/srv/docker/ai-document-assistant",
                "com.docker.compose.project.config_files": "/srv/docker/ai-document-assistant/compose.yaml",
                "com.docker.compose.service": c["service"],
            }
            labels.update(self.labels_override.get(name, {}))
            state = {"Status": c["status"]}
            if not self.no_healthcheck:
                state["Health"] = {"Status": health}
            info = {"Name": "/" + name, "Image": c["image"],
                    "Config": {"Image": c["ref"], "Labels": labels}, "State": state}
            return ad.Result(0, json.dumps([info]))
        if args[:2] == ["image", "inspect"]:
            image = self._resolve(args[-1])
            return ad.Result(0 if image else 1, (image or "") + "\n", "" if image else "No such image")
        if args[:2] == ["image", "tag"]:
            src, dst = args[2], args[3]
            image = self._resolve(src)
            assert image, src
            if dst.startswith(ad.ROLLBACK_REPO):
                self.tags[dst] = image
            elif not self.broken_tag:
                self.refs[dst] = image
            return ad.Result(0)
        if args[:2] == ["image", "rm"]:
            self.tags.pop(args[2], None)
            return ad.Result(0)
        raise AssertionError(f"unexpected docker {args}")

    # -- helpers ------------------------------------------------------------
    def mutating_calls(self):
        out = []
        for argv in self.calls:
            if argv[0] == "git":
                if _git_parts(argv)[1] in ("merge", "reset", "fetch"):
                    out.append(argv)
            elif argv[:2] == ["docker", "compose"] and len(argv) > 8 and argv[8] in ("build", "up"):
                out.append(argv)
            elif argv[:3] in (["docker", "image", "tag"], ["docker", "image", "rm"]):
                out.append(argv)
        return out

    def ups(self):
        return [a[-1] for a in self.calls if a[:2] == ["docker", "compose"] and len(a) > 8 and a[8] == "up"]

    def images(self):
        return {n: c["image"] for n, c in self.containers.items()}


def server_stat(path):
    """Repository owned by uid 1000; everything else root-owned and safe."""
    if path.startswith(REPO_DIR):
        return os.stat_result((stat.S_IFDIR | 0o755, 0, 0, 1, REPO_OWNER[0], REPO_OWNER[1], 0, 0, 0, 0))
    return os.stat_result((stat.S_IFREG | 0o755, 0, 0, 1, 0, 0, 100, 0, 0, 0))


def interrupt_on(*words):
    """Predicate matching an argv that contains every word, in order of appearance."""
    def predicate(argv):
        return all(w in argv for w in words)
    return predicate


class DeployTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.check_dir = os.path.join(self.tmp.name, "check")
        os.makedirs(self.check_dir)
        self.cfg = ad.Config(state_dir="/var/lib/x", check_state_dir=self.check_dir)
        self.cfg.health_timeout = 60
        self.store = ad.StateStore(os.path.join(self.tmp.name, "state"))
        os.makedirs(self.store.dir)
        self.server = FakeServer()
        self.gh = FakeGitHub(head=C)
        self.server.origin_main = C
        self.clock = [0.0]
        self.members: list[str] = []
        self.write_state(deployed=B)
        self.write_candidate(C)

    def write_state(self, deployed=B, images=None, **extra):
        state = {"version": 1, "deployed_commit": deployed, "previous": None, "failed": {},
                 "approved": None, "rollback_tags": [], "last_event": None,
                 "images": dict(OLD if images is None else images), "in_progress": None}
        state.update(extra)
        self.store.save(state)

    def write_candidate(self, sha, raw=None):
        with open(self.cfg.candidate_path, "w", encoding="utf-8") as fh:
            fh.write(raw if raw is not None else json.dumps({"version": 1, "sha": sha}))

    probe_result = None  # None: every identity-switch step succeeds

    def deployer(self, stat_fn=server_stat, code_paths=(), euid=0):
        def sleep(seconds):
            self.clock[0] += seconds

        def probe(uid, gid):
            self.probed = (uid, gid)
            if self.probe_result is not None:
                return self.probe_result
            return [(label, "ok") for label, _ in ad.identity_switch_steps(uid, gid)]
        return ad.Deployer(self.cfg, self.store, runner=self.server, github=self.gh,
                           stat_fn=stat_fn, sleep=sleep, monotonic=lambda: self.clock[0],
                           code_paths=code_paths, checker_uid=os.stat(self.check_dir).st_uid,
                           docker_group_members=lambda: list(self.members),
                           identity_probe=probe, euid=lambda: euid)

    def state(self):
        return self.store.load()

    def assert_nothing_changed(self):
        self.assertEqual([a for a in self.server.mutating_calls() if "fetch" not in a], [])
        self.assertEqual(self.server.head, B)
        self.assertEqual(self.server.images(), OLD)
        self.assertEqual(self.state()["deployed_commit"], B)

    def deploy_c(self):
        """Deploy C successfully (B -> C) and reset call logs."""
        self.assertEqual(self.deployer().run(), ad.EXIT_OK)
        self.server.calls.clear()
        self.server.identities.clear()


# ---------------------------------------------------------------------------
# CI verdicts and GitHub response validation
# ---------------------------------------------------------------------------

class CIEvaluationTests(unittest.TestCase):
    cfg = ad.Config()

    def verdict(self, *runs, sha=B):
        return ad.evaluate_ci_runs(runs_payload(*runs), sha, self.cfg).state

    def test_success(self):
        self.assertEqual(self.verdict(run_fixture()), "success")

    def test_workflow_path_with_ref_suffix_is_accepted(self):
        self.assertEqual(self.verdict(run_fixture(path=".github/workflows/ci.yml@refs/heads/main")),
                         "success")

    def test_missing_run(self):
        self.assertEqual(self.verdict(), "missing")

    def test_pending_states(self):
        for status in ("queued", "in_progress", "waiting", "requested", "pending"):
            with self.subTest(status=status):
                self.assertEqual(self.verdict(run_fixture(status=status, conclusion=None)), "pending")

    def test_failure_conclusions(self):
        for conclusion in ("failure", "cancelled", "timed_out", "skipped", "neutral", "action_required"):
            with self.subTest(conclusion=conclusion):
                self.assertEqual(self.verdict(run_fixture(conclusion=conclusion)), "failure")

    def test_latest_run_decides(self):
        self.assertEqual(self.verdict(run_fixture(id=100), run_fixture(id=200, conclusion="failure")),
                         "failure")
        self.assertEqual(self.verdict(run_fixture(id=100, conclusion="failure"), run_fixture(id=200)),
                         "success")

    def test_rerun_attempt_decides(self):
        self.assertEqual(self.verdict(run_fixture(run_attempt=2, conclusion="failure")), "failure")

    def test_any_unfinished_run_is_pending_even_after_success(self):
        self.assertEqual(self.verdict(run_fixture(id=300),
                                      run_fixture(id=200, status="in_progress", conclusion=None)),
                         "pending")

    def test_rejects_runs_that_do_not_match_exactly(self):
        bad = {
            "other sha": run_fixture(head_sha=C),
            "other branch": run_fixture(head_branch="production-hardening"),
            "pull request": run_fixture(event="pull_request"),
            "manual dispatch": run_fixture(event="workflow_dispatch"),
            "schedule": run_fixture(event="schedule"),
            "other workflow": run_fixture(path=".github/workflows/other.yml"),
            "lookalike workflow": run_fixture(path=".github/workflows/ci.yml.evil"),
            "fork head": run_fixture(head_repository={"full_name": "evil/ai-document-assistant"}),
            "other repo": run_fixture(repository={"full_name": "evil/x"}),
            "missing repo": run_fixture(head_repository=None),
            "bool id": run_fixture(id=True),
            "string id": run_fixture(id="100"),
            "unknown status": run_fixture(status="done"),
            "completed without conclusion": run_fixture(conclusion=None),
        }
        for label, run in bad.items():
            with self.subTest(label):
                with self.assertRaises(ad.DeployError):
                    ad.evaluate_ci_runs(runs_payload(run), B, self.cfg)

    def test_rejects_malformed_payloads(self):
        for payload in ({}, {"workflow_runs": []}, {"total_count": 0},
                        {"total_count": "1", "workflow_runs": [run_fixture()]},
                        {"total_count": 0, "workflow_runs": {}},
                        {"total_count": True, "workflow_runs": [run_fixture()]},
                        {"total_count": 2, "workflow_runs": [run_fixture()]},
                        {"total_count": 1, "workflow_runs": ["x"]}):
            with self.subTest(payload=payload):
                with self.assertRaises(ad.DeployError):
                    ad.evaluate_ci_runs(payload, B, self.cfg)

    def test_rejects_invalid_target_sha(self):
        with self.assertRaises(ad.DeployError):
            ad.evaluate_ci_runs(runs_payload(), "B" * 40, self.cfg)

    def test_branch_head_validation(self):
        self.assertEqual(ad.parse_branch_head({"name": "main", "commit": {"sha": B}}, "main"), B)
        for data in ({"name": "dev", "commit": {"sha": B}}, {"name": "main"},
                     {"name": "main", "commit": {"sha": B[:12]}}, {"name": "main", "commit": "x"}):
            with self.subTest(data=data):
                with self.assertRaises(ad.DeployError):
                    ad.parse_branch_head(data, "main")


class FakeResponse:
    def __init__(self, body, status=200, ctype="application/json; charset=utf-8"):
        self.status = status
        self.headers = {"Content-Type": ctype}
        self._body = body if isinstance(body, bytes) else json.dumps(body).encode()

    def read(self, n=-1):
        return self._body if n < 0 else self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code, remaining=None):
    headers = email.message.Message()
    if remaining is not None:
        headers["X-RateLimit-Remaining"] = remaining
    return urllib.error.HTTPError("https://api.github.com/x", code, "err", headers, io.BytesIO(b""))


class GitHubClientTests(unittest.TestCase):
    def client(self, outcome, token=None):
        self.requests = []

        def opener(req, timeout):
            self.requests.append(req)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        return ad.GitHub(ad.Config(), token, opener=opener)

    def test_valid_response(self):
        gh = self.client(FakeResponse({"name": "main", "commit": {"sha": B}}))
        self.assertEqual(gh.branch_head(), B)
        req = self.requests[0]
        self.assertTrue(req.full_url.startswith("https://api.github.com/repos/HishamFekri/"))
        self.assertEqual(req.get_method(), "GET")
        self.assertIsNone(req.get_header("Authorization"))

    def test_runs_query_is_scoped_to_push_on_main_for_one_sha(self):
        gh = self.client(FakeResponse(runs_payload()))
        gh.ci_runs(B)
        url = self.requests[0].full_url
        for part in ("/actions/workflows/ci.yml/runs", "branch=main", "event=push", f"head_sha={B}"):
            self.assertIn(part, url)

    def test_token_is_unredirected_header(self):
        token = "github_pat_" + "x" * 30
        gh = self.client(FakeResponse({"name": "main", "commit": {"sha": B}}), token=token)
        gh.branch_head()
        req = self.requests[0]
        self.assertNotIn("Authorization", req.headers)
        self.assertEqual(req.unredirected_hdrs.get("Authorization"), f"Bearer {token}")

    def test_failures_fail_closed_without_leaking_token(self):
        token = "github_pat_" + "s" * 30
        cases = {
            "rate limited 403": http_error(403, remaining="0"),
            "rate limited 429": http_error(429, remaining="0"),
            "forbidden": http_error(403),
            "server error": http_error(502),
            "redirect": http_error(301),
            "network": urllib.error.URLError("down"),
            "timeout": TimeoutError(),
            "html": FakeResponse(b"<html>", ctype="text/html"),
            "invalid json": FakeResponse(b"{not json"),
            "json list": FakeResponse([1, 2]),
            "status 204": FakeResponse(b"{}", status=204),
            "oversized": FakeResponse(b" " * (ad.MAX_API_BYTES + 1) + b"{}"),
        }
        for label, outcome in cases.items():
            with self.subTest(label):
                with self.assertRaises(ad.DeployError) as ctx:
                    self.client(outcome, token=token).get("/repos/x/y")
                self.assertNotIn(token, str(ctx.exception))

    def test_rate_limit_message(self):
        with self.assertRaisesRegex(ad.DeployError, "rate limit"):
            self.client(http_error(403, remaining="0")).get("/x")

    def test_redirects_are_refused(self):
        handler = ad._NoRedirect()
        req = urllib.request.Request("https://api.github.com/x")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            handler.redirect_request(req, None, 302, "Found", email.message.Message(), "https://evil.test/")
        ctx.exception.close()


class TokenTests(unittest.TestCase):
    def test_token_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t")
            with open(path, "w") as fh:
                fh.write("github_pat_" + "a" * 30 + "\n")
            self.assertTrue(ad.read_token(path).startswith("github_pat_"))
            with open(path, "w") as fh:
                fh.write("bad token with spaces")
            with self.assertRaises(ad.DeployError) as ctx:
                ad.read_token(path)
            self.assertNotIn("spaces", str(ctx.exception))

    def test_no_token_by_default(self):
        old = os.environ.pop("CREDENTIALS_DIRECTORY", None)
        try:
            self.assertIsNone(ad.read_token(""))
        finally:
            if old is not None:
                os.environ["CREDENTIALS_DIRECTORY"] = old


# ---------------------------------------------------------------------------
# Pure helpers, configuration and the command guard
# ---------------------------------------------------------------------------

class ChangeClassificationTests(unittest.TestCase):
    def test_migrations_and_tooling_need_review(self):
        found = ad.classify_changes([
            "backend/migrations/versions/x.py", "backend/migrations/env.py", "backend/alembic.ini",
            "ops/deploy/autodeploy.py", "backend/app/main.py", "frontend/src/a.tsx",
        ])
        self.assertEqual(found["migrations"], ["backend/migrations/versions/x.py",
                                               "backend/migrations/env.py", "backend/alembic.ini"])
        self.assertEqual(found["tooling"], ["ops/deploy/autodeploy.py"])

    def test_lookalike_paths_are_not_migrations(self):
        found = ad.classify_changes(["backend/migrations_old/x.py", "backend/alembic.ini.bak",
                                     "docs/backend/migrations/x.md"])
        self.assertEqual(found, {"migrations": [], "tooling": []})


class ConfigTests(unittest.TestCase):
    def test_defaults_match_production(self):
        cfg = ad.parse_config("")
        self.assertEqual(cfg.repo_dir, REPO_DIR)
        self.assertEqual(cfg.compose_file, "/srv/docker/ai-document-assistant/compose.yaml")
        self.assertEqual(cfg.containers, ("ai-backend", "ai-frontend"))
        self.assertEqual(cfg.candidate_path, "/var/lib/ai-document-assistant-check/candidate.json")

    def test_example_file_parses(self):
        path = os.path.join(os.path.dirname(__file__), "..", "deploy.conf.example")
        with open(path, encoding="utf-8") as fh:
            ad.parse_config(fh.read())

    def test_rejects_unmanaged_containers(self):
        for value in ("hesham-postgres", "ai-backend,hesham-redis", "hesham-portfolio", "ai-backend,ai-backend"):
            with self.subTest(value=value):
                with self.assertRaises(ad.DeployError):
                    ad.parse_config(f"CONTAINERS={value}")

    def test_rejects_bad_values(self):
        for text in ("UNKNOWN=1", "BUILD_TIMEOUT=0", "BUILD_TIMEOUT=abc", "REPO_DIR=relative/path",
                     "REPO_DIR=/srv/../etc", "WORKFLOW_FILE=../x.yml", "BRANCH=a..b", "garbage",
                     "CHECKER_USER=root", "CHECKER_USER=Bad User",
                     "CHECK_STATE_DIR=/var/lib/ai-document-assistant-deploy"):
            with self.subTest(text=text):
                with self.assertRaises(ad.DeployError):
                    ad.parse_config(text)


class CommandGuardTests(unittest.TestCase):
    base = ["docker", "compose", "--project-name", "p", "--project-directory", "/d", "--file", "/d/c.yaml"]

    def test_allows_the_commands_the_tool_uses(self):
        for argv in (
            ["git", "-C", "/r", "-c", "core.hooksPath=/dev/null", "fetch", "origin"],
            ["git", "-c", "credential.helper=", "ls-remote", "--exit-code", "https://github.com/x/y.git"],
            ["git", "-C", "/r", "merge", "--ff-only", B],
            ["git", "-C", "/r", "reset", "--keep", B],
            [*self.base, "build", "backend"],
            [*self.base, "up", "--detach", "--no-deps", "--no-build", "--pull", "never",
             "--force-recreate", "backend"],
            ["docker", "inspect", "--type", "container", "ai-backend"],
            ["docker", "image", "tag", OLD_BACKEND, "ai-document-assistant-rollback:ai-backend-x"],
            ["docker", "image", "rm", "ai-document-assistant-rollback:ai-backend-x"],
        ):
            with self.subTest(argv=argv):
                ad.assert_command_allowed(argv)

    def test_refuses_destructive_commands(self):
        for argv in (
            [*self.base, "down"],
            [*self.base, "rm", "backend"],
            [*self.base, "stop", "backend"],
            [*self.base, "restart", "backend"],
            [*self.base, "run", "backend"],
            [*self.base, "up", "-d", "backend"],
            [*self.base, "up", "--force-recreate", "backend"],
            [*self.base, "up", "--no-deps", "--no-build", "--remove-orphans", "backend"],
            [*self.base, "up", "--no-deps", "--no-build", "-V", "backend"],
            [*self.base, "up", "--no-deps", "--no-build", "--renew-anon-volumes", "backend"],
            [*self.base, "up", "--no-deps", "--no-build", "--always-recreate-deps", "backend"],
            [*self.base, "up", "--no-deps", "--no-build", "postgres", "hesham-postgres"],
            ["docker", "restart", "hesham-redis"],
            ["docker", "rm", "-f", "ai-backend"],
            ["docker", "stop", "ai-backend"],
            ["docker", "system", "prune"],
            ["docker", "volume", "rm", "x"],
            ["docker", "image", "rm", "-f", "ai-document-assistant-rollback:x"],
            ["docker", "image", "rm", "pgvector/pgvector"],
            ["docker", "image", "prune"],
            ["docker", "stop", "hesham-portfolio"],
            ["systemctl", "restart", "docker"],
            ["sudo", "docker", "ps"],
            ["chown", "-R", "x", "/srv"],
            ["git", "-C", "/r", "push", "origin", "main"],
            ["git", "-C", "/r", "reset", "--hard", B],
            ["git", "-C", "/r", "clean", "-fdx"],
            ["git", "-C", "/r", "checkout", "--force", B],
            ["git", "-C", "/r", "merge", B],
            ["git", "-C", "/r", "fetch", "--force", "origin"],
            ["git", "-C", "/r", "config", "core.hooksPath", "/x"],
            ["git", "-C", "/r", "remote", "set-url", "origin", "x"],
            ["bash", "-c", "anything"],
            [],
        ):
            with self.subTest(argv=argv):
                with self.assertRaises(ad.DeployError):
                    ad.assert_command_allowed(argv)

    def test_child_environment_carries_no_secrets(self):
        os.environ["GITHUB_TOKEN"] = "secret-value"
        os.environ["DATABASE_URL"] = "postgresql://u:p@h/db"
        try:
            env, git_env = ad.minimal_env("docker"), ad.minimal_env("git")
        finally:
            del os.environ["GITHUB_TOKEN"], os.environ["DATABASE_URL"]
        for e in (env, git_env):
            self.assertNotIn("GITHUB_TOKEN", e)
            self.assertNotIn("DATABASE_URL", e)
        self.assertEqual(git_env["GIT_CONFIG_GLOBAL"], "/dev/null")
        self.assertEqual(git_env["HOME"], "/nonexistent")

    def test_trusted_file_rules(self):
        def st(mode, uid=0):
            return os.stat_result((stat.S_IFREG | mode, 0, 0, 1, uid, 0, 1, 0, 0, 0))
        self.assertIsNone(ad.trusted_file_problem(st(0o644)))
        self.assertIsNotNone(ad.trusted_file_problem(st(0o664)))
        self.assertIsNotNone(ad.trusted_file_problem(st(0o646)))
        self.assertIsNotNone(ad.trusted_file_problem(st(0o644, uid=1000)))
        self.assertIsNotNone(ad.trusted_file_problem(st(0o644), executable=True))


# ---------------------------------------------------------------------------
# Checker (unprivileged role)
# ---------------------------------------------------------------------------

class CheckerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = ad.Config(check_state_dir=self.tmp.name)
        self.server = FakeServer()
        self.server.origin_main = C
        self.gh = FakeGitHub(head=C)

    def checker(self, stat_fn=server_stat, code_paths=()):
        return ad.Checker(self.cfg, runner=self.server, github=self.gh, stat_fn=stat_fn,
                          code_paths=code_paths)

    def candidate(self):
        try:
            with open(self.cfg.candidate_path, encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            return None

    def test_success_publishes_candidate(self):
        self.assertEqual(self.checker().check(), ad.EXIT_OK)
        self.assertEqual(self.candidate()["sha"], C)

    def test_never_touches_docker_or_the_production_checkout(self):
        self.checker().check()
        for argv in self.server.calls:
            self.assertEqual(argv[0], "git")
            self.assertEqual(_git_parts(argv)[1], "ls-remote")
            self.assertNotIn("-C", argv)
            self.assertIn(f"https://github.com/{REPO}.git", argv)

    def test_uncertain_ci_publishes_nothing(self):
        for label, payload in {
            "pending": runs_payload(run_fixture(C, status="queued", conclusion=None)),
            "missing": runs_payload(),
            "failure": runs_payload(run_fixture(C, conclusion="failure")),
        }.items():
            with self.subTest(label):
                self.gh.runs = payload
                self.assertEqual(self.checker().check(), ad.EXIT_OK)
                self.assertIsNone(self.candidate())

    def test_api_error_publishes_nothing(self):
        self.gh.error = ad.DeployError("GitHub API rate limit exhausted")
        with self.assertRaises(ad.DeployError):
            self.checker().check()
        self.assertIsNone(self.candidate())

    def test_api_and_git_disagree(self):
        self.gh.head = "e" * 40
        self.checker().check()
        self.assertIsNone(self.candidate())

    def test_no_api_calls_once_published(self):
        self.checker().check()
        self.gh.calls.clear()
        self.checker().check()
        self.assertEqual(self.gh.calls, [])

    def test_new_head_replaces_candidate(self):
        self.server.origin_main, self.gh.head = B, B
        self.gh.runs = runs_payload(run_fixture(B))
        self.checker().check()
        self.assertEqual(self.candidate()["sha"], B)
        self.server.origin_main, self.gh.head = C, C
        self.gh.runs = runs_payload(run_fixture(C))
        self.checker().check()
        self.assertEqual(self.candidate()["sha"], C)

    def test_refuses_writable_installation(self):
        def writable(_):
            return os.stat_result((stat.S_IFREG | 0o666, 0, 0, 1, 0, 0, 1, 0, 0, 0))
        with self.assertRaises(ad.UnsafeStateError):
            self.checker(stat_fn=writable, code_paths=["/opt/x/autodeploy.py"]).check()
        self.assertEqual(self.server.calls, [])


class CandidateFileTests(DeployTestCase):
    def test_absent(self):
        os.remove(self.cfg.candidate_path)
        self.assertIsNone(self.deployer().read_candidate())

    def test_valid(self):
        self.assertEqual(self.deployer().read_candidate(), C)

    def test_rejects_untrusted_content(self):
        for raw in ("{", "[]", json.dumps({"version": 2, "sha": C}), json.dumps({"version": 1, "sha": "x"}),
                    json.dumps({"version": 1}), " " * (ad.MAX_CANDIDATE_BYTES + 10)):
            with self.subTest(raw=raw[:30]):
                self.write_candidate(None, raw=raw)
                with self.assertRaises(ad.UnsafeStateError):
                    self.deployer().read_candidate()

    def test_rejects_wrong_owner(self):
        d = self.deployer()
        d._checker_uid = os.stat(self.check_dir).st_uid + 1
        with self.assertRaisesRegex(ad.UnsafeStateError, "owned"):
            d.read_candidate()

    def test_rejects_directory(self):
        os.remove(self.cfg.candidate_path)
        os.mkdir(self.cfg.candidate_path)
        with self.assertRaises(ad.UnsafeStateError):
            self.deployer().read_candidate()

    def test_rejects_symlink(self):
        target = os.path.join(self.tmp.name, "elsewhere.json")
        with open(target, "w") as fh:
            fh.write(json.dumps({"version": 1, "sha": C}))
        os.remove(self.cfg.candidate_path)
        try:
            os.symlink(target, self.cfg.candidate_path)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaises(ad.UnsafeStateError):
            self.deployer().read_candidate()


# ---------------------------------------------------------------------------
# Executor gates
# ---------------------------------------------------------------------------

class RunGateTests(DeployTestCase):
    def test_no_candidate_makes_no_network_calls(self):
        os.remove(self.cfg.candidate_path)
        self.assertEqual(self.deployer().run(), ad.EXIT_OK)
        self.assertEqual(self.gh.calls, [])
        self.assertFalse(any(_git_parts(a)[1] in ("fetch", "ls-remote") for a in self.server.calls
                             if a[0] == "git"))
        self.assert_nothing_changed()

    def test_up_to_date_makes_no_network_calls(self):
        self.write_candidate(B)
        self.assertEqual(self.deployer().run(), ad.EXIT_OK)
        self.assertEqual(self.gh.calls, [])
        self.assert_nothing_changed()

    def test_stale_candidate_waits(self):
        self.server.origin_main = "f" * 40
        self.assertEqual(self.deployer().run(), ad.EXIT_OK)
        self.assertEqual(self.gh.calls, [])
        self.assert_nothing_changed()

    def test_candidate_is_reverified_not_trusted(self):
        # The checker said C is good; the executor's own CI check disagrees.
        self.gh.runs = runs_payload(run_fixture(C, conclusion="failure"))
        self.assertEqual(self.deployer().run(), ad.EXIT_OK)
        self.assertIn(("runs", C), self.gh.calls)
        self.assert_nothing_changed()

    def test_git_runs_as_checkout_owner_and_docker_as_root(self):
        self.deployer().run()
        for argv, identity in self.server.identities:
            if argv[0] == "git":
                self.assertEqual(identity, REPO_OWNER, argv)
            else:
                self.assertIsNone(identity, argv)

    def test_checkout_and_git_dir_owner_mismatch_blocks(self):
        def mixed(path):
            if path.endswith("/.git"):
                return os.stat_result((stat.S_IFDIR | 0o755, 0, 0, 1, 0, 0, 0, 0, 0, 0))
            return server_stat(path)
        with self.assertRaisesRegex(ad.UnsafeStateError, "owners"):
            self.deployer(stat_fn=mixed).run()

    def test_writable_installation_blocks(self):
        def writable(path):
            if path.startswith("/opt"):
                return os.stat_result((stat.S_IFREG | 0o664, 0, 0, 1, 0, 1000, 1, 0, 0, 0))
            return server_stat(path)
        with self.assertRaisesRegex(ad.UnsafeStateError, "writable"):
            self.deployer(stat_fn=writable, code_paths=["/opt/ai-document-assistant-deploy/autodeploy.py"]).run()
        self.assertEqual(self.server.calls, [])

    def test_dirty_tree_blocks(self):
        self.server.dirty = " M backend/app/main.py\n"
        with self.assertRaisesRegex(ad.UnsafeStateError, "not clean"):
            self.deployer().run()
        self.assert_nothing_changed()

    def test_untracked_files_block(self):
        self.server.dirty = "?? stray.sh\n"
        with self.assertRaises(ad.UnsafeStateError):
            self.deployer().run()

    def test_wrong_branch_blocks(self):
        self.server.local_branch = "feature"
        with self.assertRaisesRegex(ad.UnsafeStateError, "branch"):
            self.deployer().run()

    def test_detached_head_blocks(self):
        self.server.local_branch = None
        with self.assertRaises(ad.UnsafeStateError):
            self.deployer().run()

    def test_unexpected_origin_blocks(self):
        self.server.origin_url = "https://github.com/evil/ai-document-assistant.git"
        with self.assertRaisesRegex(ad.UnsafeStateError, "origin"):
            self.deployer().run()

    def test_head_must_match_recorded_deployment(self):
        self.server.head = A
        with self.assertRaisesRegex(ad.UnsafeStateError, "recorded deployed commit"):
            self.deployer().run()

    def test_containers_changed_outside_the_tool_block(self):
        self.server.containers["ai-frontend"]["image"] = NEW_FRONTEND
        with self.assertRaisesRegex(ad.UnsafeStateError, "recorded image"):
            self.deployer().run()
        self.assertEqual(self.gh.calls, [])

    def test_missing_recorded_images_block(self):
        self.write_state(deployed=B, images={})
        with self.assertRaises(ad.UnsafeStateError):
            self.deployer().run()

    def test_interrupted_operation_blocks_every_run(self):
        self.write_state(deployed=B, in_progress={"action": "deploy", "phase": "build",
                                                  "from_commit": B, "to_commit": C})
        with self.assertRaisesRegex(ad.UnsafeStateError, "recover --abort"):
            self.deployer().run()
        self.assertEqual(self.server.calls, [])

    def test_missing_state_blocks(self):
        os.remove(self.store.state_path)
        with self.assertRaisesRegex(ad.UnsafeStateError, "init"):
            self.deployer().run()

    def test_storage_failure_blocks(self):
        self.server.storage_rc = 1
        with self.assertRaisesRegex(ad.UnsafeStateError, "storage"):
            self.deployer().run()
        self.assert_nothing_changed()
        self.assertEqual(self.gh.calls, [])

    def test_untrusted_storage_script_is_not_run(self):
        def storage(mode, uid=0, missing=False):
            def fn(path):
                if path == self.cfg.storage_check:
                    if missing:
                        raise FileNotFoundError
                    return os.stat_result((stat.S_IFREG | mode, 0, 0, 1, uid, 0, 1, 0, 0, 0))
                return server_stat(path)
            return fn
        for label, fn in {"writable": storage(0o777), "not root": storage(0o755, uid=1000),
                          "not executable": storage(0o644), "missing": storage(0, missing=True)}.items():
            with self.subTest(label):
                with self.assertRaises(ad.UnsafeStateError):
                    self.deployer(stat_fn=fn).run()
        self.assertFalse(any("check-storage" in a[0] for a in self.server.calls))

    def test_rewritten_history_blocks(self):
        self.server.origin_main = "d" * 40
        self.write_candidate("d" * 40)
        with self.assertRaisesRegex(ad.UnsafeStateError, "rewritten"):
            self.deployer().run()
        self.assert_nothing_changed()

    def test_migration_change_waits_for_approval(self):
        self.server.changed[(B, C)] = ["backend/migrations/versions/new.py", "backend/app/x.py"]
        self.assertEqual(self.deployer().run(), ad.EXIT_OK)
        self.assert_nothing_changed()
        self.assertEqual(self.gh.calls, [])
        self.assertEqual(self.state()["last_event"], [C, "awaiting_approval"])

    def test_waiting_is_recorded_once(self):
        self.server.changed[(B, C)] = ["backend/alembic.ini"]
        self.deployer().run()
        self.deployer().run()
        self.assertEqual(len(self.store.history(100)), 1)

    def test_tooling_change_waits_for_approval(self):
        self.server.changed[(B, C)] = ["ops/deploy/autodeploy.py"]
        self.deployer().run()
        self.assert_nothing_changed()

    def test_ci_pending_missing_failed_do_not_deploy(self):
        cases = {
            "pending": runs_payload(run_fixture(C, status="in_progress", conclusion=None)),
            "missing": runs_payload(),
            "failure": runs_payload(run_fixture(C, conclusion="failure")),
        }
        for label, payload in cases.items():
            with self.subTest(label):
                self.gh.runs = payload
                self.assertEqual(self.deployer().run(), ad.EXIT_OK)
                self.assert_nothing_changed()
                self.assertEqual(self.state()["last_event"], [C, f"ci_{label}"])

    def test_api_error_fails_closed(self):
        self.gh.error = ad.DeployError("GitHub API rate limit exhausted")
        with self.assertRaisesRegex(ad.DeployError, "rate limit"):
            self.deployer().run()
        self.assert_nothing_changed()

    def test_malformed_ci_response_fails_closed(self):
        self.gh.runs = runs_payload(run_fixture(C, event="pull_request"))
        with self.assertRaises(ad.DeployError):
            self.deployer().run()
        self.assert_nothing_changed()

    def test_api_and_git_disagree_on_head(self):
        self.gh.head = "e" * 40
        self.assertEqual(self.deployer().run(), ad.EXIT_OK)
        self.assert_nothing_changed()
        self.assertNotIn(("runs", C), self.gh.calls)

    def test_older_success_is_not_deployed_when_newer_commit_appears(self):
        self.server.remote_heads = ["f" * 40]
        self.assertEqual(self.deployer().run(), ad.EXIT_OK)
        self.assert_nothing_changed()

    def test_previously_failed_commit_is_skipped(self):
        self.write_state(deployed=B, failed={C: "build_failed"})
        self.assertEqual(self.deployer().run(), ad.EXIT_OK)
        self.assertEqual(self.gh.calls, [])
        self.assert_nothing_changed()

    def test_dry_run_changes_nothing(self):
        def read_state():
            with open(self.store.state_path, encoding="utf-8") as fh:
                return fh.read()
        before = read_state()
        self.assertEqual(self.deployer().run(dry_run=True), ad.EXIT_OK)
        self.assert_nothing_changed()
        self.assertEqual(read_state(), before)
        self.assertEqual(self.store.history(), [])

    def test_compose_labels_must_match_production_file(self):
        self.server.labels_override["ai-frontend"] = {
            "com.docker.compose.project.config_files": "/tmp/other.yaml"}
        with self.assertRaisesRegex(ad.UnsafeStateError, "compose.yaml"):
            self.deployer().run()
        self.assert_nothing_changed()

    def test_build_context_must_be_the_repository(self):
        self.server.compose_model["services"]["backend"]["build"] = {"context": "/opt/elsewhere"}
        with self.assertRaisesRegex(ad.UnsafeStateError, "does not build from"):
            self.deployer().run()
        self.server.compose_model["services"]["backend"]["build"] = {"context": "${CTX}"}
        with self.assertRaisesRegex(ad.UnsafeStateError, "literal"):
            self.deployer().run()
        self.assert_nothing_changed()

    def test_declared_image_must_match_running_reference(self):
        self.server.compose_model["services"]["backend"]["image"] = "someone/else"
        with self.assertRaisesRegex(ad.UnsafeStateError, "declares"):
            self.deployer().run()

    def test_old_compose_is_refused(self):
        self.server.compose_version = "2.12.0"
        with self.assertRaises(ad.UnsafeStateError):
            self.deployer().run()


# ---------------------------------------------------------------------------
# Deploy, automatic rollback and partial failures
# ---------------------------------------------------------------------------

class DeployFlowTests(DeployTestCase):
    def test_successful_deploy(self):
        self.assertEqual(self.deployer().run(), ad.EXIT_OK)
        state = self.state()
        self.assertEqual(state["deployed_commit"], C)
        self.assertEqual(state["images"], NEW)
        self.assertEqual(state["previous"], {"commit": B, "images": OLD, "tags": {
            "ai-backend": ad.rollback_tag("ai-backend", B), "ai-frontend": ad.rollback_tag("ai-frontend", B)}})
        self.assertIsNone(state["in_progress"])
        self.assertEqual(self.server.head, C)
        self.assertEqual(self.server.images(), NEW)
        self.assertEqual(self.server.tags[ad.rollback_tag("ai-backend", B)], OLD_BACKEND)
        self.assertEqual([e["result"] for e in self.store.history(10)], ["started", "success"])

    def test_only_managed_services_are_touched(self):
        self.deployer().run()
        for argv in self.server.calls:
            joined = " ".join(argv)
            for word in ("postgres", "redis", "portfolio"):
                self.assertNotIn(word, joined)
            if argv[:2] == ["docker", "compose"] and len(argv) > 8 and argv[8] in ("up", "build"):
                services = argv[9:] if argv[8] == "build" else argv[-1:]
                self.assertTrue(set(services) <= {"backend", "frontend"}, argv)
            if "up" in argv:
                for flag in ("--no-deps", "--no-build", "--force-recreate"):
                    self.assertIn(flag, argv)

    def test_order_backend_before_frontend_after_build(self):
        self.deployer().run()
        compose = [a[8] for a in self.server.calls if a[:2] == ["docker", "compose"] and len(a) > 8]
        self.assertEqual([s for s in compose if s in ("build", "up")], ["build", "up", "up"])
        self.assertEqual(self.server.ups(), ["backend", "frontend"])

    def test_unchanged_image_is_not_recreated(self):
        self.server.build_output["assistant-frontend"] = OLD_FRONTEND
        self.assertEqual(self.deployer().run(), ad.EXIT_OK)
        self.assertEqual(self.server.ups(), ["backend"])
        self.assertEqual(self.state()["images"], {"ai-backend": NEW_BACKEND, "ai-frontend": OLD_FRONTEND})

    def test_storage_is_rechecked_before_changes(self):
        self.deployer().run()
        checks = [i for i, a in enumerate(self.server.calls) if a[0].endswith("check-storage.sh")]
        merge = next(i for i, a in enumerate(self.server.calls) if a[0] == "git" and _git_parts(a)[1] == "merge")
        self.assertEqual(len(checks), 2)
        self.assertLess(checks[1], merge)

    def test_build_failure_leaves_containers_and_restores_checkout(self):
        self.server.build_rc = 1
        self.assertEqual(self.deployer().run(), ad.EXIT_ERROR)
        self.assertEqual(self.server.head, B)
        self.assertEqual(self.server.images(), OLD)
        self.assertEqual(self.server.ups(), [])
        state = self.state()
        self.assertEqual(state["deployed_commit"], B)
        self.assertEqual(state["failed"][C], "build_failed")
        self.assertIsNone(state["in_progress"])
        self.assertTrue(any(a[0] == "git" and _git_parts(a)[1] == "reset" and "--keep" in a
                            for a in self.server.calls))

    def test_unhealthy_backend_rolls_back(self):
        self.server.health_after_up["ai-backend"] = ["starting", "unhealthy"]
        self.assertEqual(self.deployer().run(), ad.EXIT_ERROR)
        self.assertEqual(self.server.images(), OLD)
        self.assertEqual(self.server.head, B)
        state = self.state()
        self.assertEqual(state["failed"][C], "rolled_back")
        self.assertEqual(state["deployed_commit"], B)
        self.assertEqual(state["images"], OLD)
        self.assertIsNone(state["in_progress"])
        # The frontend was never replaced, so it is not recreated during rollback either.
        self.assertEqual(self.server.ups(), ["backend", "backend"])

    def test_unhealthy_frontend_rolls_back_both(self):
        self.server.health_after_up["ai-frontend"] = ["unhealthy"]
        self.assertEqual(self.deployer().run(), ad.EXIT_ERROR)
        self.assertEqual(self.server.images(), OLD)
        self.assertEqual(self.server.ups(), ["backend", "frontend", "backend", "frontend"])

    def test_rollback_recreates_with_the_exact_previous_image(self):
        self.server.health_after_up["ai-frontend"] = ["unhealthy"]
        self.deployer().run()
        retags = [a for a in self.server.calls
                  if a[:3] == ["docker", "image", "tag"] and not a[4].startswith(ad.ROLLBACK_REPO)]
        # Rollback tags the recorded image *id* (not a mutable name) onto the Compose name.
        self.assertIn(["docker", "image", "tag", OLD_BACKEND, "assistant-backend"], retags)
        self.assertIn(["docker", "image", "tag", OLD_FRONTEND, "assistant-frontend"], retags)

    def test_health_timeout_rolls_back(self):
        self.server.health_after_up["ai-backend"] = ["starting"]
        self.assertEqual(self.deployer().run(), ad.EXIT_ERROR)
        self.assertGreaterEqual(self.clock[0], self.cfg.health_timeout)
        self.assertEqual(self.state()["failed"][C], "rolled_back")

    def test_missing_healthcheck_blocks_before_any_change(self):
        self.server.no_healthcheck = True
        with self.assertRaisesRegex(ad.UnsafeStateError, "not healthy"):
            self.deployer().run()
        self.assert_nothing_changed()

    def test_unhealthy_current_release_blocks_before_any_change(self):
        self.server.containers["ai-backend"]["health"] = ["unhealthy"]
        with self.assertRaisesRegex(ad.UnsafeStateError, "not healthy"):
            self.deployer().run()
        self.assert_nothing_changed()

    def test_container_not_actually_recreated_is_detected(self):
        self.server.ignore_recreate = True
        code = self.deployer().run()
        self.assertNotEqual(code, ad.EXIT_OK)
        self.assertNotEqual(self.state()["deployed_commit"], C)

    def test_retag_that_does_not_take_effect_is_detected(self):
        self.server.broken_tag = True
        self.server.health_after_up["ai-frontend"] = ["unhealthy"]
        self.assertEqual(self.deployer().run(), ad.EXIT_UNSAFE)
        state = self.state()
        self.assertEqual(state["in_progress"]["phase"], "rollback_failed")
        self.assertEqual(state["failed"][C], "rollback_failed")

    def test_partial_deploy_with_failed_backend_rollback(self):
        # Backend replaced fine, frontend fails, then restoring the backend fails.
        self.server.health_after_up["ai-frontend"] = ["unhealthy"]
        self.server.rollback_health["ai-backend"] = "unhealthy"
        self.assertEqual(self.deployer().run(), ad.EXIT_UNSAFE)
        state = self.state()
        self.assertEqual(state["in_progress"]["phase"], "rollback_failed")
        self.assertEqual(state["in_progress"]["to_images"], NEW)
        self.assertEqual(state["deployed_commit"], B)
        # The next run refuses instead of assuming the recorded state is real.
        self.server.calls.clear()
        with self.assertRaisesRegex(ad.UnsafeStateError, "recover"):
            self.deployer().run()
        self.assertEqual(self.server.calls, [])

    def test_partial_deploy_with_failed_frontend_rollback(self):
        self.server.health_after_up["ai-frontend"] = ["unhealthy"]
        self.server.rollback_health["ai-frontend"] = "unhealthy"
        self.assertEqual(self.deployer().run(), ad.EXIT_UNSAFE)
        self.assertEqual(self.server.images()["ai-backend"], OLD_BACKEND)
        self.assertEqual(self.state()["in_progress"]["phase"], "rollback_failed")
        # Fixed by hand, then aborted explicitly.
        self.server.rollback_health["ai-frontend"] = "healthy"
        self.assertEqual(self.deployer().recover("abort"), ad.EXIT_OK)
        self.assertEqual(self.server.images(), OLD)
        self.assertEqual(self.server.head, B)
        self.assertIsNone(self.state()["in_progress"])

    def test_approved_migration_deploys_but_never_auto_rolls_back(self):
        self.server.changed[(B, C)] = ["backend/migrations/versions/new.py"]
        self.write_state(deployed=B, approved=C)
        self.server.health_after_up["ai-backend"] = ["unhealthy"]
        self.assertEqual(self.deployer().run(), ad.EXIT_UNSAFE)
        self.assertEqual(self.server.images()["ai-backend"], NEW_BACKEND)
        self.assertEqual(self.server.ups(), ["backend"])
        state = self.state()
        self.assertEqual(state["failed"][C], "health_failed_migrations")
        self.assertEqual(state["in_progress"]["phase"], "failed_migrations")
        with self.assertRaisesRegex(ad.UnsafeStateError, "Rollback across a migration"):
            self.deployer().run()

    def test_approved_migration_success_clears_approval(self):
        self.server.changed[(B, C)] = ["backend/migrations/versions/new.py"]
        self.write_state(deployed=B, approved=C)
        self.assertEqual(self.deployer().run(), ad.EXIT_OK)
        self.assertIsNone(self.state()["approved"])
        self.assertEqual(self.state()["deployed_commit"], C)

    def test_approval_for_other_commit_does_not_apply(self):
        self.server.changed[(B, C)] = ["backend/migrations/versions/new.py"]
        self.write_state(deployed=B, approved=A)
        self.deployer().run()
        self.assert_nothing_changed()

    def test_approval_does_not_bypass_ci(self):
        self.server.changed[(B, C)] = ["backend/migrations/versions/new.py"]
        self.write_state(deployed=B, approved=C)
        self.gh.runs = runs_payload(run_fixture(C, conclusion="failure"))
        self.deployer().run()
        self.assert_nothing_changed()

    def test_old_rollback_tags_are_pruned(self):
        self.cfg.rollback_keep = 1
        old = [ad.rollback_tag("ai-backend", A), ad.rollback_tag("ai-frontend", A)]
        self.write_state(deployed=B, rollback_tags=old)
        self.deployer().run()
        removed = [a[-1] for a in self.server.calls if a[:3] == ["docker", "image", "rm"]]
        self.assertEqual(removed, old)
        self.assertEqual(len(self.state()["rollback_tags"]), 2)


# ---------------------------------------------------------------------------
# Interruption (kill / power loss) and explicit recovery
# ---------------------------------------------------------------------------

class InterruptionTests(DeployTestCase):
    def interrupt(self, predicate):
        self.server.fail_on = [(predicate, KeyboardInterrupt())]
        with self.assertRaises(KeyboardInterrupt):
            self.deployer().run()
        self.server.fail_on = []
        return self.state()["in_progress"]

    def assert_blocked(self, text):
        with self.assertRaisesRegex(ad.UnsafeStateError, text):
            self.deployer().run()

    def assert_back_to_old(self):
        self.assertEqual(self.server.head, B)
        self.assertEqual(self.server.images(), OLD)
        state = self.state()
        self.assertEqual(state["deployed_commit"], B)
        self.assertEqual(state["images"], OLD)
        self.assertIsNone(state["in_progress"])

    def test_interrupted_during_checkout_update(self):
        ip = self.interrupt(lambda a: a[0] == "git" and _git_parts(a)[1] == "merge")
        self.assertEqual(ip["phase"], "checkout")
        self.assert_blocked("recover --abort")
        self.assertEqual(self.deployer().recover("abort"), ad.EXIT_OK)
        self.assert_back_to_old()
        self.assertEqual(self.state()["failed"][C], "aborted")

    def test_interrupted_during_build(self):
        ip = self.interrupt(lambda a: a[:2] == ["docker", "compose"] and len(a) > 8 and a[8] == "build")
        self.assertEqual(ip["phase"], "build")
        self.assertEqual(self.server.head, C)  # checkout already moved
        self.assert_blocked("No container was changed")
        with self.assertRaisesRegex(ad.UnsafeStateError, "nothing was built"):
            self.deployer().recover("complete")
        self.assertEqual(self.deployer().recover("abort"), ad.EXIT_OK)
        self.assertEqual(self.server.head, B)
        self.assertEqual(self.server.images(), OLD)
        self.assertEqual(self.server.ups(), [])

    def test_interrupted_during_container_replacement_then_completed(self):
        ip = self.interrupt(interrupt_on("up", "frontend"))
        self.assertEqual(ip["phase"], "replace:ai-frontend")
        self.assertEqual(self.server.images(), {"ai-backend": NEW_BACKEND, "ai-frontend": OLD_FRONTEND})
        self.assert_blocked("split between the old and new images")
        d = self.deployer()
        text = d.describe_recovery(ip, d.resolve_project(), self.server.head)
        self.assertIn("ai-backend: " + NEW_BACKEND[7:19] + " (new release)", text)
        self.assertIn("ai-frontend: " + OLD_FRONTEND[7:19] + " (old release)", text)
        self.assertEqual(self.deployer().recover("complete"), ad.EXIT_OK)
        state = self.state()
        self.assertEqual(state["deployed_commit"], C)
        self.assertEqual(state["images"], NEW)
        self.assertEqual(state["previous"]["commit"], B)
        self.assertEqual(self.server.images(), NEW)

    def test_interrupted_during_container_replacement_then_aborted(self):
        self.interrupt(interrupt_on("up", "frontend"))
        self.assertEqual(self.deployer().recover("abort"), ad.EXIT_OK)
        self.assert_back_to_old()
        self.assertIsNone(self.state()["in_progress"])

    def test_interrupted_during_automatic_rollback(self):
        self.server.health_after_up["ai-frontend"] = ["unhealthy"]
        calls = {"n": 0}

        def second_backend_up(argv):
            if "up" in argv and argv[-1] == "backend":
                calls["n"] += 1
                return calls["n"] == 2
            return False
        ip = self.interrupt(second_backend_up)
        self.assertEqual(ip["phase"], "rollback:ai-backend")
        self.assert_blocked("Restoring the previous images")
        self.assertEqual(self.deployer().recover("abort"), ad.EXIT_OK)
        self.assert_back_to_old()

    def test_interrupted_while_restoring_checkout_after_build_failure(self):
        self.server.build_rc = 1
        ip = self.interrupt(lambda a: a[0] == "git" and _git_parts(a)[1] == "reset")
        self.assertEqual(ip["phase"], "restore-checkout")
        self.assertEqual(self.deployer().recover("abort"), ad.EXIT_OK)
        self.assertEqual(self.server.head, B)

    def test_abort_across_migration_requires_downgrade_confirmation(self):
        self.server.changed[(B, C)] = ["backend/migrations/versions/new.py"]
        self.write_state(deployed=B, approved=C)
        self.interrupt(interrupt_on("up", "frontend"))
        with self.assertRaisesRegex(ad.UnsafeStateError, "database-downgraded"):
            self.deployer().recover("abort")
        self.assertEqual(self.deployer().recover("abort", database_downgraded=True), ad.EXIT_OK)
        self.assertEqual(self.server.images(), OLD)

    def test_recover_refuses_unknown_head(self):
        self.interrupt(lambda a: a[:2] == ["docker", "compose"] and len(a) > 8 and a[8] == "build")
        self.server.head = A
        with self.assertRaisesRegex(ad.UnsafeStateError, "neither"):
            self.deployer().recover("abort")

    def test_recovery_failure_keeps_the_journal(self):
        self.interrupt(interrupt_on("up", "frontend"))
        self.server.rollback_health["ai-backend"] = "unhealthy"
        self.assertEqual(self.deployer().recover("abort"), ad.EXIT_UNSAFE)
        self.assertTrue(self.state()["in_progress"]["phase"].startswith("recover-abort"))

    def test_adopt_accepts_running_state_explicitly(self):
        self.interrupt(interrupt_on("up", "frontend"))
        with self.assertRaisesRegex(ad.UnsafeStateError, "not HEAD"):
            self.deployer().recover("adopt", commit=B)
        self.assertEqual(self.deployer().recover("adopt", commit=C), ad.EXIT_OK)
        state = self.state()
        self.assertEqual(state["deployed_commit"], C)
        self.assertEqual(state["images"], {"ai-backend": NEW_BACKEND, "ai-frontend": OLD_FRONTEND})
        self.assertIsNone(state["in_progress"])

    def test_adopt_after_external_change(self):
        self.server.containers["ai-backend"]["image"] = NEW_BACKEND
        with self.assertRaises(ad.UnsafeStateError):
            self.deployer().run()
        self.deployer().recover("adopt", commit=B)
        self.assertEqual(self.state()["images"]["ai-backend"], NEW_BACKEND)

    def test_status_with_nothing_interrupted(self):
        self.assertEqual(self.deployer().recover("status"), ad.EXIT_OK)


# ---------------------------------------------------------------------------
# Manual rollback and operator commands
# ---------------------------------------------------------------------------

class OperatorCommandTests(DeployTestCase):
    def test_init_refuses_existing_state(self):
        with self.assertRaises(ad.UnsafeStateError):
            self.deployer().init(B)

    def test_init_records_head_and_running_images(self):
        os.remove(self.store.state_path)
        with self.assertRaises(ad.UnsafeStateError):
            self.deployer().init(A)
        self.assertEqual(self.deployer().init(B), ad.EXIT_OK)
        self.assertEqual(self.state()["deployed_commit"], B)
        self.assertEqual(self.state()["images"], OLD)

    def test_approve_requires_current_head(self):
        with self.assertRaisesRegex(ad.UnsafeStateError, "not the current head"):
            self.deployer().approve(A, backup_confirmed=True)

    def test_approve_migrations_requires_backup_confirmation(self):
        self.server.changed[(B, C)] = ["backend/migrations/versions/new.py"]
        with self.assertRaisesRegex(ad.UnsafeStateError, "backup"):
            self.deployer().approve(C, backup_confirmed=False)
        self.assertEqual(self.deployer().approve(C, backup_confirmed=True), ad.EXIT_OK)
        self.assertEqual(self.state()["approved"], C)

    def test_retry_clears_failure(self):
        self.write_state(deployed=B, failed={C: "build_failed"})
        self.deployer().retry(C)
        self.assertNotIn(C, self.state()["failed"])

    def test_rollback_restores_previous(self):
        self.deploy_c()
        self.assertEqual(self.deployer().rollback(), ad.EXIT_OK)
        state = self.state()
        self.assertEqual(state["deployed_commit"], B)
        self.assertEqual(state["images"], OLD)
        self.assertEqual(state["failed"][C], "rolled_back_manually")
        self.assertIsNone(state["in_progress"])
        self.assertEqual(self.server.head, B)
        self.assertEqual(self.server.images(), OLD)
        self.assertEqual(self.server.ups(), ["backend", "frontend"])
        # The bad commit is not redeployed by the next timer run.
        self.server.calls.clear()
        self.assertEqual(self.deployer().run(), ad.EXIT_OK)
        self.assertEqual([a for a in self.server.mutating_calls()], [])

    def test_rollback_dry_run(self):
        self.deploy_c()
        self.assertEqual(self.deployer().rollback(dry_run=True), ad.EXIT_OK)
        self.assertEqual(self.server.mutating_calls(), [])

    def test_failed_manual_rollback_is_journaled(self):
        self.deploy_c()
        self.server.rollback_health["ai-frontend"] = "unhealthy"
        self.assertEqual(self.deployer().rollback(), ad.EXIT_UNSAFE)
        state = self.state()
        self.assertEqual(state["in_progress"]["action"], "rollback")
        self.assertEqual(state["in_progress"]["phase"], "rollback_failed")
        self.assertEqual(state["deployed_commit"], C)
        # Fixed, then completed explicitly.
        self.server.rollback_health["ai-frontend"] = "healthy"
        self.assertEqual(self.deployer().recover("complete"), ad.EXIT_OK)
        self.assertEqual(self.state()["deployed_commit"], B)
        self.assertEqual(self.server.images(), OLD)
        self.assertEqual(self.server.head, B)

    def test_rollback_refused_when_previous_image_is_gone(self):
        self.deploy_c()
        self.server.existing_images.discard(OLD_FRONTEND)
        self.server.tags.clear()
        self.server.refs = {k: v for k, v in self.server.refs.items()}
        with self.assertRaisesRegex(ad.UnsafeStateError, "no longer present"):
            self.deployer().rollback()
        self.assertEqual(self.server.mutating_calls(), [])

    def test_rollback_refused_across_migrations(self):
        self.write_state(deployed=C, images=NEW, previous={"commit": B, "images": OLD, "tags": {}})
        self.server.head = C
        for c, image in NEW.items():
            self.server.containers[c]["image"] = image
        self.server.changed[(B, C)] = ["backend/migrations/versions/new.py"]
        with self.assertRaisesRegex(ad.UnsafeStateError, "migrations"):
            self.deployer().rollback()
        self.assertEqual(self.server.mutating_calls(), [])
        self.assertEqual(self.deployer().rollback(database_downgraded=True), ad.EXIT_OK)
        self.assertEqual(self.server.images(), OLD)

    def test_rollback_refused_while_interrupted(self):
        self.write_state(deployed=B, previous={"commit": A, "images": OLD},
                         in_progress={"action": "deploy", "phase": "build", "from_commit": B, "to_commit": C})
        with self.assertRaises(ad.UnsafeStateError):
            self.deployer().rollback()

    def test_rollback_without_previous(self):
        with self.assertRaises(ad.UnsafeStateError):
            self.deployer().rollback()


# ---------------------------------------------------------------------------
# doctor (read-only)
# ---------------------------------------------------------------------------

class DoctorTests(DeployTestCase):
    probed = None

    def doctor(self, **kw):
        out = io.StringIO()
        stdout, sys.stdout = sys.stdout, out
        try:
            code = self.deployer(**kw).doctor()
        finally:
            sys.stdout = stdout
        self.assertEqual(self.server.mutating_calls(), [], "doctor must never change anything")
        return code, out.getvalue()

    def line(self, report, name):
        return next(ln for ln in report.splitlines() if name in ln)

    def test_healthy_system_passes_without_changes(self):
        code, report = self.doctor()
        self.assertEqual(code, ad.EXIT_OK, report)
        self.assertIn("0 failed", report)
        self.assertTrue(self.line(report, "GitHub head and CI").startswith("PASS"))

    def test_runs_even_without_a_new_commit(self):
        self.write_candidate(B)
        self.server.origin_main = B
        self.gh.head = B
        self.gh.runs = runs_payload(run_fixture(B))
        code, report = self.doctor()
        self.assertEqual(code, ad.EXIT_OK, report)
        self.assertTrue(self.line(report, "compose labels").startswith("PASS"))

    def test_offline_skips_network(self):
        out = io.StringIO()
        stdout, sys.stdout = sys.stdout, out
        try:
            self.deployer().doctor(online=False)
        finally:
            sys.stdout = stdout
        self.assertEqual(self.gh.calls, [])
        self.assertFalse(any(a[0] == "git" and _git_parts(a)[1] == "ls-remote" for a in self.server.calls))

    def test_reports_each_failure_class(self):
        cases = {
            "compose labels": lambda s: s.labels_override.update(
                {"ai-backend": {"com.docker.compose.project.config_files": "/x.yaml"}}),
            "SSD verification": lambda s: setattr(s, "storage_rc", 3),
            "production checkout": lambda s: setattr(s, "dirty", " M x\n"),
            "recorded images": lambda s: s.containers["ai-backend"].update(image=NEW_BACKEND),
            "health checks": lambda s: s.containers["ai-frontend"].update(health=["unhealthy"]),
        }
        for name, mutate in cases.items():
            with self.subTest(name):
                self.server = FakeServer()
                self.server.origin_main = C
                mutate(self.server)
                code, report = self.doctor()
                self.assertEqual(code, ad.EXIT_UNSAFE, report)
                self.assertTrue(self.line(report, name).startswith("FAIL"), report)

    def test_missing_healthcheck_fails(self):
        self.server.no_healthcheck = True
        code, report = self.doctor()
        self.assertTrue(self.line(report, "health checks").startswith("FAIL"))

    def test_interrupted_operation_fails_with_advice(self):
        self.write_state(deployed=B, in_progress={"action": "deploy", "phase": "replace:ai-frontend",
                                                  "from_commit": B, "to_commit": C})
        code, report = self.doctor()
        self.assertEqual(code, ad.EXIT_UNSAFE)
        self.assertIn("recover --status", self.line(report, "deployment state"))

    def test_image_name_drift_warns(self):
        self.server.refs["assistant-backend"] = NEW_BACKEND
        code, report = self.doctor()
        self.assertTrue(self.line(report, "image references").startswith("WARN"))

    def test_checker_in_docker_group_fails(self):
        self.members = ["ai-deploy-check"]
        code, report = self.doctor()
        self.assertTrue(self.line(report, "privilege separation").startswith("FAIL"))

    def test_other_docker_group_members_warn(self):
        self.members = ["hesham"]
        code, report = self.doctor()
        self.assertTrue(self.line(report, "privilege separation").startswith("WARN"))

    def test_writable_installation_fails(self):
        def writable(path):
            if path.startswith("/opt"):
                return os.stat_result((stat.S_IFREG | 0o666, 0, 0, 1, 0, 0, 1, 0, 0, 0))
            return server_stat(path)
        code, report = self.doctor(stat_fn=writable, code_paths=["/opt/x/autodeploy.py"])
        self.assertTrue(self.line(report, "installed code").startswith("FAIL"))

    def test_identity_switch_passes_as_checkout_owner(self):
        code, report = self.doctor()
        self.assertTrue(self.line(report, "git identity switch").startswith("PASS"), report)
        self.assertEqual(self.probed, REPO_OWNER)

    def test_identity_switch_failure_names_the_refused_step(self):
        self.probe_result = [("exec, no switch", "ok"), ("+setgroups([])", "ok"),
                             ("+setregid", "ok"), ("+setreuid", "FAILED EPERM at pre-exec")]
        code, report = self.doctor()
        self.assertEqual(code, ad.EXIT_UNSAFE)
        line = self.line(report, "git identity switch")
        self.assertTrue(line.startswith("FAIL"))
        self.assertIn("+setreuid: FAILED EPERM at pre-exec", line)
        self.assertIn("uid 1000/gid 1000", line)

    def test_identity_switch_skipped_when_not_root(self):
        code, report = self.doctor(euid=1000)
        self.assertTrue(self.line(report, "git identity switch").startswith("WARN"))

    def test_missing_rollback_image_fails(self):
        self.deploy_c()
        self.write_candidate(C)
        self.server.existing_images.discard(OLD_BACKEND)
        self.server.tags.clear()
        self.gh.head = C
        code, report = self.doctor()
        self.assertTrue(self.line(report, "rollback images").startswith("FAIL"), report)


# ---------------------------------------------------------------------------
# Spawn diagnostics: distinguish fork/credential, chdir and exec failures
# ---------------------------------------------------------------------------

EPERM = errno_mod.EPERM


class SpawnDiagnosticsTests(unittest.TestCase):
    def test_classify_by_cpython_error_reporting(self):
        self.assertEqual(ad.classify_spawn_error(PermissionError(EPERM, "Operation not permitted"), "/"),
                         "pre-exec")
        self.assertEqual(ad.classify_spawn_error(OSError(errno_mod.ENOENT, "No such file", "/"), "/"), "chdir")
        self.assertEqual(ad.classify_spawn_error(PermissionError(EPERM, "Operation not permitted", "git"), "/"),
                         "exec")

    def test_security_context_parsing(self):
        status = ("Name:\tpython3\nCapEff:\t00000000000000e4\nCapBnd:\t00000000000000e4\n"
                  "NoNewPrivs:\t1\nSeccomp:\t2\nSeccomp_filters:\t3\nUid:\t0\t0\t0\t0\n")
        fields = ad.parse_security_context(status)
        self.assertEqual(fields["Seccomp"], "2")
        self.assertNotIn("Uid", fields)
        self.assertEqual(ad.describe_caps(fields["CapEff"]),
                         "CAP_DAC_READ_SEARCH+CAP_KILL+CAP_SETGID+CAP_SETUID")
        self.assertEqual(ad.describe_caps("0000000000000024"), "CAP_DAC_READ_SEARCH+CAP_KILL")
        self.assertEqual(ad.describe_caps("0"), "none")
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "status")
            with open(path, "w") as fh:
                fh.write(status)
            text = ad.security_context(path)
        self.assertIn("CapEff=00000000000000e4(CAP_DAC_READ_SEARCH+CAP_KILL+CAP_SETGID+CAP_SETUID)", text)
        self.assertIn("NoNewPrivs=1", text)
        self.assertIn("Seccomp_filters=3", text)
        self.assertEqual(ad.security_context(os.path.join(d, "missing")), "security context unavailable")

    def runner_error(self, exc, euid=0):
        def fake_run(*args, **kwargs):
            self.spawn_kwargs = kwargs
            raise exc
        with mock.patch.object(ad.subprocess, "run", fake_run), \
                mock.patch.object(ad.os, "geteuid", create=True, return_value=euid):
            with self.assertRaises(ad.DeployError) as ctx:
                ad.Runner().run(["git", "-C", REPO_DIR, "status"], 10, as_user=REPO_OWNER)
        return str(ctx.exception)

    def test_runner_reports_pre_exec_credential_failure(self):
        message = self.runner_error(PermissionError(EPERM, "Operation not permitted"))
        self.assertEqual(self.spawn_kwargs["user"], 1000)
        self.assertEqual(self.spawn_kwargs["extra_groups"], [])
        self.assertIn("Operation not permitted", message)
        self.assertIn("stage=pre-exec", message)
        self.assertIn("setgroups/setregid/setreuid", message)
        self.assertIn("errno=EPERM", message)
        self.assertIn("uid/gid switch to 1000/1000", message)

    def test_runner_reports_exec_failure(self):
        message = self.runner_error(PermissionError(EPERM, "Operation not permitted", "git"))
        self.assertIn("stage=exec: execve failed", message)

    def test_runner_without_switch_says_so(self):
        message = self.runner_error(PermissionError(EPERM, "Operation not permitted"), euid=1000)
        self.assertNotIn("user", self.spawn_kwargs)
        self.assertIn("no uid/gid switch", message)

    def test_runner_diagnostics_never_include_environment(self):
        os.environ["DATABASE_URL"] = "postgresql://u:secret@h/db"
        try:
            message = self.runner_error(PermissionError(EPERM, "Operation not permitted"))
        finally:
            del os.environ["DATABASE_URL"]
        self.assertNotIn("secret", message)

    def probe(self, fail_when):
        calls = []

        def spawn(argv, **kwargs):
            calls.append(kwargs)
            exc = fail_when(kwargs)
            if exc:
                raise exc
            return subprocess.CompletedProcess(argv, 0, "git version 2.43.0\n", "")
        return ad.probe_identity_switch(1000, 1000, spawn=spawn), calls

    def test_probe_all_steps_ok(self):
        results, calls = self.probe(lambda kw: None)
        self.assertEqual([r for _, r in results], ["ok"] * 4)
        self.assertEqual(calls[-1]["user"], 1000)
        self.assertEqual(calls[-1]["group"], 1000)
        self.assertEqual(calls[-1]["extra_groups"], [])
        for kw in calls:  # same spawn settings as Runner
            self.assertEqual(kw["cwd"], "/")
            self.assertEqual(kw["env"]["GIT_CONFIG_GLOBAL"], "/dev/null")

    def test_probe_pinpoints_setreuid(self):
        results, _ = self.probe(lambda kw: PermissionError(EPERM, "x") if "user" in kw else None)
        self.assertEqual(results[-1], ("+setreuid", "FAILED EPERM at pre-exec"))
        self.assertEqual([r for _, r in results[:3]], ["ok"] * 3)

    def test_probe_pinpoints_setgroups_and_stops(self):
        results, calls = self.probe(lambda kw: PermissionError(EPERM, "x") if "extra_groups" in kw else None)
        self.assertEqual(results, [("exec, no switch", "ok"), ("+setgroups([])", "FAILED EPERM at pre-exec")])
        self.assertEqual(len(calls), 2)

    def test_probe_distinguishes_exec_failure(self):
        results, _ = self.probe(lambda kw: PermissionError(EPERM, "x", "git"))
        self.assertEqual(results, [("exec, no switch", "FAILED EPERM at exec")])

    def test_probe_command_is_allowed_but_nothing_else_new(self):
        ad.assert_command_allowed(["git", "--version"])
        for argv in (["git", "--version", "push"], ["git", "-C", "/r", "--version"]):
            with self.assertRaises(ad.DeployError):
                ad.assert_command_allowed(argv)


# ---------------------------------------------------------------------------
# systemd units: privileges and sandbox must not regress
# ---------------------------------------------------------------------------

UNIT_DIR = os.path.join(os.path.dirname(__file__), "..", "systemd")
EXECUTOR_UNIT = "ai-document-assistant-deploy.service"
DOCTOR_UNIT = "ai-document-assistant-deploy-doctor.service"
CHECKER_UNIT = "ai-document-assistant-check.service"
ROOT_SANDBOX = {
    "NoNewPrivileges": "yes",
    "CapabilityBoundingSet": "CAP_SETUID CAP_SETGID CAP_KILL CAP_DAC_READ_SEARCH",
    "AmbientCapabilities": "",
    "UMask": "0077",
    "PrivateTmp": "yes",
    "ProtectSystem": "strict",
    "ProtectHome": "yes",
    "ReadWritePaths": REPO_DIR,
    "ProtectKernelTunables": "yes",
    "ProtectKernelModules": "yes",
    "ProtectKernelLogs": "yes",
    "ProtectControlGroups": "yes",
    "ProtectClock": "yes",
    "ProtectHostname": "yes",
    "RestrictAddressFamilies": "AF_UNIX AF_INET AF_INET6",
    "RestrictNamespaces": "yes",
    "RestrictRealtime": "yes",
    "RestrictSUIDSGID": "yes",
    "LockPersonality": "yes",
    "SystemCallArchitectures": "native",
}


def read_unit(name):
    """Return ({section: {key: [values]}}, raw text). Comments are ignored."""
    with open(os.path.join(UNIT_DIR, name), encoding="utf-8") as fh:
        text = fh.read()
    sections: dict[str, dict[str, list[str]]] = {}
    current = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = sections.setdefault(line[1:-1], {})
            continue
        key, _, value = line.partition("=")
        current.setdefault(key.strip(), []).append(value.strip())
    return sections, text


class SystemdUnitTests(unittest.TestCase):
    def service(self, name):
        return read_unit(name)[0]["Service"]

    def test_executor_and_doctor_do_not_set_user(self):
        # systemd v255 drops CAP_SETUID for an explicit User= (even root)
        # under NoNewPrivileges; git can then not switch to the checkout owner.
        for name in (EXECUTOR_UNIT, DOCTOR_UNIT):
            with self.subTest(name):
                service = self.service(name)
                self.assertNotIn("User", service)
                self.assertNotIn("DynamicUser", service)
                self.assertNotIn("Group", service)

    def test_root_sandbox_is_unchanged(self):
        for name in (EXECUTOR_UNIT, DOCTOR_UNIT):
            service = self.service(name)
            for key, want in ROOT_SANDBOX.items():
                with self.subTest(unit=name, key=key):
                    self.assertEqual(service.get(key), [want])

    def test_ambient_capabilities_are_not_used_as_a_workaround(self):
        for name in (EXECUTOR_UNIT, DOCTOR_UNIT, CHECKER_UNIT):
            with self.subTest(name):
                self.assertEqual(self.service(name).get("AmbientCapabilities"), [""])

    def test_doctor_uses_the_executor_sandbox(self):
        ignore = {"ExecStart", "TimeoutStartSec", "SuccessExitStatus"}
        executor = {k: v for k, v in self.service(EXECUTOR_UNIT).items() if k not in ignore}
        doctor = {k: v for k, v in self.service(DOCTOR_UNIT).items() if k not in ignore}
        self.assertEqual(executor, doctor)
        self.assertTrue(self.service(EXECUTOR_UNIT)["ExecStart"][0].endswith(" run"))
        self.assertTrue(self.service(DOCTOR_UNIT)["ExecStart"][0].endswith(" doctor"))

    def test_docker_engine_is_never_started_by_these_units(self):
        for name in (EXECUTOR_UNIT, DOCTOR_UNIT):
            unit = read_unit(name)[0]["Unit"]
            self.assertEqual(unit.get("Requisite"), ["docker.service"])
            self.assertNotIn("Requires", unit)

    def test_units_explain_why_user_is_unset(self):
        for name in (EXECUTOR_UNIT, DOCTOR_UNIT):
            with self.subTest(name):
                self.assertIn("Do NOT set User=", read_unit(name)[1])

    def test_checker_keeps_its_unprivileged_user_and_sandbox(self):
        service = self.service(CHECKER_UNIT)
        self.assertEqual(service["User"], ["ai-deploy-check"])
        self.assertEqual(service["Group"], ["ai-deploy-check"])
        self.assertEqual(service["CapabilityBoundingSet"], [""])
        self.assertEqual(service["NoNewPrivileges"], ["yes"])
        self.assertIn("-/run/docker.sock", service["InaccessiblePaths"][0])
        self.assertTrue(service["ExecStart"][0].endswith(" check"))


@unittest.skipUnless(sys.platform.startswith("linux"), "flock is Linux-only")
class LockTests(unittest.TestCase):
    def test_second_lock_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            first, second = ad.StateStore(d), ad.StateStore(d)
            first.lock()
            try:
                with self.assertRaises(ad.LockedError):
                    second.lock()
            finally:
                first.unlock()
            second.lock()
            second.unlock()


class StateTests(unittest.TestCase):
    def test_corrupt_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            store = ad.StateStore(d)
            for content in ("{", "[]", json.dumps({"version": 2}),
                            json.dumps({"version": 1, "deployed_commit": "abc"})):
                with open(store.state_path, "w") as fh:
                    fh.write(content)
                with self.subTest(content=content):
                    with self.assertRaises(ad.UnsafeStateError):
                        store.load()


if __name__ == "__main__":
    unittest.main()
