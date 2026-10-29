# Pull-based auto-deployment

`autodeploy.py` keeps the self-hosted AI Document Assistant on the latest
commit of `main` that passed CI. The server pulls from GitHub, and nothing on
GitHub can reach the server: no self-hosted runner, no webhook and no write
token. Any error, timeout, unexpected response or unclear state stops it before
anything changes.

## Architecture

```text
 every 5 min                         on candidate change + every 15 min
┌──────────────────────────────┐     ┌──────────────────────────────────────────────┐
│ ai-document-assistant-check  │     │ ai-document-assistant-deploy (root)          │
│ user ai-deploy-check         │     │                                              │
│ no Docker, no checkout       │     │ re-verifies EVERY gate itself:               │
│                              │     │  checkout, SSD, recorded images, fetch,      │
│ git ls-remote (HTTPS)        │     │  ancestry, migrations, API head + exact-     │
│ GitHub API: head + ci.yml    │────▶│  commit CI, health of current release        │
│ writes candidate.json        │hint │ git runs as the checkout's owner             │
│ only when CI succeeded       │     │ builds/recreates ai-backend, ai-frontend only│
└──────────────────────────────┘     └──────────────────────────────────────────────┘
```

The checker is the only role that polls the network on a schedule. It holds
no privileges: no Docker socket (made inaccessible in its unit), no write
access to the checkout, and an empty capability set. The executor treats
`candidate.json` as an untrusted hint. It accepts the file only if it is a
regular file owned by `ai-deploy-check`, at most 4 KiB, with a strict JSON
shape, and it re-checks everything before changing anything. A compromised
checker can at most trigger a run that then refuses.

| Gate (executor) | How it is checked |
| --- | --- |
| Installation intact | `autodeploy.py`, its directory, `deploy.conf` and its directory are root-owned and not group/world-writable |
| No interrupted operation | The `in_progress` journal is empty (see "Interrupted operations") |
| Checkout is safe | On `main`, `origin` is `https://github.com/HishamFekri/ai-document-assistant(.git)`, clean including untracked files, `HEAD` equals the recorded deployed commit, worktree and `.git` have the same owner |
| State matches reality | Each managed container runs exactly the image ID recorded at the last deploy (or `init`) |
| Compose matches production | Labels name `/srv/docker/ai-document-assistant/compose.yaml`; one project; each service declares that `container_name`; literal build context inside the checkout; declared `image:` matches the running image name |
| SSD mounted | `/srv/docker/apps/check-storage.sh` exits 0. It must be root-owned, not group/world-writable and executable, or it is not run. Checked again right before the first change. |
| Target is the latest `main` | The candidate must equal the fetched `origin/main`, and the GitHub branch API must agree. `git ls-remote` is checked again immediately before the first change. |
| Linear history | The target descends from the deployed commit |
| CI passed for that exact commit | `ci.yml` runs filtered by `branch=main`, `event=push`, `head_sha`. Every run is validated (SHA, branch, event, workflow path, repository, head repository not a fork). The newest run must be `completed/success`. Queued, running or missing means wait. |
| Database safety | Changes under `backend/migrations/` or to `backend/alembic.ini` need approval |
| Tooling changes | Changes under `ops/deploy/` need approval (the installed copy lives in `/opt`) |
| Rollback is verifiable | Both current containers have a healthcheck and are `healthy` |

## What a deploy does

1. Tags the running images as `ai-document-assistant-rollback:<container>-<old sha12>`
   and writes the `in_progress` journal (fsynced).
2. `git merge --ff-only <target>`, run as the checkout's owner (hooks disabled,
   no stored credentials, HTTPS only).
3. `docker compose … build <backend service> <frontend service>` (`BUILD_TIMEOUT`).
4. For the backend, then the frontend, only if its image changed:
   - `docker image tag <new image id> <Compose image name>`, then confirms the
     name resolves to that ID;
   - `docker compose … up --detach --no-deps --no-build --pull never --force-recreate <service>`;
   - waits until the container runs **that exact image ID** and is `healthy`
     (`HEALTH_TIMEOUT`).
5. Records the new commit and image IDs, keeps the previous ones as the
   rollback target, and clears the journal.

