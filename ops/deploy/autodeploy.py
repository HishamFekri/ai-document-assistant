#!/usr/bin/env python3
"""Pull-based auto-deployment for the self-hosted AI Document Assistant.

Two roles, run by two systemd units from this one root-owned file:

* `check` runs every few minutes as the unprivileged `ai-deploy-check` user. It
  has no Docker access and never touches the production checkout. It asks
  GitHub for the head of main and the ci.yml result for that exact commit, and
  writes `candidate.json` when CI succeeded.
* `run` (the executor) runs as root, triggered by that file and by a backstop
  timer. It treats the candidate only as a hint and re-verifies every gate
  itself before changing anything:

  - the target is the current head of origin/main (git and the GitHub API
    agree, checked again right before the first change);
  - ci.yml completed successfully for that exact commit, for a push to main in
    this repository (not a fork, not a pull request);
  - the external SSD passes the existing UUID verification script;
  - the production checkout is clean, on main, at the recorded deployed commit,
    and the running containers use the recorded images;
  - the change does not touch migrations or this tooling, unless an operator
    approved that exact commit;
  - no earlier operation was interrupted.

Git commands run as the checkout's owner, so file ownership never changes.
Only ai-backend and ai-frontend are built and recreated, always with
`--no-deps`, so PostgreSQL, Redis and other containers are never touched.
Python standard library only; no secrets in arguments or logs.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import errno
import json
import logging
import os
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

log = logging.getLogger("autodeploy")

# Exit codes. "Waiting" outcomes (CI pending, approval needed, nothing new) are
# successful runs: the timers simply check again later.
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_UNSAFE = 2
EXIT_LOCKED = 75

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_]{20,255}$")
TAG_SAFE_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")

# The only containers this tool may build or recreate.
MANAGED_CONTAINERS = ("ai-backend", "ai-frontend")
# Containers that must never appear in any command this tool runs.
PROTECTED_CONTAINERS = ("hesham-postgres", "hesham-redis", "hesham-portfolio")

ROLLBACK_REPO = "ai-document-assistant-rollback"

# Changes under these paths are never deployed automatically. The backend image
# runs `python -m alembic upgrade head` on every start, so a migration change
# would be applied to production the moment the new container starts.
MIGRATION_PATHS = ("backend/migrations/", "backend/alembic.ini")
# This tooling is installed outside the repository; changes to it need review.
TOOLING_PATHS = ("ops/deploy/",)

MAX_API_BYTES = 2 * 1024 * 1024
MAX_CANDIDATE_BYTES = 4096
GITHUB_API = "https://api.github.com"
MIN_COMPOSE_VERSION = (2, 20)


class DeployError(Exception):
    """A gate failed or a step errored. Nothing further may change."""


class UnsafeStateError(DeployError):
    """The server is in a state that needs an operator (exit 2)."""


class LockedError(Exception):
    pass


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

@dataclass
class Config:
    repo_slug: str = "HishamFekri/ai-document-assistant"
    branch: str = "main"
    workflow_file: str = "ci.yml"
    repo_dir: str = "/srv/storage/projects/ai-document-assistant"
    compose_file: str = "/srv/docker/ai-document-assistant/compose.yaml"
    storage_check: str = "/srv/docker/apps/check-storage.sh"
    state_dir: str = "/var/lib/ai-document-assistant-deploy"
    check_state_dir: str = "/var/lib/ai-document-assistant-check"
    checker_user: str = "ai-deploy-check"
    containers: tuple[str, ...] = MANAGED_CONTAINERS
    github_token_file: str = ""
    git_timeout: int = 120
    api_timeout: int = 20
    storage_check_timeout: int = 60
    build_timeout: int = 1800
    up_timeout: int = 300
    health_timeout: int = 300
    health_poll_interval: float = 5.0
    rollback_keep: int = 3

    @property
    def origin_urls(self) -> tuple[str, ...]:
        base = f"https://github.com/{self.repo_slug}"
        return (base, base + ".git")

    @property
    def workflow_path(self) -> str:
        return f".github/workflows/{self.workflow_file}"

    @property
    def candidate_path(self) -> str:
        return posixpath.join(self.check_state_dir, "candidate.json")


_INT_KEYS = {
    "GIT_TIMEOUT": "git_timeout",
    "API_TIMEOUT": "api_timeout",
    "STORAGE_CHECK_TIMEOUT": "storage_check_timeout",
    "BUILD_TIMEOUT": "build_timeout",
    "UP_TIMEOUT": "up_timeout",
    "HEALTH_TIMEOUT": "health_timeout",
    "ROLLBACK_KEEP": "rollback_keep",
}
_STR_KEYS = {
    "REPO_SLUG": "repo_slug",
    "BRANCH": "branch",
    "WORKFLOW_FILE": "workflow_file",
    "REPO_DIR": "repo_dir",
    "COMPOSE_FILE": "compose_file",
    "STORAGE_CHECK": "storage_check",
    "STATE_DIR": "state_dir",
    "CHECK_STATE_DIR": "check_state_dir",
    "CHECKER_USER": "checker_user",
    "GITHUB_TOKEN_FILE": "github_token_file",
}


def parse_config(text: str) -> Config:
    """Parse KEY=VALUE lines. Unknown keys are errors; nothing is evaluated."""
    cfg = Config()
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise DeployError(f"config line {lineno}: expected KEY=VALUE")
        key, value = (part.strip() for part in line.split("=", 1))
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key in _STR_KEYS:
            setattr(cfg, _STR_KEYS[key], value)
        elif key in _INT_KEYS:
            if not value.isdigit() or int(value) <= 0:
                raise DeployError(f"config line {lineno}: {key} must be a positive integer")
            setattr(cfg, _INT_KEYS[key], int(value))
        elif key == "CONTAINERS":
            cfg.containers = tuple(v.strip() for v in value.split(",") if v.strip())
        else:
            raise DeployError(f"config line {lineno}: unknown key {key}")
    validate_config(cfg)
    return cfg


def validate_config(cfg: Config) -> None:
    if not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+", cfg.repo_slug):
        raise DeployError("REPO_SLUG is not owner/name")
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", cfg.branch) or ".." in cfg.branch:
        raise DeployError("BRANCH is invalid")
    if not re.fullmatch(r"[A-Za-z0-9._-]+\.ya?ml", cfg.workflow_file):
        raise DeployError("WORKFLOW_FILE is invalid")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", cfg.checker_user) or cfg.checker_user == "root":
        raise DeployError("CHECKER_USER is invalid")
    for name in ("repo_dir", "compose_file", "storage_check", "state_dir", "check_state_dir"):
        value = getattr(cfg, name)
        if not value.startswith("/") or posixpath.normpath(value) != value:
            raise DeployError(f"{name.upper()} must be a normalized absolute path")
    if cfg.state_dir == cfg.check_state_dir:
        raise DeployError("STATE_DIR and CHECK_STATE_DIR must differ")
    if cfg.github_token_file and not cfg.github_token_file.startswith("/"):
        raise DeployError("GITHUB_TOKEN_FILE must be an absolute path")
    if not cfg.containers or len(set(cfg.containers)) != len(cfg.containers):
        raise DeployError("CONTAINERS must list distinct container names")
    for name in cfg.containers:
        if name not in MANAGED_CONTAINERS:
            raise DeployError(f"container {name!r} is not one this tool may manage")


def load_config(path: str | None) -> Config:
    if not path:
        cfg = Config()
        validate_config(cfg)
        return cfg
    with open(path, encoding="utf-8") as fh:
        return parse_config(fh.read())


# --------------------------------------------------------------------------
# Command execution with a safety guard
# --------------------------------------------------------------------------

@dataclass
class Result:
    returncode: int
    stdout: str = ""
    stderr: str = ""


_GIT_ALLOWED = {"fetch", "ls-remote", "rev-parse", "status", "symbolic-ref",
                "merge-base", "diff", "merge", "reset", "remote"}
_COMPOSE_ALLOWED = {"build", "up", "config", "version"}
_COMPOSE_REFUSED_FLAGS = {"--remove-orphans", "-v", "--volumes", "-V", "--renew-anon-volumes",
                          "--always-recreate-deps", "--abort-on-container-exit", "--rmi"}


def assert_command_allowed(argv: Sequence[str]) -> None:
    """Refuse commands this tool must never run, whatever the caller asks.

    Defense in depth: the deploy logic never builds these, and the tests prove
    that, but a guard here keeps a future edit from silently adding one.
    """
    if not argv:
        raise DeployError("empty command")
    args = list(argv)
    joined = " ".join(args)
    program = posixpath.basename(args[0])
    for name in PROTECTED_CONTAINERS:
        if name in joined:
            raise DeployError(f"refusing command that references {name}")
    if program in ("systemctl", "service", "rm", "sudo", "su", "dockerd", "sh", "bash", "chown", "chmod"):
        raise DeployError(f"refusing to run {program}")
    if program == "git":
        if args[1:] == ["--version"]:
            return  # used only by the identity-switch probe
        if any(a in ("--force", "-f") for a in args):
            raise DeployError("refusing forced git command")
        sub = _git_subcommand(args)
        if sub not in _GIT_ALLOWED:
            raise DeployError(f"refusing git {sub}")
        if sub == "reset" and "--keep" not in args:
            raise DeployError("only `git reset --keep` is allowed")
        if sub == "merge" and "--ff-only" not in args:
            raise DeployError("only fast-forward merges are allowed")
        if sub == "remote" and args[-2:-1] != ["get-url"]:
            raise DeployError("git remote may only be read")
    if program == "docker":
        rest = args[1:]
        if rest[:1] == ["compose"]:
            sub = _compose_subcommand(rest[1:])
            if sub not in _COMPOSE_ALLOWED:
                raise DeployError(f"refusing docker compose {sub}")
            if sub == "up" and not {"--no-deps", "--no-build"} <= set(rest):
                raise DeployError("docker compose up requires --no-deps --no-build")
            if _COMPOSE_REFUSED_FLAGS & set(rest):
                raise DeployError("refusing destructive compose flag")
        else:
            allowed = {("inspect",), ("image", "inspect"), ("image", "tag"),
                       ("image", "rm"), ("image", "ls")}
            head = tuple(rest[:2]) if rest[:1] == ["image"] else tuple(rest[:1])
            if head not in allowed:
                raise DeployError(f"refusing docker {' '.join(head)}")
            if head == ("image", "rm"):
                if any(a.startswith("-") for a in rest[2:]):
                    raise DeployError("refusing docker image rm flags")
                for ref in rest[2:]:
                    if not ref.startswith(ROLLBACK_REPO + ":"):
                        raise DeployError("only rollback tags created by this tool may be removed")


def _git_subcommand(args: list[str]) -> str:
    i = 1
    while i < len(args):
        if args[i] in ("-c", "-C"):
            i += 2
            continue
        if args[i].startswith("-"):
            i += 1
            continue
        return args[i]
    return ""


def _compose_subcommand(args: list[str]) -> str:
    i = 0
    while i < len(args):
        if args[i] in ("--project-name", "--project-directory", "--file", "-p", "-f"):
            i += 2
            continue
        if args[i].startswith("-"):
            i += 1
            continue
        return args[i]
    return ""


def minimal_env(program: str = "") -> dict[str, str]:
    """Environment for child processes: no inherited secrets."""
    env = {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "GIT_TERMINAL_PROMPT": "0",
        "DOCKER_CLI_HINTS": "false",
        "COMPOSE_ANSI": "never",
    }
    if program == "git":
        # Ignore every user and system git config: only -c options apply.
        env.update({"HOME": "/nonexistent", "XDG_CONFIG_HOME": "/nonexistent",
                    "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"})
        return env
    for key in ("HOME", "DOCKER_HOST", "DOCKER_CONFIG", "XDG_RUNTIME_DIR"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


# --------------------------------------------------------------------------
# Spawn diagnostics (no secrets: only stage, errno, ids and process security state)
# --------------------------------------------------------------------------

# The capabilities the executor unit grants, by bit number (linux/capability.h).
_CAP_BITS = {2: "CAP_DAC_READ_SEARCH", 5: "CAP_KILL", 6: "CAP_SETGID", 7: "CAP_SETUID"}
_STATUS_KEYS = ("CapEff", "CapBnd", "NoNewPrivs", "Seccomp", "Seccomp_filters")
_STAGE_HINTS = {
    "pre-exec": "fork, or setgroups/setregid/setreuid in the child, failed before exec",
    "chdir": "chdir to the working directory failed",
    "exec": "execve failed",
}


def classify_spawn_error(exc: OSError, cwd: str) -> str:
    """Where a child failed to start, from CPython's _posixsubprocess reporting.

    The child reports setgroups/setregid/setreuid failures without a filename,
    a failed chdir with the cwd, and a failed execve with the executable (the
    first non-ENOENT error across PATH). A failed fork raises in the parent,
    also without a filename.
    """
    if exc.filename is None:
        return "pre-exec"
    if exc.filename == cwd:
        return "chdir"
    return "exec"


def parse_security_context(status: str) -> dict[str, str]:
    fields = {}
    for line in status.splitlines():
        key, _, value = line.partition(":")
        if key in _STATUS_KEYS:
            fields[key] = value.strip()
    return fields


def describe_caps(hex_value: str) -> str:
    try:
        bits = int(hex_value, 16)
    except ValueError:
        return "?"
    return "+".join(name for bit, name in sorted(_CAP_BITS.items()) if bits >> bit & 1) or "none"


def security_context(path: str = "/proc/self/status") -> str:
    """This process's capabilities, no_new_privs and seccomp mode."""
    try:
        with open(path, encoding="ascii", errors="replace") as fh:
            fields = parse_security_context(fh.read())
    except OSError:
        return "security context unavailable"
    return " ".join(f"{k}={fields[k]}" + (f"({describe_caps(fields[k])})" if k.startswith("Cap") else "")
                    for k in _STATUS_KEYS if k in fields)