Every restoration (automatic rollback, `rollback`, `recover`) uses the same
routine, step 4. The intended image is addressed by its immutable ID, never by
a name that a later build could have moved. The tool confirms the retag took
effect, forces the recreate, and checks that the new container runs that ID.

The Compose project, working directory and file come from the running
containers' labels, so paths and `.env` resolve exactly as in production.
`--no-deps` means PostgreSQL, Redis and everything else are never started,
stopped or recreated. A guard in `Runner` refuses `compose down`/`stop`/`rm`/
`restart`/`run`, `--remove-orphans`, `-V`, `docker volume`/`system`/`prune`/
`stop`/`rm`, `systemctl`, `chown`, `git push`/`clean`/`checkout`/`reset --hard`,
and any command naming `hesham-postgres`, `hesham-redis` or `hesham-portfolio`.

### When a deploy fails

| Failure | Result | Exit |
| --- | --- | --- |
| Build fails or times out | Containers untouched; checkout moved back with `git reset --keep` (refuses to discard anything); journal cleared; commit marked `build_failed` | 1 |
| A container is unhealthy, exits, times out, or does not run the intended image | Every changed container is restored to its exact previous image ID; checkout moved back; journal cleared; `rolled_back` | 1 |
| …and the release changed migrations (approved deploy) | **No automatic rollback** (the database may be at the new revision). Journal kept at `failed_migrations`. | 2 |
| Automatic rollback fails | Journal kept at `rollback_failed`; every later run refuses | 2 |

A failed commit is skipped until a newer commit reaches `main` or you run `retry <sha>`.

## Interrupted operations

Before each phase the executor fsyncs `in_progress` into `state.json`: the
phase, both commits, the old image IDs, and the new ones once built. After a
kill, crash, timeout of the unit or power loss, the journal stays. Every
`run`, `rollback` and `approve` then refuses and prints instructions for that
phase. The tool never assumes that the recorded state matches the containers:
each run also compares the live image IDs with the recorded ones.

| Phase in `in_progress.phase` | What may have happened | Do this |
| --- | --- | --- |
| `starting`, `checkout` | Checkout may have moved; no container changed | `recover --abort` |
| `build`, `restore-checkout` | Checkout moved; images partly built; no container changed | `recover --abort` |
| `built`, `replace:<container>` | Containers may be split between old and new | `recover --status`, then `recover --complete` or `recover --abort` |
| `rollback:<container>`, `rollback-checkout`, `rollback_failed` | Restoring the old release failed or was cut short | Fix the cause (`docker logs --tail 200 ai-backend`), then `recover --abort` (deploy) or `recover --complete` (manual rollback) |
| `failed_migrations` | Approved migration release unhealthy; database may be newer | "Rollback across a migration" below, or fix and `recover --complete` |
| `recover-…` | A recovery was cut short | Run the same `recover` command again |

- `recover --status` is read-only. It shows the phase, `HEAD`, and for each
  container whether it runs the old image, the new one, or an unknown one.
- `recover --complete` brings both containers to the operation's target images
  (same verified routine) and moves the checkout forward.
- `recover --abort` brings both back to the old images and moves the checkout back.
- Going to an older release across a migration additionally requires
  `--database-downgraded`, after the manual downgrade.
- `recover --adopt --commit <HEAD sha>` is the explicit escape hatch: "the
  containers are what I want; record them and HEAD as deployed". Use it after
  fixing things by hand, or when the containers were changed outside the tool,
  for example by `ai-document-assistant.service` at boot. It refuses unless the
  given SHA is `HEAD`, and warns if anything is unhealthy.

## Privileges

| Role | Runs as | Can |
| --- | --- | --- |
| Checker | `ai-deploy-check`: system user, no login shell, **not** in the `docker` group | HTTPS to GitHub; write `/var/lib/ai-document-assistant-check` |
| Executor, doctor | `root` (the systemd default; `User=` is deliberately unset, see "Known cause" below), in a systemd sandbox | Docker socket; write the checkout and `/var/lib/ai-document-assistant-deploy`; capabilities limited to `CAP_SETUID CAP_SETGID CAP_KILL CAP_DAC_READ_SEARCH` |
| git (inside the executor) | the checkout's existing owner (uid/gid read from the directory) | read/write the checkout, nothing else root could |

**Unavoidable risk: Docker access is root-equivalent.** Anything that can
talk to the Docker socket can start a privileged container and own the host.
The design limits this risk but cannot remove it:

- No account is added to the `docker` group for this tool (the earlier design's
  `ai-deploy` docker-group user is not used). `doctor` warns about any members
  of the `docker` group, because each one is root-equivalent.
- The only root process starts from systemd, runs root-owned code it checks
  before acting, and gets its inputs from root-owned config and GitHub. It
  re-verifies the checker's hint itself.
- `docker compose build` executes the repository's Dockerfiles (`RUN` steps) on
  this host's Docker daemon. Whoever can merge to `main`, with CI passing, can
  therefore run code in build containers and in production. Branch protection,
  required reviews and 2FA on the GitHub account are part of this system's security.
- Further isolation (rootless Docker, or a filtering socket proxy) is possible
  but outside this tool's scope. A filtering proxy is hard to make
  build-compatible.

Secrets: no write token exists. An optional fine-grained token with **no
permissions** (public repositories, read-only) can be given via systemd
`LoadCredential=github-token:…`. It is sent only as an unredirected header,
redirects are refused, and it is never logged or placed in an argument or the
environment. Child processes get a minimal environment; git ignores every
user and system config (`GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`).
`docker compose config` is read with `--no-interpolate`, and nothing from it is
logged. Build output is logged only on failure (last 30 lines).

## Installation prerequisites (exact)

| Requirement | Check | Ubuntu 24.04 default |
| --- | --- | --- |
| systemd ≥ 252 (`PrivateIPC`, `ProtectProc`, `LoadCredential`) | `systemctl --version` | 255 ✓ |
| Python ≥ 3.9 at `/usr/bin/python3` (stdlib only; `subprocess` `user=`/`extra_groups=`) | `python3 --version` | 3.12 ✓ |
| git ≥ 2.32 (`GIT_CONFIG_GLOBAL`) | `git --version` | 2.43 ✓ |
| Docker Engine + Compose plugin ≥ v2.20 (`up --pull never`) | `docker compose version` | install from Docker's repository |
| Outbound HTTPS to `github.com` and `api.github.com` | `curl -sI https://api.github.com` | — |
| `ai-backend` and `ai-frontend` exist, created by Compose from `/srv/docker/ai-document-assistant/compose.yaml`, each with `container_name`, a literal `build.context` inside `/srv/storage/projects/ai-document-assistant`, and a working healthcheck (the Dockerfiles define them) | `doctor` | — |
| `/srv/docker/apps/check-storage.sh`: root-owned, not group/world-writable, executable, exits 0 only when the SSD UUID matches, works as root inside the sandbox (read-only `/`, no `/home`) | `stat -c '%U %a' …`, `doctor` | — |
| Checkout: on `main`, clean, at `9172b415f43e0f43753accf0fd5296002191ac55`, `origin` = the HTTPS URL, worktree and `.git` owned by the same user | `git status`, `doctor` | — |

No `chown` of the checkout and no ACL on `.env` are needed. The executor reads
`.env` as root, exactly as `docker compose` does today. Git keeps the
checkout's current owner because it runs as that user.

## Install (on the server)

Nothing below has been run. Review each step first.