def spawn_failure_detail(argv: Sequence[str], exc: OSError, cwd: str,
                         switch: tuple[int, int] | None) -> str:
    stage = classify_spawn_error(exc, cwd)
    code = errno.errorcode.get(exc.errno or 0, str(exc.errno))
    resolved = shutil.which(argv[0], path=minimal_env()["PATH"]) or "not found on PATH"
    who = f"uid/gid switch to {switch[0]}/{switch[1]}" if switch else "no uid/gid switch"
    return (f"{exc.strerror} [stage={stage}: {_STAGE_HINTS[stage]}; errno={code}; {who}; "
            f"executable={resolved}; {security_context()}]")


def identity_switch_steps(uid: int, gid: int) -> tuple[tuple[str, dict[str, Any]], ...]:
    """Each step adds one credential change, in the order the child applies them."""
    return (
        ("exec, no switch", {}),
        ("+setgroups([])", {"extra_groups": []}),
        ("+setregid", {"extra_groups": [], "group": gid}),
        ("+setreuid", {"extra_groups": [], "group": gid, "user": uid}),
    )


def probe_identity_switch(uid: int, gid: int,
                          spawn: Callable[..., Any] = subprocess.run) -> list[tuple[str, str]]:
    """Run `git --version` with the same spawn settings as Runner, one change at a time.

    Stops at the first failing step, which names the syscall that is refused.
    Read-only: `git --version` touches no repository.
    """
    argv = ["git", "--version"]
    assert_command_allowed(argv)
    results: list[tuple[str, str]] = []
    for label, kwargs in identity_switch_steps(uid, gid):
        try:
            proc = spawn(argv, env=minimal_env("git"), cwd="/", stdin=subprocess.DEVNULL,
                         capture_output=True, text=True, errors="replace", timeout=30,
                         check=False, **kwargs)
        except subprocess.TimeoutExpired:
            results.append((label, "timed out"))
            break
        except OSError as exc:
            code = errno.errorcode.get(exc.errno or 0, str(exc.errno))
            results.append((label, f"FAILED {code} at {classify_spawn_error(exc, '/')}"))
            break
        if proc.returncode != 0:
            results.append((label, f"exit {proc.returncode}"))
            break
        results.append((label, "ok"))
    return results


class Runner:
    """Runs guarded commands with timeouts. Output is captured, never echoed."""

    def run(self, argv: Sequence[str], timeout: int, as_user: tuple[int, int] | None = None) -> Result:
        assert_command_allowed(argv)
        kwargs: dict[str, Any] = {}
        if as_user is not None:
            euid = os.geteuid()
            if euid == 0 and as_user[0] != 0:
                kwargs.update(user=as_user[0], group=as_user[1], extra_groups=[])
            elif euid != as_user[0]:
                raise DeployError("git must run as root or as the checkout's owner")
        try:
            proc = subprocess.run(
                list(argv), env=minimal_env(posixpath.basename(argv[0])), cwd="/",
                stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace",
                timeout=timeout, check=False, **kwargs,
            )
        except subprocess.TimeoutExpired as exc:
            raise DeployError(f"timed out after {timeout}s: {describe(argv)}") from exc
        except OSError as exc:
            switch = (kwargs["user"], kwargs["group"]) if kwargs else None
            raise DeployError(f"could not run {describe(argv)}: "
                              f"{spawn_failure_detail(argv, exc, '/', switch)}") from exc
        return Result(proc.returncode, proc.stdout, proc.stderr)


def describe(argv: Sequence[str]) -> str:
    return " ".join(argv[:4]) + (" ..." if len(argv) > 4 else "")


def tail(text: str, lines: int = 30) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


# --------------------------------------------------------------------------
# GitHub API (read-only, validated)
# --------------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


def default_opener() -> Callable[..., Any]:
    return urllib.request.build_opener(_NoRedirect()).open


def read_token(path: str) -> str | None:
    """Read an optional read-only token. Its value is never logged."""
    if not path:
        cred_dir = os.environ.get("CREDENTIALS_DIRECTORY")
        candidate = os.path.join(cred_dir, "github-token") if cred_dir else ""
        if not candidate or not os.path.isfile(candidate):
            return None
        path = candidate
    try:
        with open(path, encoding="ascii") as fh:
            token = fh.read().strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise DeployError("GitHub token file could not be read") from exc
    if not TOKEN_RE.fullmatch(token):
        raise DeployError("GitHub token file has an unexpected format")
    return token