```bash
# 1. Unprivileged checker account (no docker group, no shell).
sudo useradd --system --no-create-home --home-dir /nonexistent \
  --shell /usr/sbin/nologin ai-deploy-check
id ai-deploy-check            # must NOT list the docker group

# 2. Code and config, root-owned and not writable by any other account.
sudo install -d -o root -g root -m 0755 /opt/ai-document-assistant-deploy /etc/ai-document-assistant-deploy
sudo install -o root -g root -m 0644 ops/deploy/autodeploy.py ops/deploy/README.md \
  ops/deploy/INTEGRATION-TEST-PLAN.md /opt/ai-document-assistant-deploy/
sudo install -o root -g root -m 0644 ops/deploy/deploy.conf.example /etc/ai-document-assistant-deploy/deploy.conf

# 3. Units.
sudo install -o root -g root -m 0644 ops/deploy/systemd/ai-document-assistant-check.service \
  ops/deploy/systemd/ai-document-assistant-check.timer \
  ops/deploy/systemd/ai-document-assistant-deploy.service \
  ops/deploy/systemd/ai-document-assistant-deploy.timer \
  ops/deploy/systemd/ai-document-assistant-deploy.path \
  ops/deploy/systemd/ai-document-assistant-deploy-doctor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemd-analyze verify /etc/systemd/system/ai-document-assistant-*.service \
  /etc/systemd/system/ai-document-assistant-*.timer /etc/systemd/system/ai-document-assistant-deploy.path
sudo systemd-analyze security ai-document-assistant-check.service ai-document-assistant-deploy.service

# 4. The storage check must be root-owned and not writable by others.
stat -c '%U %a %n' /srv/docker/apps/check-storage.sh
```

`deployctl` below is shorthand for
`sudo /usr/bin/python3 -I /opt/ai-document-assistant-deploy/autodeploy.py --config /etc/ai-document-assistant-deploy/deploy.conf`.

### First run

```bash
git -C /srv/storage/projects/ai-document-assistant status --short --branch
git -C /srv/storage/projects/ai-document-assistant rev-parse HEAD   # 9172b415f43e0f43753accf0fd5296002191ac55

deployctl init --commit 9172b415f43e0f43753accf0fd5296002191ac55   # records commit + running image IDs

# Read-only checks inside the real executor sandbox (also proves the hardening works):
sudo systemctl start ai-document-assistant-deploy-doctor.service
journalctl -u ai-document-assistant-deploy-doctor.service -n 40 --no-pager

# The checker in its own sandbox; with production at the head of main this
# publishes a candidate equal to the deployed commit, which the executor ignores.
sudo systemctl start ai-document-assistant-check.service
journalctl -u ai-document-assistant-check.service -n 20 --no-pager

deployctl run --dry-run
```

`doctor` runs every compatibility check even when no new commit exists:
installation, state and journal, checkout ownership and cleanliness, SSD
script, Compose labels, paths, build contexts and declared images, whether
image names resolve to the running images, recorded vs live image IDs,
healthchecks, rollback images, the candidate file, privilege separation, and
(unless `--offline`) `git ls-remote` plus the GitHub head and CI. It never
builds, recreates, stops or restarts anything, never fetches, and never writes
state. Exit 0 means no FAIL lines.

Before enabling, read `systemctl cat ai-document-assistant.service`. If it runs
`docker compose up --build` or `pull` at boot, containers can change outside
this tool. The executor will then refuse with "no longer run the recorded
image" until you check and run `recover --adopt --commit <HEAD>`. This tool
does not modify that unit.

```bash
sudo systemctl enable --now ai-document-assistant-check.timer \
  ai-document-assistant-deploy.path ai-document-assistant-deploy.timer
journalctl -u ai-document-assistant-check.service -u ai-document-assistant-deploy.service -f
```

## Day to day

| Task | Command |
| --- | --- |
| Logs | `journalctl -u ai-document-assistant-deploy.service -u ai-document-assistant-check.service` |
| State, journal and last 10 history entries | `deployctl status` |
| Full history | `/var/lib/ai-document-assistant-deploy/history.jsonl` (one JSON object per line) |
| Read-only health of the whole setup | `deployctl doctor` (or the doctor unit) |
| Pause everything | `sudo systemctl stop ai-document-assistant-check.timer ai-document-assistant-deploy.path ai-document-assistant-deploy.timer` |
| Resume | the same with `start` |
| Try a failed commit again | `deployctl retry <sha>` |

Overlapping runs cannot happen. Each role takes its own exclusive `flock`
(`deploy.lock`, `check.lock`), and a second instance exits 75. Exit codes:
`0` ok or waiting · `1` stopped by an error (nothing changed, or cleanly rolled
back) · `2` unsafe state, needs an operator · `75` lock held.

## Deploying a migration (manual approval)

The backend runs `python -m alembic upgrade head` on every start, so a release
with migration changes would migrate production as soon as its container
starts. It waits instead:

1. Read the new migration files, including `downgrade()`. Look for long locks
   and for data a downgrade would drop. Not every migration can be undone
   without data loss, and some cannot be undone at all.
2. Take a PostgreSQL backup and confirm it restores
   (`backend/docs/rollback-runbook.md`, "Before every deploy that includes a migration").
3. Note the current revision: `docker exec ai-backend python -m alembic current`.
4. `deployctl approve <sha> --database-backup-taken`. The approval is void if `main` moves on.
5. `sudo systemctl start ai-document-assistant-deploy.service`, or wait for the timer.

Approval never bypasses CI, the SSD check, the clean-tree check or the
latest-commit rule. Tooling-only changes (`ops/deploy/`) use `approve` without
the backup flag. Re-install the files from Install step 2 first.

## Rollback

### Application only (no migration between the releases)

```bash
deployctl rollback --dry-run
deployctl rollback
```

This restores each container to the exact previous image ID with the verified
routine and moves the checkout back with `git reset --keep`. It marks the bad
commit so it is not redeployed until a newer commit or `retry`. Only one step
back is recorded. If it fails, the journal stays at `rollback_failed` and
`recover` takes over.

### Rollback across a migration

Old backend code cannot start on a newer database revision: it fails with
`Can't locate revision identified by '<rev>'`. **Migrations are not
automatically reversible.** This procedure works whether the new backend is
healthy, unhealthy, or cannot start at all, because it never relies on
`docker exec` into the running backend.

1. Pause everything (see "Day to day"), then take a backup of the current database.
2. **Stop the backend**, so its restart policy cannot re-run `alembic upgrade head`
   after the downgrade. The UI stays up; the API is unavailable until step 7.
   ```bash
   docker stop ai-backend
   ```
3. Collect the two image IDs from `deployctl status`. NEW is `images` (or
   `in_progress.to_images`) for `ai-backend`; OLD is `previous.images` (or
   `in_progress.from_images`).
4. Find the revision OLD expects, without touching the database:
   ```bash
   docker run --rm --network none --entrypoint python <OLD id> -m alembic heads
   ```
   If that fails, read it from git: the target is the `down_revision` of the
   oldest file in `git -C <checkout> diff --name-only <old sha> <new sha> -- backend/migrations/versions`.
5. Inspect and downgrade **with NEW**, which contains the `downgrade()` code. Use
   a one-off container from the same Compose service, so it has the same `.env`,
   networks and volumes. It does not start dependencies, does not use the
   `ai-backend` name, and leaves the stopped container alone:
   ```bash
   P=/srv/docker/ai-document-assistant
   project=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' ai-backend)
   service=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' ai-backend)
   ref=$(docker inspect --format '{{.Config.Image}}' ai-backend)
   docker image inspect --format '{{.Id}}' "$ref"      # must print the NEW id; if not:
   # docker image tag <NEW id> "$ref"
   dc() { docker compose --project-name "$project" --project-directory "$P" --file "$P/compose.yaml" "$@"; }
   dc run --rm --no-deps --entrypoint python "$service" -m alembic current
   dc run --rm --no-deps --entrypoint python "$service" -m alembic downgrade <revision from step 4>
   dc run --rm --no-deps --entrypoint python "$service" -m alembic current   # must print it
   ```
   If a migration failed halfway, `current` shows where it stopped. Downgrade
   from there. If a `downgrade()` fails or would destroy data you need, stop and
   restore the backup into a **new** database instead
   (`backend/docs/rollback-runbook.md`, section D).
6. Restore the old code. The command recreates `ai-backend` on OLD, starting it
   again, waits for `healthy`, and moves the checkout back:
   - after a failed or interrupted deploy (`deployctl status` shows `in_progress`):
     `deployctl recover --abort --database-downgraded`;
   - after a successful deploy: `deployctl rollback --database-downgraded`.
7. Verify `/health`, `/ready`, sign-in, the chat list and an upload, then resume
   the timers once a fix is on `main`.

### Without the tool

The rollback images, state and history are plain Docker tags and JSON:

```bash
docker image ls ai-document-assistant-rollback
ref=$(docker inspect --format '{{.Config.Image}}' ai-backend)
docker image tag <previous image id> "$ref"
docker image inspect --format '{{.Id}}' "$ref"        # must print that id
dc up --detach --no-deps --no-build --pull never --force-recreate <backend service>
docker inspect --format '{{.Image}} {{.State.Health.Status}}' ai-backend
# same for the frontend; then, as the checkout's owner:
#   git -C <checkout> reset --keep <previous sha>
# and record it: deployctl recover --adopt --commit <previous sha>
```

If the previous images are gone, `git reset --keep <sha>` as the checkout owner,
then `dc build <service>` and the `up` line rebuild that version.

## Reconciling a checkout that moved outside the executor

If someone runs `git pull` in the production checkout, `HEAD` no longer
matches `deployed_commit`. Every `run`, `rollback` and `approve` then refuses
with "HEAD … is not the recorded deployed commit". This is intended: the
running containers were built from the recorded commit, not from `HEAD`.
Do not start the executor until this is resolved. Pause the timers and the
path unit first (see "Day to day").

1. Read-only checks:
   ```bash
   deployctl status                      # deployed_commit, images, no in_progress
   R=/srv/storage/projects/ai-document-assistant
   OLD=<deployed_commit>; NEW=$(git -C "$R" rev-parse HEAD)
   git -C "$R" merge-base --is-ancestor "$OLD" "$NEW" && echo linear
   git -C "$R" diff --name-only "$OLD" "$NEW"                              # what moved
   git -C "$R" diff --name-only "$OLD" "$NEW" -- . ':(exclude)ops/deploy'  # app changes
   git -C "$R" diff --name-only "$OLD" "$NEW" -- backend/migrations backend/alembic.ini
   git -C "$R" status --porcelain=v1 --untracked-files=all                 # must be empty
   deployctl doctor --offline            # every line except the checkout check must pass
   ```
2. Decide:
   - **Only `ops/deploy/` (or other non-build files) changed**, history is
     linear, the tree is clean, and doctor shows the running images match the
     recorded ones: the containers are equivalent to a build of `HEAD`. Record
     it explicitly with `deployctl recover --adopt --commit <NEW>`. That writes
     only the state file; no container is touched. If `ops/deploy/` changed,
     install the reviewed files to `/opt` first (Install step 2).
   - **Application files or migrations changed**: the running containers do
     **not** match `HEAD`. Do not adopt. Move the checkout back as its owner
     (`sudo -u <owner> git -C "$R" reset --keep "$OLD"`) and let the executor
     deploy `NEW` through its gates, with approval if migrations changed.
3. Re-run `deployctl doctor` and confirm 0 failures before resuming the timers.

## Troubleshooting: git fails with "Operation not permitted" in the sandbox

As root, the executor starts git with `subprocess.run(user=<owner uid>,
group=<owner gid>, extra_groups=[])`. The child calls `setgroups`, `setregid`,
`setreuid`, then `execve`. Spawn errors now report which step failed:

```text
could not run git -C …: Operation not permitted [stage=pre-exec: fork, or setgroups/setregid/setreuid
in the child, failed before exec; errno=EPERM; uid/gid switch to 1000/1000; executable=/usr/bin/git;
CapEff=…(CAP_…) CapBnd=… NoNewPrivs=1 Seccomp=2 Seccomp_filters=N]
```

### Known cause: `User=root` with systemd v255

Seen on Ubuntu 24.04 (systemd 255): `+setreuid: FAILED EPERM at pre-exec`,
with `CapBnd=…e4` (includes `CAP_SETUID`) but `CapEff=…64` (does not).

In `src/core/exec-invoke.c`, an explicit `User=` (even `User=root`) gives
systemd a valid uid. Together with any seccomp-based sandbox option
(`RestrictNamespaces=`, `LockPersonality=`, `ProtectClock=`, …) and no
`SystemCallFilter=` that forbids dropping privileges, that selects the
`keep_seccomp_privileges` path. After the user switch, that path calls
`drop_capability(CAP_SETUID)` unless `CAP_SETUID` is in `AmbientCapabilities=`.
`drop_capability` clears the inheritable, permitted and effective sets. Under
`NoNewPrivileges=yes`, `execve()` cannot regain a capability that was not
already permitted, so the service starts without `CAP_SETUID` even though the
bounding set contains it. `CAP_SETGID` is not dropped, which is why
`setgroups` and `setregid` still succeed. Capability checks write no audit
records.