class GitHub:
    def __init__(self, cfg: Config, token: str | None, opener: Callable[..., Any] | None = None):
        self.cfg = cfg
        self._token = token
        self._open = opener or default_opener()

    def get(self, path: str, params: dict[str, str] | None = None) -> dict:
        url = GITHUB_API + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, method="GET", headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ai-document-assistant-autodeploy",
        })
        if self._token:
            # Unredirected: never forwarded even if a redirect were followed.
            req.add_unredirected_header("Authorization", f"Bearer {self._token}")
        try:
            with self._open(req, timeout=self.cfg.api_timeout) as resp:
                status = getattr(resp, "status", None)
                ctype = resp.headers.get("Content-Type", "")
                body = resp.read(MAX_API_BYTES + 1)
        except urllib.error.HTTPError as exc:
            remaining = exc.headers.get("X-RateLimit-Remaining") if exc.headers else None
            exc.close()
            if exc.code in (403, 429) and remaining == "0":
                raise DeployError("GitHub API rate limit exhausted") from None
            raise DeployError(f"GitHub API returned HTTP {exc.code} for {path}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise DeployError(f"GitHub API request failed for {path}: {type(exc).__name__}") from None
        if status != 200:
            raise DeployError(f"GitHub API returned HTTP {status} for {path}")
        if not ctype.split(";")[0].strip().lower() == "application/json":
            raise DeployError("GitHub API returned a non-JSON response")
        if len(body) > MAX_API_BYTES:
            raise DeployError("GitHub API response is too large")
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise DeployError("GitHub API returned invalid JSON") from None
        if not isinstance(data, dict):
            raise DeployError("GitHub API returned an unexpected JSON shape")
        return data

    def branch_head(self) -> str:
        branch = urllib.parse.quote(self.cfg.branch, safe="")
        data = self.get(f"/repos/{self.cfg.repo_slug}/branches/{branch}")
        return parse_branch_head(data, self.cfg.branch)

    def ci_runs(self, sha: str) -> dict:
        return self.get(
            f"/repos/{self.cfg.repo_slug}/actions/workflows/{self.cfg.workflow_file}/runs",
            {"branch": self.cfg.branch, "event": "push", "head_sha": sha, "per_page": "100"},
        )


def parse_branch_head(data: dict, branch: str) -> str:
    commit = data.get("commit")
    if data.get("name") != branch or not isinstance(commit, dict):
        raise DeployError("GitHub branch response does not describe the expected branch")
    sha = commit.get("sha")
    if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
        raise DeployError("GitHub branch response has an invalid commit SHA")
    return sha


@dataclass
class CIVerdict:
    state: str  # success | pending | failure | missing
    detail: str


def evaluate_ci_runs(data: dict, sha: str, cfg: Config) -> CIVerdict:
    """Decide whether CI passed for exactly `sha`. Any surprise raises.

    Every returned run must be a push to the configured branch, for `sha`, of
    the configured workflow file, in this repository (not a fork). The newest
    run decides; if any run for the commit is still going, the result is
    pending, even if an earlier one succeeded.
    """
    if not SHA_RE.fullmatch(sha):
        raise DeployError("invalid target SHA")
    runs = data.get("workflow_runs")
    total = data.get("total_count")
    if not isinstance(runs, list) or not isinstance(total, int) or isinstance(total, bool):
        raise DeployError("GitHub workflow runs response is malformed")
    if total != len(runs):
        raise DeployError("GitHub workflow runs response is incomplete (pagination)")
    if not runs:
        return CIVerdict("missing", f"no {cfg.workflow_file} push run for {sha[:12]}")
    for run in runs:
        _validate_run(run, sha, cfg)
    latest = max(runs, key=lambda r: (r["id"], r["run_attempt"]))
    if any(r["status"] != "completed" for r in runs):
        return CIVerdict("pending", f"run {latest['id']} is {latest['status']}")
    if latest["conclusion"] == "success":
        return CIVerdict("success", f"run {latest['id']} attempt {latest['run_attempt']} succeeded")
    return CIVerdict("failure", f"run {latest['id']} concluded {latest['conclusion']}")


_RUN_STATUSES = {"completed", "queued", "in_progress", "waiting", "requested", "pending", "action_required"}


def _validate_run(run: Any, sha: str, cfg: Config) -> None:
    if not isinstance(run, dict):
        raise DeployError("workflow run is not an object")
    run_id = run.get("id")
    attempt = run.get("run_attempt")
    for value in (run_id, attempt):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise DeployError("workflow run has an invalid id or attempt")
    expect = {
        "head_sha": sha,
        "head_branch": cfg.branch,
        "event": "push",
    }
    for key, want in expect.items():
        if run.get(key) != want:
            raise DeployError(f"workflow run {run_id} has unexpected {key}")
    path = run.get("path")
    if not isinstance(path, str) or path.split("@", 1)[0] != cfg.workflow_path:
        raise DeployError(f"workflow run {run_id} is not {cfg.workflow_path}")
    for key in ("repository", "head_repository"):
        repo = run.get(key)
        if not isinstance(repo, dict) or repo.get("full_name") != cfg.repo_slug:
            raise DeployError(f"workflow run {run_id} {key} is not {cfg.repo_slug}")
    if run.get("status") not in _RUN_STATUSES:
        raise DeployError(f"workflow run {run_id} has unknown status")
    conclusion = run.get("conclusion")
    if run["status"] == "completed" and not isinstance(conclusion, str):
        raise DeployError(f"workflow run {run_id} completed without a conclusion")


# --------------------------------------------------------------------------
# Pure safety helpers
# --------------------------------------------------------------------------

def validate_sha(value: Any, what: str = "commit") -> str:
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise DeployError(f"{what} is not a full 40-character SHA")
    return value


def _matches(path: str, prefixes: Iterable[str]) -> bool:
    for prefix in prefixes:
        if prefix.endswith("/"):
            if path.startswith(prefix):
                return True
        elif path == prefix:
            return True
    return False


def classify_changes(paths: Iterable[str]) -> dict[str, list[str]]:
    """Return {"migrations": [...], "tooling": [...]} for paths needing review."""
    found: dict[str, list[str]] = {"migrations": [], "tooling": []}
    for path in paths:
        if _matches(path, MIGRATION_PATHS):
            found["migrations"].append(path)
        elif _matches(path, TOOLING_PATHS):
            found["tooling"].append(path)
    return found


def parse_compose_version(text: str) -> tuple[int, int]:
    match = re.search(r"(\d+)\.(\d+)", text)
    if not match:
        raise DeployError("could not parse docker compose version")
    return int(match.group(1)), int(match.group(2))


def rollback_tag(container: str, sha: str) -> str:
    tag = f"{container}-{sha[:12]}"
    if not TAG_SAFE_RE.fullmatch(tag):
        raise DeployError("invalid rollback tag")
    return f"{ROLLBACK_REPO}:{tag}"


def validate_images(value: Any, containers: Iterable[str], what: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise UnsafeStateError(f"{what} are not recorded")
    for name in containers:
        if not isinstance(value.get(name), str) or not IMAGE_ID_RE.fullmatch(value[name]):
            raise UnsafeStateError(f"{what} have no valid image id for {name}")
    return value


def utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def trusted_file_problem(st: os.stat_result, *, executable: bool = False) -> str | None:
    """Why a root-run file is unsafe to trust, or None."""
    if st.st_uid != 0:
        return "is not owned by root"
    if st.st_mode & 0o022:
        return "is writable by group or others"
    if executable and not st.st_mode & 0o111:
        return "is not executable"
    return None


def recovery_advice(ip: dict) -> str:
    """Operator instructions for an interrupted or failed operation."""
    phase = str(ip.get("phase", ""))
    step = phase.split(":", 1)[0]
    action = ip.get("action")
    frm, to = str(ip.get("from_commit", ""))[:12], str(ip.get("to_commit", ""))[:12]
    lines = [f"interrupted {action} {frm} -> {to} at phase '{phase}' (started {ip.get('started_at')})."]
    if step in ("starting", "checkout", "build", "restore-checkout"):
        lines.append("No container was changed; only the checkout may have moved and images may be "
                     "partly built. Run `autodeploy.py recover --abort`.")
    elif step in ("built", "replace", "recover-complete", "recover-abort", "recover-checkout"):
        lines.append("Containers may be split between the old and new images. Run "
                     "`autodeploy.py recover --status` to see which image each one runs, then "
                     "`recover --complete` (finish) or `recover --abort` (return to the old release).")
    elif step in ("rollback", "rollback-checkout", "rollback_failed"):
        lines.append("Restoring the previous images failed or was cut short. Read the container logs "
                     "(`docker logs --tail 200 ai-backend`), fix the cause, then run `recover --abort` "
                     "(or `recover --complete` for an interrupted manual rollback).")
    elif step == "failed_migrations":
        lines.append("A release with migration changes did not become healthy, and the database may "
                     "already be at the new revision. Do not restart the old backend. Follow "
                     "'Rollback across a migration' in ops/deploy/README.md, then "
                     "`recover --abort --database-downgraded`, or fix forward and `recover --complete`.")
    else:
        lines.append("Run `autodeploy.py recover --status`.")
    if ip.get("migrations"):
        lines.append("This operation includes migration changes: going back to the older release "
                     "requires --database-downgraded after a manual downgrade.")
    lines.append("If the containers were already put right by hand: `recover --adopt --commit <HEAD sha>`.")
    return " ".join(lines)


# --------------------------------------------------------------------------
# State, history and lock
# --------------------------------------------------------------------------

def _lock_file(path: str) -> int:
    import fcntl  # Linux only; imported here so tests run anywhere.

    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o640)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise LockedError("another run holds the lock") from None
    return fd


def _atomic_write_json(path: str, data: dict, mode: int = 0o640) -> None:
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


class StateStore:
    def __init__(self, state_dir: str):
        self.dir = state_dir
        self.state_path = os.path.join(state_dir, "state.json")
        self.history_path = os.path.join(state_dir, "history.jsonl")
        self.lock_path = os.path.join(state_dir, "deploy.lock")
        self._lock_fd: int | None = None

    def lock(self) -> None:
        os.makedirs(self.dir, mode=0o750, exist_ok=True)
        self._lock_fd = _lock_file(self.lock_path)

    def unlock(self) -> None:
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None

    def exists(self) -> bool:
        return os.path.isfile(self.state_path)

    def load(self) -> dict:
        try:
            with open(self.state_path, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            raise UnsafeStateError("no deployment state; run `autodeploy.py init` first") from None
        except (OSError, json.JSONDecodeError) as exc:
            raise UnsafeStateError(f"deployment state is unreadable: {type(exc).__name__}") from None
        if not isinstance(data, dict) or data.get("version") != 1:
            raise UnsafeStateError("deployment state has an unknown format")
        if not SHA_RE.fullmatch(str(data.get("deployed_commit"))):
            raise UnsafeStateError("deployment state has an invalid deployed commit")
        for key, default in (("previous", None), ("failed", {}), ("approved", None),
                             ("rollback_tags", []), ("last_event", None), ("images", {}),
                             ("in_progress", None)):
            data.setdefault(key, default)
        return data

    def save(self, data: dict) -> None:
        data["updated_at"] = utcnow()
        _atomic_write_json(self.state_path, data)

    def record(self, entry: dict) -> None:
        entry = {"time": utcnow(), **entry}
        with open(self.history_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def history(self, limit: int = 10) -> list[dict]:
        try:
            with open(self.history_path, encoding="utf-8") as fh:
                lines = fh.readlines()[-limit:]
        except FileNotFoundError:
            return []
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                out.append({"unparseable": True})
        return out


def check_installation(paths: Iterable[str], stat_fn: Callable[[str], os.stat_result]) -> None:
    """Installed code and config must be root-owned and not writable by others."""
    for path in paths:
        try:
            st = stat_fn(path)
        except OSError:
            raise UnsafeStateError(f"installed file {path} is missing") from None
        problem = trusted_file_problem(st)
        if problem:
            raise UnsafeStateError(f"installed file {path} {problem}; reinstall it root-owned (0644/0755)")


# --------------------------------------------------------------------------
# Checker (unprivileged)
# --------------------------------------------------------------------------

class Checker:
    """Polls GitHub and writes candidate.json. No Docker, no production checkout."""

    def __init__(self, cfg: Config, runner: Runner | None = None, github: GitHub | None = None,
                 code_paths: Sequence[str] = (), stat_fn: Callable[[str], os.stat_result] = os.stat):
        self.cfg = cfg
        self.runner = runner or Runner()
        self._github = github
        self.code_paths = list(code_paths)
        self.stat = stat_fn
        self.state_path = posixpath.join(cfg.check_state_dir, "check-state.json")
        self.lock_path = posixpath.join(cfg.check_state_dir, "check.lock")

    @property
    def github(self) -> GitHub:
        if self._github is None:
            self._github = GitHub(self.cfg, read_token(self.cfg.github_token_file))
        return self._github

    def remote_head(self) -> str:
        url = self.cfg.origin_urls[1]
        res = self.runner.run(["git", "-c", "credential.helper=", "-c", "protocol.allow=never",
                               "-c", "protocol.https.allow=always", "ls-remote", "--exit-code",
                               url, f"refs/heads/{self.cfg.branch}"], self.cfg.git_timeout)
        if res.returncode != 0:
            raise DeployError(f"git ls-remote failed: {tail(res.stderr, 5)}")
        return parse_ls_remote(res.stdout, self.cfg.branch)

    def _load(self) -> dict:
        try:
            with open(self.state_path, encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def check(self) -> int:
        check_installation(self.code_paths, self.stat)
        head = self.remote_head()
        state = self._load()
        if state.get("last") == [head, "success"] and self._candidate_sha() == head:
            log.info("candidate %s already published", head[:12])
            return EXIT_OK
        api_head = self.github.branch_head()
        if api_head != head:
            log.info("GitHub API reports %s, git reports %s; retrying later", api_head[:12], head[:12])
            return EXIT_OK
        verdict = evaluate_ci_runs(self.github.ci_runs(head), head, self.cfg)
        if state.get("last") != [head, verdict.state]:
            log.info("%s: CI %s (%s)", head[:12], verdict.state, verdict.detail)
        if verdict.state == "success" and self._candidate_sha() != head:
            _atomic_write_json(self.cfg.candidate_path, {
                "version": 1, "sha": head, "ci": verdict.detail, "checked_at": utcnow()}, mode=0o644)
            log.info("published candidate %s", head[:12])
        _atomic_write_json(self.state_path, {"last": [head, verdict.state], "checked_at": utcnow()})
        return EXIT_OK

    def _candidate_sha(self) -> str | None:
        try:
            with open(self.cfg.candidate_path, encoding="utf-8") as fh:
                data = json.load(fh)
            return data.get("sha") if isinstance(data, dict) else None
        except (FileNotFoundError, json.JSONDecodeError):
            return None


def parse_ls_remote(stdout: str, branch: str) -> str:
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    if len(lines) != 1:
        raise DeployError("unexpected ls-remote output")
    sha, _, ref = lines[0].partition("\t")
    if ref != f"refs/heads/{branch}":
        raise DeployError("unexpected ls-remote ref")
    return validate_sha(sha, "remote head")


# --------------------------------------------------------------------------
# Executor (root)
# --------------------------------------------------------------------------

def _docker_group_members() -> list[str]:
    import grp  # Linux only.

    try:
        return list(grp.getgrnam("docker").gr_mem)
    except KeyError:
        return []


@dataclass
class ComposeTarget:
    container: str
    service: str
    image_ref: str
    image_id: str
    status: str = ""
    health: str | None = None


@dataclass
class ComposeProject:
    name: str
    working_dir: str
    config_file: str
    targets: list[ComposeTarget] = field(default_factory=list)

    def base(self) -> list[str]:
        return ["docker", "compose", "--project-name", self.name,
                "--project-directory", self.working_dir, "--file", self.config_file]

    def live_images(self) -> dict[str, str]:
        return {t.container: t.image_id for t in self.targets}


class Deployer:
    def __init__(self, cfg: Config, store: StateStore, runner: Runner | None = None,
                 github: GitHub | None = None,
                 stat_fn: Callable[[str], os.stat_result] = os.stat,
                 sleep: Callable[[float], None] = time.sleep,
                 monotonic: Callable[[], float] = time.monotonic,
                 code_paths: Sequence[str] = (),
                 checker_uid: int | None = None,
                 docker_group_members: Callable[[], list[str]] | None = None,
                 identity_probe: Callable[[int, int], list[tuple[str, str]]] = probe_identity_switch,
                 euid: Callable[[], int] = lambda: os.geteuid()):
        self.cfg = cfg
        self.store = store
        self.runner = runner or Runner()
        self.docker_group_members = docker_group_members or _docker_group_members
        self.identity_probe = identity_probe
        self.euid = euid
        self._github = github
        self.stat = stat_fn
        self.sleep = sleep
        self.monotonic = monotonic
        self.code_paths = list(code_paths)
        self._checker_uid = checker_uid
        self._repo_identity: tuple[int, int] | None = None

    @property
    def github(self) -> GitHub:
        if self._github is None:
            self._github = GitHub(self.cfg, read_token(self.cfg.github_token_file))
        return self._github

    @property
    def checker_uid(self) -> int:
        if self._checker_uid is None:
            import pwd

            try:
                self._checker_uid = pwd.getpwnam(self.cfg.checker_user).pw_uid
            except KeyError:
                raise UnsafeStateError(f"user {self.cfg.checker_user} does not exist") from None
        return self._checker_uid

    # ---- git (as the checkout's owner) ------------------------------------

    def repo_identity(self) -> tuple[int, int]:
        """The checkout's owner. Git runs as this user, so ownership never changes."""
        if self._repo_identity is None:
            try:
                top = self.stat(self.cfg.repo_dir)
                gitdir = self.stat(posixpath.join(self.cfg.repo_dir, ".git"))
            except OSError:
                raise UnsafeStateError("the production checkout or its .git directory is missing") from None
            if not stat.S_ISDIR(top.st_mode) or not stat.S_ISDIR(gitdir.st_mode):
                raise UnsafeStateError("the production checkout is not a regular git working tree")
            if (top.st_uid, top.st_gid) != (gitdir.st_uid, gitdir.st_gid):
                raise UnsafeStateError("the checkout and its .git directory have different owners")
            self._repo_identity = (top.st_uid, top.st_gid)
        return self._repo_identity

    def git(self, *args: str, check: bool = True, timeout: int | None = None) -> Result:
        argv = ["git", "-C", self.cfg.repo_dir, "-c", "core.hooksPath=/dev/null",
                "-c", "credential.helper=", "-c", "protocol.allow=never",
                "-c", "protocol.https.allow=always", *args]
        res = self.runner.run(argv, timeout or self.cfg.git_timeout, as_user=self.repo_identity())
        if check and res.returncode != 0:
            raise DeployError(f"git {args[0]} failed: {tail(res.stderr, 5)}")
        return res

    def head(self) -> str:
        return validate_sha(self.git("rev-parse", "--verify", "HEAD^{commit}").stdout.strip(), "HEAD")

    def check_checkout(self, expected_head: str | None) -> str:
        url = self.git("remote", "get-url", "origin").stdout.strip()
        if url not in self.cfg.origin_urls:
            raise UnsafeStateError("origin does not point at the expected GitHub repository")
        branch = self.git("symbolic-ref", "--quiet", "--short", "HEAD", check=False)
        if branch.returncode != 0 or branch.stdout.strip() != self.cfg.branch:
            raise UnsafeStateError(f"checkout is not on branch {self.cfg.branch}")
        self.check_clean()
        head = self.head()
        if expected_head is not None and head != expected_head:
            raise UnsafeStateError(
                f"HEAD {head[:12]} is not the recorded deployed commit {expected_head[:12]}")
        return head

    def check_clean(self) -> None:
        res = self.git("status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules=none")
        if res.stdout.strip():
            count = len(res.stdout.strip().splitlines())
            raise UnsafeStateError(f"working tree is not clean ({count} changed or untracked paths)")

    def fetch_target(self) -> str:
        b = self.cfg.branch
        self.git("fetch", "--quiet", "--no-tags", "--no-recurse-submodules", "--no-write-fetch-head",
                 "origin", f"+refs/heads/{b}:refs/remotes/origin/{b}")
        return validate_sha(
            self.git("rev-parse", "--verify", f"refs/remotes/origin/{b}^{{commit}}").stdout.strip(),
            f"origin/{b}")

    def remote_head(self) -> str:
        res = self.git("ls-remote", "--exit-code", "origin", f"refs/heads/{self.cfg.branch}")
        return parse_ls_remote(res.stdout, self.cfg.branch)

    def is_ancestor(self, older: str, newer: str) -> bool:
        res = self.git("merge-base", "--is-ancestor", older, newer, check=False)
        if res.returncode not in (0, 1):
            raise DeployError("git merge-base failed")
        return res.returncode == 0

    def changed_paths(self, old: str, new: str) -> list[str]:
        res = self.git("diff", "--name-only", "--no-renames", "-z", old, new, "--")
        return [p for p in res.stdout.split("\0") if p]

    def move_checkout(self, state: dict | None, target: str, allowed_from: set[str],
                      phase: str | None = None) -> None:
        """Move HEAD to `target`: fast-forward, or `reset --keep` backwards.

        --keep refuses to touch local changes; unlike --hard it cannot discard
        anything (the tree is verified clean first).
        """
        head = self.head()
        if head == target:
            return
        if head not in allowed_from:
            raise UnsafeStateError(f"HEAD {head[:12]} is not one of the commits this operation expects")
        self.check_clean()
        if state is not None and phase:
            self._phase(state, phase)
        if self.is_ancestor(head, target):
            self.git("merge", "--ff-only", "--quiet", target)
        else:
            self.git("reset", "--quiet", "--keep", target)
        if self.head() != target:
            raise DeployError("checkout did not move to the expected commit")

    # ---- storage, installation, candidate --------------------------------

    def check_installation(self) -> None:
        check_installation(self.code_paths, self.stat)

    def check_storage(self) -> None:
        path = self.cfg.storage_check
        try:
            st = self.stat(path)
        except OSError:
            raise UnsafeStateError("storage verification script is missing") from None
        if not stat.S_ISREG(st.st_mode):
            raise UnsafeStateError("storage verification script is not a regular file")
        problem = trusted_file_problem(st, executable=True)
        if problem:
            raise UnsafeStateError(f"storage verification script {problem}")
        try:
            res = self.runner.run([path], self.cfg.storage_check_timeout)
        except DeployError as exc:
            raise UnsafeStateError(f"storage verification failed: {exc}") from None
        if res.returncode != 0:
            raise UnsafeStateError(f"storage verification failed (exit {res.returncode})")
        log.info("storage verification passed")

    def read_candidate(self) -> str | None:
        """The checker's hint. Untrusted input: validated strictly, never followed."""
        path = self.cfg.candidate_path
        try:
            lst = os.lstat(path)
        except FileNotFoundError:
            return None
        except OSError:
            raise UnsafeStateError("candidate file cannot be inspected") from None
        if stat.S_ISLNK(lst.st_mode) or not stat.S_ISREG(lst.st_mode):
            raise UnsafeStateError("candidate file is not a regular file")
        if lst.st_uid != self.checker_uid:
            raise UnsafeStateError(f"candidate file is not owned by {self.cfg.checker_user}")
        if lst.st_size > MAX_CANDIDATE_BYTES:
            raise UnsafeStateError("candidate file is too large")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            fst = os.fstat(fd)
            if (fst.st_ino, fst.st_dev) != (lst.st_ino, lst.st_dev):
                raise UnsafeStateError("candidate file changed while being read")
            raw = os.read(fd, MAX_CANDIDATE_BYTES + 1)
        finally:
            os.close(fd)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise UnsafeStateError("candidate file is not valid JSON") from None
        if not isinstance(data, dict) or data.get("version") != 1:
            raise UnsafeStateError("candidate file has an unknown format")
        try:
            return validate_sha(data.get("sha"), "candidate")
        except DeployError as exc:
            raise UnsafeStateError(str(exc)) from None

    # ---- docker ----------------------------------------------------------

    def docker(self, *args: str, timeout: int = 60, check: bool = True) -> Result:
        res = self.runner.run(["docker", *args], timeout)
        if check and res.returncode != 0:
            raise DeployError(f"docker {args[0]} failed: {tail(res.stderr, 5)}")
        return res

    def inspect_container(self, name: str) -> dict:
        res = self.docker("inspect", "--type", "container", name)
        try:
            data = json.loads(res.stdout)
        except json.JSONDecodeError:
            raise DeployError(f"docker inspect {name} returned invalid JSON") from None
        if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
            raise DeployError(f"docker inspect {name} returned an unexpected shape")
        info = data[0]
        if info.get("Name") != "/" + name:
            raise DeployError(f"docker inspect returned a different container for {name}")
        return info

    def image_present(self, image: str) -> bool:
        res = self.docker("image", "inspect", "--format", "{{.Id}}", image, check=False)
        return res.returncode == 0 and res.stdout.strip() == image

    def image_id(self, ref: str) -> str:
        res = self.docker("image", "inspect", "--format", "{{.Id}}", ref)
        image = res.stdout.strip()
        if not IMAGE_ID_RE.fullmatch(image):
            raise DeployError(f"unexpected image id for {ref}")
        return image

    def resolve_project(self) -> ComposeProject:
        """Derive Compose project/services from the running containers' labels."""
        ver = self.docker("compose", "version", "--short", timeout=30).stdout
        if parse_compose_version(ver) < MIN_COMPOSE_VERSION:
            raise UnsafeStateError("docker compose v2.20 or newer is required")
        project: ComposeProject | None = None
        targets = []
        for name in self.cfg.containers:
            info = self.inspect_container(name)
            config = info.get("Config") or {}
            labels = config.get("Labels") or {}
            proj = labels.get("com.docker.compose.project")
            wdir = labels.get("com.docker.compose.project.working_dir")
            files = labels.get("com.docker.compose.project.config_files", "")
            service = labels.get("com.docker.compose.service")
            if not all(isinstance(v, str) and v for v in (proj, wdir, files, service)):
                raise UnsafeStateError(f"{name} is not managed by docker compose")
            if self.cfg.compose_file not in [posixpath.normpath(f) for f in files.split(",")]:
                raise UnsafeStateError(f"{name} was not created from {self.cfg.compose_file}")
            if project is None:
                project = ComposeProject(proj, wdir, self.cfg.compose_file)
            elif (proj, wdir) != (project.name, project.working_dir):
                raise UnsafeStateError("managed containers belong to different compose projects")
            image_ref = config.get("Image")
            image = info.get("Image")
            if not isinstance(image_ref, str) or not image_ref or image_ref.startswith("sha256:"):
                raise UnsafeStateError(f"{name} has no image name to retag")
            if not isinstance(image, str) or not IMAGE_ID_RE.fullmatch(image):
                raise DeployError(f"{name} has no valid image id")
            state = info.get("State") or {}
            targets.append(ComposeTarget(name, service, image_ref, image, str(state.get("Status")),
                                         (state.get("Health") or {}).get("Status")))
        assert project is not None
        project.targets = targets
        self._check_compose_definition(project)
        return project

    def _check_compose_definition(self, project: ComposeProject) -> None:
        # --no-interpolate: .env values are never substituted into the output.
        res = self.docker(*project.base()[1:], "config", "--format", "json", "--no-interpolate",
                          timeout=60)
        try:
            model = json.loads(res.stdout)
        except json.JSONDecodeError:
            raise DeployError("docker compose config returned invalid JSON") from None
        services = model.get("services") if isinstance(model, dict) else None
        if not isinstance(services, dict):
            raise DeployError("docker compose config has no services")
        repo = posixpath.normpath(self.cfg.repo_dir)
        for t in project.targets:
            svc = services.get(t.service)
            if not isinstance(svc, dict):
                raise UnsafeStateError(f"service {t.service} is missing from {self.cfg.compose_file}")
            if svc.get("container_name") != t.container:
                raise UnsafeStateError(f"service {t.service} does not declare container_name {t.container}")
            build = svc.get("build")
            context = build.get("context") if isinstance(build, dict) else build
            if not isinstance(context, str) or not context or "$" in context:
                raise UnsafeStateError(f"service {t.service} has no literal build context")
            if not context.startswith("/"):
                context = posixpath.join(project.working_dir, context)
            context = posixpath.normpath(context)
            if context != repo and not context.startswith(repo + "/"):
                raise UnsafeStateError(f"service {t.service} does not build from {repo}")
            declared = svc.get("image")
            if isinstance(declared, str) and declared and "$" not in declared and declared != t.image_ref:
                if declared.split(":")[0] != t.image_ref.split(":")[0]:
                    raise UnsafeStateError(f"{t.container} runs {t.image_ref}, but the service declares {declared}")
        # Drop the parsed model (it may contain resolved env_file values).
        del model, services

    def check_recorded_images(self, state: dict, project: ComposeProject) -> None:
        recorded = validate_images(state.get("images"), self.cfg.containers, "recorded images")
        live = project.live_images()
        drift = [c for c in self.cfg.containers if live[c] != recorded[c]]
        if drift:
            raise UnsafeStateError(
                f"{', '.join(drift)} no longer run the recorded image (changed outside this tool). "
                "Check with `autodeploy.py doctor`, then `recover --adopt --commit <HEAD sha>` if correct")

    def _settled(self, info: dict, want: str) -> bool:
        state = info.get("State") or {}
        return (info.get("Image") == want and state.get("Status") == "running"
                and (state.get("Health") or {}).get("Status") == "healthy")

    def wait_healthy(self, target: ComposeTarget, expected_image: str) -> None:
        deadline = self.monotonic() + self.cfg.health_timeout
        while True:
            info = self.inspect_container(target.container)
            state = info.get("State") or {}
            health = (state.get("Health") or {}).get("Status")
            if info.get("Image") != expected_image:
                raise DeployError(f"{target.container} is not running the intended image")
            if state.get("Status") == "running" and health == "healthy":
                log.info("%s is healthy on %s", target.container, expected_image[7:19])
                return
            if health == "unhealthy" or state.get("Status") in ("exited", "dead"):
                raise DeployError(f"{target.container} became {health or state.get('Status')}")
            if health is None:
                raise DeployError(f"{target.container} has no healthcheck")
            if self.monotonic() >= deadline:
                raise DeployError(f"{target.container} not healthy after {self.cfg.health_timeout}s "
                                  f"({state.get('Status')}/{health})")
            self.sleep(self.cfg.health_poll_interval)

    def converge(self, state: dict | None, project: ComposeProject, desired: dict[str, str],
                 label: str) -> None:
        """Make every managed container run `desired[container]` and be healthy.

        For each container that is not already running the intended image and
        healthy: point the Compose image name at that exact image id, confirm
        the name resolves to it, force-recreate only that service, and confirm
        the new container runs that id and becomes healthy.
        """
        for t in project.targets:
            want = desired[t.container]
            if self._settled(self.inspect_container(t.container), want):
                continue
            if state is not None:
                self._phase(state, f"{label}:{t.container}")
            self.docker("image", "tag", want, t.image_ref)
            if self.image_id(t.image_ref) != want:
                raise DeployError(f"{t.image_ref} does not resolve to the intended image")
            res = self.runner.run(
                [*project.base(), "up", "--detach", "--no-deps", "--no-build", "--pull", "never",
                 "--force-recreate", t.service], self.cfg.up_timeout)
            if res.returncode != 0:
                raise DeployError(f"compose up {t.service} failed: {tail(res.stderr, 10)}")
            self.wait_healthy(t, want)

    # ---- in-progress journal --------------------------------------------

    def _begin(self, state: dict, action: str, from_commit: str, to_commit: str,
               from_images: dict[str, str], to_images: dict[str, str] | None,
               migrations: bool, tags: dict[str, str]) -> None:
        state["in_progress"] = {
            "action": action, "phase": "starting", "started_at": utcnow(),
            "from_commit": from_commit, "to_commit": to_commit,
            "from_images": dict(from_images), "to_images": dict(to_images) if to_images else None,
            "rollback_tags": dict(tags), "migrations": migrations,
        }
        self.store.save(state)

    def _phase(self, state: dict, phase: str) -> None:
        state["in_progress"]["phase"] = phase
        self.store.save(state)

    def refuse_if_interrupted(self, state: dict) -> None:
        ip = state.get("in_progress")
        if ip:
            raise UnsafeStateError(recovery_advice(ip))

    # ---- event bookkeeping ----------------------------------------------

    def note(self, state: dict, target: str, outcome: str, detail: str) -> None:
        """Record a waiting outcome once, not on every timer tick."""
        key = [target, outcome]
        if state.get("last_event") != key:
            state["last_event"] = key
            self.store.save(state)
            self.store.record({"action": "check", "result": outcome, "target": target,
                               "deployed": state["deployed_commit"], "detail": detail})

    # ---- run -------------------------------------------------------------

    def run(self, dry_run: bool = False) -> int:
        self.check_installation()
        state = self.store.load()
        self.refuse_if_interrupted(state)
        deployed = state["deployed_commit"]
        self.check_checkout(deployed)
        project = self.resolve_project()
        self.check_recorded_images(state, project)

        candidate = self.read_candidate()
        if candidate is None:
            log.info("no candidate from the checker yet")
            return EXIT_OK
        if candidate == deployed:
            log.info("up to date at %s", deployed[:12])
            return EXIT_OK
        if candidate in state["failed"]:
            log.info("skipping %s: %s (use `retry` after fixing)", candidate[:12], state["failed"][candidate])
            return EXIT_OK

        self.check_storage()
        target = self.fetch_target()
        if target != candidate:
            log.info("checker candidate %s is not origin/%s %s; waiting for the checker",
                     candidate[:12], self.cfg.branch, target[:12])
            return EXIT_OK
        if not self.is_ancestor(deployed, target):
            raise UnsafeStateError(
                f"origin/{self.cfg.branch} {target[:12]} does not descend from deployed {deployed[:12]} "
                "(history was rewritten); manual action required")

        review = classify_changes(self.changed_paths(deployed, target))
        needs_review = bool(review["migrations"] or review["tooling"])
        if needs_review and state.get("approved") != target:
            detail = "; ".join(f"{k}: {', '.join(v)}" for k, v in review.items() if v)
            log.warning("%s needs manual approval (%s)", target[:12], detail)
            self.note(state, target, "awaiting_approval", detail)
            return EXIT_OK

        # Independent re-verification: the candidate file is only a hint.
        api_head = self.github.branch_head()
        if api_head != target:
            log.info("GitHub reports %s as head, git fetched %s; retrying later", api_head[:12], target[:12])
            return EXIT_OK
        verdict = evaluate_ci_runs(self.github.ci_runs(target), target, self.cfg)
        if verdict.state != "success":
            log.info("not deploying %s: CI %s (%s)", target[:12], verdict.state, verdict.detail)
            self.note(state, target, f"ci_{verdict.state}", verdict.detail)
            return EXIT_OK

        # The release being replaced is the rollback target: it must be verifiable.
        unhealthy = [f"{t.container} {t.status}/{t.health}" for t in project.targets
                     if t.status != "running" or t.health != "healthy"]
        if unhealthy:
            raise UnsafeStateError(
                f"not deploying while the current release is not healthy ({', '.join(unhealthy)}): "
                "an automatic rollback could not be verified. Check `doctor`")

        plan = [f"deploy {deployed[:12]} -> {target[:12]} ({verdict.detail})",
                f"git merge --ff-only {target[:12]} (as uid {self.repo_identity()[0]})",
                f"compose build {' '.join(t.service for t in project.targets)}",
                *[f"recreate {t.service} ({t.container}) if its image changed" for t in project.targets]]
        if review["migrations"]:
            plan.append("APPROVED migration change: backend start runs `alembic upgrade head`")
        if dry_run:
            log.info("dry run, nothing changed. Plan:\n  %s", "\n  ".join(plan))
            return EXIT_OK

        # Final re-checks immediately before the first change.
        if self.remote_head() != target:
            log.info("a newer commit reached %s during checks; retrying later", self.cfg.branch)
            return EXIT_OK
        self.check_clean()
        self.check_storage()
        return self._deploy(state, project, deployed, target, migrations=bool(review["migrations"]),
                            ci_detail=verdict.detail)

    def _deploy(self, state: dict, project: ComposeProject, deployed: str, target: str,
                migrations: bool, ci_detail: str) -> int:
        old_images = project.live_images()
        tags = {}
        for t in project.targets:
            tags[t.container] = rollback_tag(t.container, deployed)
            self.docker("image", "tag", t.image_id, tags[t.container])
        self._begin(state, "deploy", deployed, target, old_images, None, migrations, tags)
        self.store.record({"action": "deploy", "result": "started", "from": deployed, "to": target,
                           "detail": ci_detail, "migrations": migrations})

        self._phase(state, "checkout")
        self.git("merge", "--ff-only", "--quiet", target)
        self._phase(state, "build")
        try:
            build = self.runner.run(
                [*project.base(), "build", *(t.service for t in project.targets)],
                self.cfg.build_timeout)
            if build.returncode != 0:
                log.error("build output (tail):\n%s", tail(build.stderr or build.stdout))
                raise DeployError("image build failed")
            new_images = {t.container: self.image_id(t.image_ref) for t in project.targets}
        except DeployError as exc:
            # Running containers were not touched; only the checkout moved.
            log.error("deploy stopped before any container changed: %s", exc)
            self._phase(state, "restore-checkout")
            try:
                self.move_checkout(None, deployed, {target, deployed})
            except DeployError as exc2:
                log.critical("could not move the checkout back: %s", exc2)
                return self._fail(state, target, "build_failed_checkout_moved", str(exc), keep=True)
            return self._fail(state, target, "build_failed", str(exc))

        state["in_progress"]["to_images"] = new_images
        self._phase(state, "built")
        try:
            self.converge(state, project, new_images, "replace")
        except DeployError as exc:
            log.error("deploy failed: %s", exc)
            if migrations:
                log.critical("NOT rolling back automatically: this release changed migrations and "
                             "the database may now be at a newer revision. %s",
                             recovery_advice({**state["in_progress"], "phase": "failed_migrations"}))
                self._phase(state, "failed_migrations")
                return self._fail(state, target, "health_failed_migrations", str(exc), keep=True)
            try:
                self.converge(state, project, old_images, "rollback")
                self.move_checkout(state, deployed, {target, deployed}, "rollback-checkout")
            except DeployError as exc2:
                log.critical("automatic rollback failed: %s", exc2)
                self._phase(state, "rollback_failed")
                log.critical("%s", recovery_advice(state["in_progress"]))
                return self._fail(state, target, "rollback_failed", f"{exc}; rollback: {exc2}", keep=True)
            return self._fail(state, target, "rolled_back", str(exc))

        state["previous"] = {"commit": deployed, "images": old_images, "tags": tags}
        state["deployed_commit"] = target
        state["images"] = new_images
        state["approved"] = None
        state["last_event"] = None
        state["in_progress"] = None
        state["rollback_tags"] = [*state.get("rollback_tags", []), *tags.values()]
        self.store.save(state)
        self.store.record({"action": "deploy", "result": "success", "from": deployed, "to": target,
                           "images": new_images})
        log.info("deployed %s", target[:12])
        self._prune_rollback_tags(state)
        return EXIT_OK

    def _fail(self, state: dict, target: str, result: str, detail: str, keep: bool = False) -> int:
        """Record a failed deploy. With keep=True the journal stays, blocking later runs."""
        ip = state.get("in_progress") or {}
        state["failed"][target] = result
        state["approved"] = None
        if not keep:
            state["in_progress"] = None
        self.store.save(state)
        self.store.record({"action": ip.get("action", "deploy"), "result": result,
                           "from": ip.get("from_commit"), "to": target, "detail": detail})
        return EXIT_UNSAFE if keep else EXIT_ERROR

    def _prune_rollback_tags(self, state: dict) -> None:
        keep_n = self.cfg.rollback_keep * len(self.cfg.containers)
        tags = state.get("rollback_tags", [])
        protected = set(((state.get("previous") or {}).get("tags") or {}).values())
        old = [t for t in tags[:-keep_n] if t not in protected] if len(tags) > keep_n else []
        for tag in old:
            # Untags only; an image still used by a container is kept by Docker.
            res = self.docker("image", "rm", tag, check=False)
            if res.returncode != 0:
                log.warning("could not remove old rollback tag %s", tag)
        state["rollback_tags"] = [t for t in tags if t not in old]
        self.store.save(state)

    # ---- operator commands -------------------------------------------------

    def rollback(self, dry_run: bool = False, database_downgraded: bool = False) -> int:
        self.check_installation()
        state = self.store.load()
        self.refuse_if_interrupted(state)
        previous = state.get("previous")
        if not previous:
            raise UnsafeStateError("no previous version is recorded")
        prev_commit = validate_sha(previous.get("commit"), "previous commit")
        prev_images = validate_images(previous.get("images"), self.cfg.containers, "previous images")
        deployed = state["deployed_commit"]
        self.check_checkout(deployed)
        project = self.resolve_project()
        self.check_recorded_images(state, project)
        migrations = bool(classify_changes(self.changed_paths(prev_commit, deployed))["migrations"])
        if migrations and not database_downgraded:
            raise UnsafeStateError(
                "the deployed release changed migrations, and the previous backend cannot start "
                "on a newer database revision. Downgrade the database first (ops/deploy/README.md, "
                "'Rollback across a migration'), then re-run with --database-downgraded")
        for c, image in prev_images.items():
            if not self.image_present(image):
                raise UnsafeStateError(f"previous image for {c} is no longer present")
        self.check_storage()
        if dry_run:
            log.info("dry run: would recreate %s and move the checkout to %s",
                     ", ".join(f"{c} on {i[7:19]}" for c, i in prev_images.items()), prev_commit[:12])
            return EXIT_OK
        self._begin(state, "rollback", deployed, prev_commit, project.live_images(), prev_images,
                    migrations, previous.get("tags") or {})
        self.store.record({"action": "rollback", "result": "started", "from": deployed, "to": prev_commit})
        try:
            self.converge(state, project, prev_images, "rollback")
            self.move_checkout(state, prev_commit, {deployed, prev_commit}, "rollback-checkout")
        except DeployError as exc:
            self._phase(state, "rollback_failed")
            log.critical("rollback failed: %s. %s", exc, recovery_advice(state["in_progress"]))
            self.store.record({"action": "rollback", "result": "failed", "from": deployed,
                               "to": prev_commit, "detail": str(exc)})
            return EXIT_UNSAFE
        self._commit_transition(state, prev_commit, prev_images)
        self.store.record({"action": "rollback", "result": "success", "from": deployed, "to": prev_commit})
        log.info("rolled back to %s; %s will be skipped until a newer commit or `retry`",
                 prev_commit[:12], deployed[:12])
        return EXIT_OK

    def _commit_transition(self, state: dict, final_commit: str, final_images: dict[str, str]) -> None:
        """Record where an in-progress operation ended, then clear the journal."""
        ip = state["in_progress"]
        frm, to = ip["from_commit"], ip["to_commit"]
        if ip["action"] == "deploy" and final_commit == to:
            state["previous"] = {"commit": frm, "images": ip["from_images"], "tags": ip.get("rollback_tags") or {}}
            state["rollback_tags"] = [*state.get("rollback_tags", []), *(ip.get("rollback_tags") or {}).values()]
        elif ip["action"] == "deploy":
            state["failed"][to] = "aborted"
        elif final_commit == to:  # rollback completed
            state["previous"] = None
            state["failed"][frm] = "rolled_back_manually"
        state["deployed_commit"] = final_commit
        state["images"] = dict(final_images)
        state["approved"] = None
        state["last_event"] = None
        state["in_progress"] = None
        self.store.save(state)

    def recover(self, mode: str, database_downgraded: bool = False, commit: str | None = None) -> int:
        self.check_installation()
        state = self.store.load()
        ip = state.get("in_progress")
        if mode == "adopt":
            return self._adopt(state, commit)
        if not ip:
            log.info("no interrupted operation is recorded")
            return EXIT_OK
        project = self.resolve_project()
        head = self.head()
        if mode == "status":
            print(self.describe_recovery(ip, project, head))
            return EXIT_OK

        if mode == "complete":
            target, images = ip["to_commit"], ip.get("to_images")
            other = ip["from_commit"]
            if not images:
                raise UnsafeStateError("nothing was built for this operation; use `recover --abort`")
        else:
            target, images, other = ip["from_commit"], ip["from_images"], ip["to_commit"]
        images = validate_images(images, self.cfg.containers, "target images")
        if head not in (ip["from_commit"], ip["to_commit"]):
            raise UnsafeStateError(f"HEAD {head[:12]} is neither commit of the interrupted operation")
        self.check_checkout(None)
        going_back = target != other and self.is_ancestor(target, other)
        if ip.get("migrations") and going_back and not database_downgraded:
            raise UnsafeStateError(
                "returning to the older release crosses a migration: downgrade the database first "
                "(ops/deploy/README.md), then re-run with --database-downgraded")
        for c, image in images.items():
            if not self.image_present(image):
                raise UnsafeStateError(f"intended image for {c} is no longer present; see README")
        self.check_storage()
        self._phase(state, f"recover-{mode}")
        self.store.record({"action": "recover", "result": "started", "mode": mode, "to": target})
        try:
            self.converge(state, project, images, f"recover-{mode}")
            self.move_checkout(state, target, {ip["from_commit"], ip["to_commit"]}, "recover-checkout")
        except DeployError as exc:
            log.critical("recovery failed: %s", exc)
            self.store.record({"action": "recover", "result": "failed", "mode": mode, "detail": str(exc)})
            return EXIT_UNSAFE
        self._commit_transition(state, target, images)
        self.store.record({"action": "recover", "result": "success", "mode": mode, "to": target})
        log.info("recovered: %s is deployed", target[:12])
        return EXIT_OK

    def _adopt(self, state: dict, commit: str | None) -> int:
        """Accept the running containers and HEAD as the deployed state (operator decision)."""
        commit = validate_sha(commit, "--commit")
        head = self.check_checkout(None)
        if head != commit:
            raise UnsafeStateError(f"--commit {commit[:12]} is not HEAD {head[:12]}")
        project = self.resolve_project()
        unhealthy = [t.container for t in project.targets if t.health != "healthy" or t.status != "running"]
        if unhealthy:
            log.warning("adopting while %s are not healthy", ", ".join(unhealthy))
        ip = state.get("in_progress")
        state["deployed_commit"] = commit
        state["images"] = project.live_images()
        state["in_progress"] = None
        state["approved"] = None
        state["last_event"] = None
        self.store.save(state)
        self.store.record({"action": "adopt", "result": "success", "to": commit,
                           "images": state["images"], "cleared": ip})
        log.info("recorded %s and the running images as deployed", commit[:12])
        return EXIT_OK

    def describe_recovery(self, ip: dict, project: ComposeProject, head: str) -> str:
        lines = [recovery_advice(ip), f"HEAD: {head}"]
        for t in project.targets:
            image = t.image_id
            where = ("old release" if image == (ip.get("from_images") or {}).get(t.container)
                     else "new release" if image == (ip.get("to_images") or {}).get(t.container)
                     else "UNKNOWN image")
            lines.append(f"{t.container}: {image[7:19]} ({where}), {t.status}/{t.health}")
        return "\n".join(lines)

    def init(self, commit: str) -> int:
        self.check_installation()
        commit = validate_sha(commit, "--commit")
        if self.store.exists():
            raise UnsafeStateError("deployment state already exists")
        self.check_checkout(commit)
        project = self.resolve_project()
        state = {"version": 1, "deployed_commit": commit, "previous": None, "failed": {},
                 "approved": None, "rollback_tags": [], "last_event": None,
                 "images": project.live_images(), "in_progress": None}
        self.store.save(state)
        self.store.record({"action": "init", "result": "success", "to": commit, "images": state["images"]})
        log.info("recorded %s and the running images as deployed", commit[:12])
        return EXIT_OK

    def approve(self, sha: str, backup_confirmed: bool) -> int:
        self.check_installation()
        sha = validate_sha(sha, "approved commit")
        state = self.store.load()
        self.refuse_if_interrupted(state)
        deployed = state["deployed_commit"]
        self.check_checkout(deployed)
        target = self.fetch_target()
        if target != sha:
            raise UnsafeStateError(f"{sha[:12]} is not the current head of origin/{self.cfg.branch}")
        if not self.is_ancestor(deployed, sha):
            raise UnsafeStateError("approved commit does not descend from the deployed commit")
        review = classify_changes(self.changed_paths(deployed, sha))
        if review["migrations"] and not backup_confirmed:
            raise UnsafeStateError(
                "this release changes migrations: take and verify a database backup, then re-run "
                "with --database-backup-taken")
        state["approved"] = sha
        state["failed"].pop(sha, None)
        state["last_event"] = None
        self.store.save(state)
        self.store.record({"action": "approve", "result": "success", "to": sha,
                           "migrations": review["migrations"], "tooling": review["tooling"]})
        log.info("approved %s; it deploys on the next run if CI and all other gates pass", sha[:12])
        return EXIT_OK

    def retry(self, sha: str) -> int:
        sha = validate_sha(sha, "commit")
        state = self.store.load()
        if state["failed"].pop(sha, None) is None:
            log.info("%s is not marked as failed", sha[:12])
            return EXIT_OK
        state["last_event"] = None
        self.store.save(state)
        self.store.record({"action": "retry", "result": "success", "to": sha})
        log.info("%s will be considered again on the next run", sha[:12])
        return EXIT_OK

    def status(self) -> int:
        state = self.store.load()
        public = {k: state.get(k) for k in ("deployed_commit", "images", "previous", "approved", "failed",
                                             "last_event", "in_progress", "updated_at")}
        print(json.dumps(public, indent=2, sort_keys=True))
        if state.get("in_progress"):
            print("INTERRUPTED: " + recovery_advice(state["in_progress"]))
        for entry in self.store.history(10):
            print(json.dumps(entry, sort_keys=True))
        return EXIT_OK

    # ---- doctor (read-only) ----------------------------------------------

    def doctor(self, online: bool = True) -> int:
        """Every production compatibility check. Never builds, recreates or stops anything."""
        results: list[tuple[str, str, str]] = []

        def check(name: str, fn: Callable[[], tuple[str, str] | None]) -> Any:
            try:
                out = fn()
                status, detail = out if out else ("PASS", "")
            except DeployError as exc:
                status, detail = "FAIL", str(exc)
            results.append((status, name, detail))
            return status

        ctx: dict[str, Any] = {}

        def installation():
            self.check_installation()
            return ("PASS", f"{len(self.code_paths)} paths root-owned, not group/world-writable")

        def state_check():
            ctx["state"] = self.store.load()
            ip = ctx["state"].get("in_progress")
            if ip:
                raise UnsafeStateError(recovery_advice(ip))
            return ("PASS", f"deployed {ctx['state']['deployed_commit'][:12]}")

        def identity_switch():
            uid, gid = self.repo_identity()
            if self.euid() != 0:
                return ("WARN", "not running as root; skipped")
            if uid == 0:
                return ("PASS", "checkout is root-owned; git runs without a uid switch")
            steps = self.identity_probe(uid, gid)
            summary = "; ".join(f"{label}: {result}" for label, result in steps)
            if len(steps) == len(identity_switch_steps(uid, gid)) and all(r == "ok" for _, r in steps):
                return ("PASS", f"git --version as uid {uid}/gid {gid} ({summary})")
            raise DeployError(f"git --version as uid {uid}/gid {gid}: {summary}; {security_context()}")

        def repository():
            uid, gid = self.repo_identity()
            expected = ctx["state"]["deployed_commit"] if "state" in ctx else None
            head = self.check_checkout(expected)
            ctx["head"] = head
            return ("PASS", f"owner uid {uid}/gid {gid}; on {self.cfg.branch}; clean; HEAD {head[:12]}")

        def storage():
            self.check_storage()
            return ("PASS", "check-storage.sh exited 0")

        def compose():
            ctx["project"] = self.resolve_project()
            p = ctx["project"]
            return ("PASS", f"project {p.name}, {p.config_file}, services "
                    + ", ".join(f"{t.service}->{t.container}" for t in p.targets) + ", build contexts in repo")

        def image_refs():
            warn = []
            for t in ctx["project"].targets:
                if self.image_id(t.image_ref) != t.image_id:
                    warn.append(f"{t.image_ref} points at a different image than {t.container} runs "
                                "(built but not recreated outside this tool)")
            return ("WARN", "; ".join(warn)) if warn else ("PASS", "image names resolve to the running images")

        def recorded():
            self.check_recorded_images(ctx["state"], ctx["project"])
            return ("PASS", "running images match the recorded deployment")

        def health():
            bad = [f"{t.container} {t.status}/{t.health}" for t in ctx["project"].targets
                   if t.status != "running" or t.health != "healthy"]
            if any(t.health is None for t in ctx["project"].targets):
                raise DeployError("a managed container has no healthcheck")
            if bad:
                raise DeployError("not healthy: " + ", ".join(bad))
            return ("PASS", "both containers running and healthy")

        def rollback_images():
            prev = ctx["state"].get("previous")
            if not prev:
                return ("WARN", "no previous release recorded yet (normal before the first deploy)")
            for c, image in (prev.get("images") or {}).items():
                if not self.image_present(image):
                    raise DeployError(f"previous image for {c} is missing")
            return ("PASS", f"previous release {prev['commit'][:12]} images present")

        def candidate():
            sha = self.read_candidate()
            return ("PASS", f"candidate {sha[:12]}") if sha else ("WARN", "no candidate yet (checker not run)")

        def privileges():
            members = self.docker_group_members()
            if self.cfg.checker_user in members:
                raise DeployError(f"{self.cfg.checker_user} must not be in the docker group")
            _ = self.checker_uid
            if members:
                return ("WARN", f"docker group members (root-equivalent): {', '.join(members)}")
            return ("PASS", f"{self.cfg.checker_user} exists and has no Docker access")

        def github():
            head = self.remote_head()
            api = self.github.branch_head()
            if api != head:
                return ("WARN", f"git and API disagree on head ({head[:12]} vs {api[:12]}); retry")
            verdict = evaluate_ci_runs(self.github.ci_runs(head), head, self.cfg)
            status = "PASS" if verdict.state == "success" else "WARN"
            return (status, f"origin/{self.cfg.branch} {head[:12]}: CI {verdict.state} ({verdict.detail})")

        check("installed code", installation)
        check("deployment state", state_check)
        check("git identity switch", identity_switch)
        check("production checkout", repository)
        check("SSD verification", storage)
        if check("compose labels, paths, build contexts", compose) != "FAIL":
            check("image references", image_refs)
            if "state" in ctx:
                check("recorded images", recorded)
                check("rollback images", rollback_images)
            check("health checks", health)
        check("checker candidate", candidate)
        check("privilege separation", privileges)
        if online:
            check("GitHub head and CI", github)

        width = max(len(n) for _, n, _ in results)
        for status, name, detail in results:
            print(f"{status:4}  {name:<{width}}  {detail}")
        failed = sum(1 for s, _, _ in results if s == "FAIL")
        print(f"\n{failed} failed, {sum(1 for s, _, _ in results if s == 'WARN')} warnings")
        return EXIT_UNSAFE if failed else EXIT_OK


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--config", default="/etc/ai-document-assistant-deploy/deploy.conf")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="(ai-deploy-check) poll GitHub and publish a candidate")
    r = sub.add_parser("run", help="(root) deploy the candidate if every gate passes")
    r.add_argument("--dry-run", action="store_true", help="run every check, change nothing")
    d = sub.add_parser("doctor", help="(root) read-only production compatibility checks")
    d.add_argument("--offline", action="store_true", help="skip git ls-remote and the GitHub API")
    i = sub.add_parser("init", help="record the running commit and images (first install)")
    i.add_argument("--commit", required=True, help="full SHA currently running in production")
    a = sub.add_parser("approve", help="allow one commit that needs manual review")
    a.add_argument("sha")
    a.add_argument("--database-backup-taken", action="store_true")
    t = sub.add_parser("retry", help="clear the failure mark for a commit")
    t.add_argument("sha")
    b = sub.add_parser("rollback", help="restore the previous release")
    b.add_argument("--dry-run", action="store_true")
    b.add_argument("--database-downgraded", action="store_true",
                   help="the release changed migrations and the database was already downgraded by hand")
    rc = sub.add_parser("recover", help="inspect or resolve an interrupted operation")
    mode = rc.add_mutually_exclusive_group()
    mode.add_argument("--status", dest="mode", action="store_const", const="status")
    mode.add_argument("--complete", dest="mode", action="store_const", const="complete")
    mode.add_argument("--abort", dest="mode", action="store_const", const="abort")
    mode.add_argument("--adopt", dest="mode", action="store_const", const="adopt",
                      help="accept the running containers and HEAD as deployed (with --commit)")
    rc.add_argument("--commit")
    rc.add_argument("--database-downgraded", action="store_true")
    sub.add_parser("status", help="show state and recent history")
    return p


def installed_paths(config_path: str) -> list[str]:
    script = os.path.realpath(__file__)
    paths = [script, os.path.dirname(script)]
    if os.path.exists(config_path):
        paths += [config_path, os.path.dirname(config_path)]
    return paths


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout)
    args = build_parser().parse_args(argv)
    os.umask(0o027)
    try:
        cfg = load_config(args.config if os.path.exists(args.config) else None)
    except (OSError, DeployError) as exc:
        log.error("configuration error: %s", exc)
        return EXIT_ERROR
    paths = installed_paths(args.config)

    if args.command == "check":
        if os.geteuid() == 0:
            log.error("`check` must run as %s, not root", cfg.checker_user)
            return EXIT_UNSAFE
        checker = Checker(cfg, code_paths=paths)
        try:
            fd = _lock_file(checker.lock_path)
        except LockedError:
            return EXIT_LOCKED
        try:
            return checker.check()
        except DeployError as exc:
            log.error("check stopped: %s", exc)
            return EXIT_ERROR
        finally:
            os.close(fd)

    if os.geteuid() != 0:
        log.error("`%s` must run as root (it manages containers)", args.command)
        return EXIT_UNSAFE
    store = StateStore(cfg.state_dir)
    deployer = Deployer(cfg, store, code_paths=paths)
    try:
        if args.command == "status":
            return deployer.status()
        if args.command == "doctor":
            return deployer.doctor(online=not args.offline)
    except DeployError as exc:
        log.error("%s", exc)
        return EXIT_UNSAFE
    if args.command == "recover" and (args.mode or "status") == "status":
        try:
            return deployer.recover("status")
        except DeployError as exc:
            log.error("%s", exc)
            return EXIT_UNSAFE
    try:
        store.lock()
    except LockedError as exc:
        log.info("%s; exiting", exc)
        return EXIT_LOCKED
    try:
        if args.command == "run":
            return deployer.run(dry_run=args.dry_run)
        if args.command == "init":
            return deployer.init(args.commit)
        if args.command == "approve":
            return deployer.approve(args.sha, args.database_backup_taken)
        if args.command == "retry":
            return deployer.retry(args.sha)
        if args.command == "rollback":
            return deployer.rollback(dry_run=args.dry_run, database_downgraded=args.database_downgraded)
        if args.command == "recover":
            return deployer.recover(args.mode, args.database_downgraded, args.commit)
        return EXIT_ERROR
    except UnsafeStateError as exc:
        log.error("refusing to continue: %s", exc)
        return EXIT_UNSAFE
    except DeployError as exc:
        log.error("stopped: %s", exc)
        return EXIT_ERROR
    finally:
        store.unlock()


if __name__ == "__main__":
    sys.exit(main())