The executor and doctor units therefore **do not set `User=`**. Root is the
default for system services, so the uid stays unset in systemd and the path is
skipped. Nothing else changes: same bounding set, empty ambient set,
`NoNewPrivileges=yes`, same filesystem and syscall restrictions. A regression
test fails if either unit sets `User=` or loses one of these restrictions. The
checker unit keeps `User=ai-deploy-check`, because it needs no capabilities.

Rejected alternatives: `AmbientCapabilities=CAP_SETUID` also skips the drop,
but it keeps the path active (it briefly raises `CAP_SYS_ADMIN`/`CAP_SETPCAP`
before exec) and hands the capability to every child explicitly. Disabling
`NoNewPrivileges` removes a protection for no gain.

Reproduce it (read-only; `grep` prints its own capabilities):

```bash
P=(-p NoNewPrivileges=yes -p ProtectClock=yes -p "CapabilityBoundingSet=CAP_SETUID CAP_SETGID CAP_KILL CAP_DAC_READ_SEARCH")
sudo systemd-run --wait --pipe --collect -q -p User=root "${P[@]}" grep -E '^Cap(Prm|Eff|Bnd)' /proc/self/status  # CapEff ...64
sudo systemd-run --wait --pipe --collect -q "${P[@]}" grep -E '^Cap(Prm|Eff|Bnd)' /proc/self/status               # CapEff ...e4
```

### Diagnosing other causes

`doctor` also has a **git identity switch** check. It runs `git --version` four
times, adding one credential change each time (no switch → `setgroups([])` →
`setregid` → `setreuid`), and stops at the first refused step. Everything it
prints is read from `/proc/self/status`, so it contains no secrets.

1. Run the doctor unit and keep its "git identity switch" line:
   `sudo systemctl start ai-document-assistant-deploy-doctor.service; journalctl -u ai-document-assistant-deploy-doctor.service -n 40 --no-pager`
2. Confirm the installed unit is the reviewed one:
   `systemctl cat ai-document-assistant-deploy-doctor.service` (look for drop-ins)
   and `systemd-delta --type=extended`. `systemctl show -p User` must print
   an empty `User=` for the executor and doctor units.
3. Reproduce with the unit's exact properties, then drop one at a time. Run
   this on the server (read-only: only `git --version` runs):
   ```bash
   read -r UID_ GID_ < <(stat -c '%u %g' /srv/storage/projects/ai-document-assistant)
   PROPS=(UMask=0077 NoNewPrivileges=yes PrivateTmp=yes ProtectSystem=strict ProtectHome=yes
     ReadWritePaths=/srv/storage/projects/ai-document-assistant ProtectKernelTunables=yes
     ProtectKernelModules=yes ProtectKernelLogs=yes ProtectControlGroups=yes ProtectClock=yes
     ProtectHostname=yes "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6" RestrictNamespaces=yes
     RestrictRealtime=yes RestrictSUIDSGID=yes LockPersonality=yes SystemCallArchitectures=native
     "CapabilityBoundingSet=CAP_SETUID CAP_SETGID CAP_KILL CAP_DAC_READ_SEARCH" AmbientCapabilities=)
   probe() {
     local args=(); for p in "$@"; do args+=(-p "$p"); done
     sudo systemd-run --wait --pipe --collect --quiet "${args[@]}" /usr/bin/python3 -I -c \
       "import sys; sys.path.insert(0, '/opt/ai-document-assistant-deploy'); import autodeploy as a
   print(a.security_context())
   for step in a.probe_identity_switch($UID_, $GID_): print(*step)"
   }
   echo '== all properties'; probe "${PROPS[@]}"
   for i in "${!PROPS[@]}"; do
     echo "== without ${PROPS[$i]}"; probe "${PROPS[@]:0:$i}" "${PROPS[@]:$((i+1))}"
   done
   ```
4. Compare `CapEff`, `NoNewPrivs`, `Seccomp` and `Seccomp_filters` between the
   doctor unit's output and the "all properties" probe. If they differ, the
   transient probe is not equivalent: `systemctl show` both units and compare
   the implied properties (`SystemCallFilter`, `DevicePolicy`, `DeviceAllow`,
   `CapabilityBoundingSet`).

| Evidence | Points to | Smallest fix |
| --- | --- | --- |
| `+setreuid` fails, `CapBnd` has `CAP_SETUID` but `CapEff` does not | systemd v255 dropped it because `User=` is set (see "Known cause") | Remove `User=` from the unit or drop-in. Nothing is weakened. |
| First failing step is `+setgroups`, `+setregid` or `+setreuid`, and `CapEff` lacks `CAP_SETGID`/`CAP_SETUID` | The capabilities are not effective (drop-in, edited unit) | Restore the reviewed unit. Nothing is weakened. |
| Same step fails, `CapEff` has both, and one removed property makes the probe pass | A seccomp `ERRNO(EPERM)` filter from that property (these do not write audit records) | Remove only that property from the executor and doctor units, with the probe output recorded as the justification in the unit comment |
| `stage=exec` | `execve` refused: file capabilities (`getcap /usr/bin/git`), an LSM transition under `NoNewPrivileges` (`aa-status`), or a `git` earlier in `PATH` (`executable=` shows which) | Fix that cause. Do not drop `NoNewPrivileges`. |
| The probe passes in the doctor unit but git still fails | Not the credential switch | Compare the full `could not run` detail |

If a policy that should not be relaxed turns out to block credential changes,
a different design avoids them: git would run in a separate unit whose
`User=` is the checkout owner, and the executor would lose `CAP_SETUID` and
`CAP_SETGID` entirely. That is a larger change, so it is only proposed on
that evidence.

## Limitations and open risks

- **Trust in `main`** and **Docker root-equivalence**: see "Privileges".
- **CI does not build the production image.** CI runs tests and a placeholder
  frontend build. The backend base image (`python:3.11-slim`) is not pinned by
  digest, so the server can build a different image than CI tested.
- **Migrations are detected by path only.** Code that changes how data is
  written, without a migration, deploys automatically. Rolling such a release
  back may leave data the older code does not expect.
- **`alembic upgrade head` on every backend start.** With no migration changes
  it is a no-op, assuming the database is at head. If someone downgraded it and
  left the newer code running, the next restart upgrades it again.
- **Short downtime.** Containers are recreated in place, with no blue/green
  deploy, so there are a few seconds per changed container.
- **A broken production stays broken.** The executor refuses to deploy while
  the current release is unhealthy, because a rollback could not be verified.
  Fix it by hand, then `recover --adopt`.
- **Unverified on the server.** `check-storage.sh`, the production
  `compose.yaml` and `ai-document-assistant.service` are not in this repository
  and were not read. The sandbox's capability set has not been exercised on the
  server. The doctor unit is the first real check, and
  `INTEGRATION-TEST-PLAN.md` covers a disposable rehearsal.
- **A crash between steps can leave stale rollback tags.** The journal shows
  them, and they are pruned on the next success. Build cache and dangling images
  are not pruned.
- **Anonymous API limit.** 60 requests/hour per server IP, shared with
  anything else on the host. The checker uses 2 calls per check only while a
  new head waits for CI; the executor uses 2 per deploy attempt.
- Offline tests use fakes for git, Docker and GitHub. The flock and symlink
  tests run only on Linux.

## Tests

```bash
python3 -m unittest discover -s ops/deploy/tests -v
```

They need no network, Docker, git checkout or root. They cover CI verdicts and
response validation, API failure and rate limits, the checker role (never
touches Docker or the checkout), candidate-file validation, every executor gate,
git running as the checkout owner, installation integrity, image drift,
build/health/recreate/retag failures, partial deploys with failed rollbacks,
interruption at every phase with `recover --status/--complete/--abort/--adopt`,
manual rollback, and `doctor` with each failure class (it is also asserted
never to change anything). Real-system rehearsal: `INTEGRATION-TEST-PLAN.md`.
