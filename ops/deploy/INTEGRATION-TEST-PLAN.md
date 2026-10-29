# Linux integration test plan (disposable environment only)

The offline unit tests replace git, Docker and GitHub with fakes. This plan
exercises the real binaries, the systemd sandbox and Docker Compose behavior
on a **throwaway VM with throwaway data**. Never run it on the production
server: several steps deliberately break containers, kill the executor
mid-deploy, and simulate an unmounted disk.

## 1. Environment

| Item | Setup |
| --- | --- |
| VM | Fresh Ubuntu 24.04 (Multipass, LXD VM or a cloud instance), 2 vCPU, 4 GB RAM, 20 GB disk. A VM, not a container: the tests need systemd and a Docker daemon. Destroy it afterwards. |
| Docker | Docker Engine and the Compose plugin from Docker's apt repository. Record `docker compose version` (must be ≥ 2.20). |
| GitHub | A **new public sandbox repository** under your account (for example `<you>/autodeploy-sandbox`), not the real one. It holds a minimal `backend/` and `frontend/` (see 2) and `.github/workflows/ci.yml` with a job you can make pass or fail through a file such as `ci-should-fail`. |
| Config | `/etc/ai-document-assistant-deploy/deploy.conf` with `REPO_SLUG=<you>/autodeploy-sandbox`, and every path pointing at sandbox locations on the VM (`/srv/storage/projects/ai-document-assistant`, `/srv/docker/ai-document-assistant/compose.yaml`, …). No production secrets: the sandbox `.env` holds dummy values only. |
| "SSD" | A loop device: `truncate -s 1G /var/tmp/ssd.img`, `mkfs.ext4`, mount on `/srv/storage` by UUID. Write a root-owned `/srv/docker/apps/check-storage.sh` that compares `findmnt -no UUID /srv/storage` with the expected UUID. |
| Bystanders | Three containers named `hesham-postgres` (`postgres:17`), `hesham-redis` (`redis:7`) and `hesham-portfolio` (`nginx`) with throwaway data, in the same Compose file or a separate one. Record `docker inspect --format '{{.Id}} {{.State.StartedAt}} {{.RestartCount}}'` for each before every scenario. |

## 2. Sandbox application

- `backend/Dockerfile`: a tiny Python HTTP server with
  `HEALTHCHECK CMD python -c "…urlopen('http://127.0.0.1:8000/health')…"`.
  The response comes from a file in the repository (`backend/health_status`),
  so a commit can make the health check fail.
- Add `backend/migrations/README` and `backend/alembic.ini` placeholders, so
  migration detection can be triggered by path.
- `frontend/Dockerfile`: the same pattern, on port 3000.
- `compose.yaml` (outside the repository, at the production path):
  services `backend` and `frontend` with `container_name: ai-backend` /
  `ai-frontend`, `build.context` under the checkout, `restart: unless-stopped`,
  and `env_file: .env` with dummy values.
- The checkout is owned by an ordinary user (for example `uid 1000`), not root.

## 3. Install

Follow `README.md` "Install" exactly, then `init --commit <sandbox HEAD>`.
Record `stat -c '%U:%G' -R`-style ownership of the checkout
(`find <checkout> -printf '%u:%g %p\n' | sort > before.txt`).

## 4. Scenarios

For every scenario, record the commands, exit codes, journal lines and
`deployctl status`. Then confirm the **invariants**:

- I1 the bystander containers' IDs, `StartedAt` and `RestartCount` are unchanged;
- I2 checkout ownership is unchanged (`find … -printf` diff is empty);
- I3 the history file has an entry for the outcome;
- I4 nothing from `.env` appears in the journal (`journalctl … | grep -F <dummy secret>` is empty).

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| S1 | Sandbox hardening | `systemctl start ai-document-assistant-deploy-doctor.service` | All PASS (WARN only for "no previous release"); proves capabilities, `ProtectSystem` and git-as-owner work |
| S2 | Checker isolation | `sudo -u ai-deploy-check docker ps`; `systemd-run -p User=ai-deploy-check … ls /run/docker.sock` inside the checker sandbox; `systemd-analyze security ai-document-assistant-check.service` | Permission denied / inaccessible; exposure score recorded |
| S3 | Happy path | Push a harmless change; wait | Checker publishes candidate after CI success; executor deploys; both containers on new image IDs and healthy; `previous` recorded |
| S4 | CI pending / failed / missing | Push with a slow CI job; push with `ci-should-fail`; push with `[skip ci]` | No candidate, no deploy, history notes once |
| S5 | Newer commit during CI | Push A, then B before A's CI ends | A is never deployed; B deploys after its CI |
| S6 | Migration gate | Change `backend/migrations/README` | `awaiting_approval`; nothing changes; after `approve --database-backup-taken` and a start, it deploys |
| S7 | Tooling gate | Change `ops/deploy/README.md` | `awaiting_approval` |
| S8 | SSD missing | `umount /srv/storage` (the loop device), then trigger | Refuses with storage failure; no change. Remount afterwards. |
| S9 | Dirty / wrong-owner checkout | Create an untracked file; separately `chmod g+w` the installed script | Refuses; no change |
| S10 | Unhealthy release | Commit `backend/health_status=fail` | Backend recreated, unhealthy, automatically restored to the exact previous image ID (`docker inspect --format '{{.Image}}'`), checkout back, `rolled_back` |
| S11 | Frontend fails after backend succeeded | Make only the frontend unhealthy | Both restored |
| S12 | Rollback that fails | As S10, plus make the previous image fail too (for example retag a broken image onto the rollback tag beforehand) | Exit 2, journal `rollback_failed`, later runs refuse; fix, then `recover --abort` |
| S13 | Kill during build | Start a deploy of a slow build; `systemctl kill --signal=KILL ai-document-assistant-deploy.service` mid-build | Journal phase `build`; runs refuse; `recover --abort` restores checkout; containers never changed |
| S14 | Kill during replacement | Kill right after `ai-backend` is recreated (poll `docker inspect`) | Phase `replace:ai-frontend`; `recover --status` shows split; test both `recover --complete` and (after a reset of the VM snapshot) `recover --abort` |
| S15 | Power loss | `echo b > /proc/sysrq-trigger` mid-deploy (VM only) | After boot: refuse with phase advice; recovery as S13/S14 |
| S16 | External change | `docker compose up -d --force-recreate backend` by hand after a rebuild | Next run refuses ("no longer run the recorded image"); `doctor` shows FAIL; `recover --adopt --commit <HEAD>` clears it |
| S17 | Manual rollback | `deployctl rollback --dry-run`, then `rollback` | Both on previous IDs; bad commit skipped by the next run |
| S18 | Migration rollback with a dead backend | Approve a release whose backend crashes on start (exit 1) and whose `backend/migrations/README` changed | `failed_migrations`, no automatic rollback; follow README "Rollback across a migration" (the alembic steps are replaced by no-ops in the sandbox), then `recover --abort --database-downgraded` |
| S19 | GitHub unreachable / rate limited | Block `api.github.com` with `nftables` on the VM; separately exhaust the anonymous limit from the VM's IP | Checker and executor exit 1; no change |
| S20 | Lock | Start the executor twice concurrently (`systemctl start` + `deployctl run`) | Second exits 75 |
| S21 | Forged candidate | As root, write a `candidate.json` owned by another user, a symlink, or a SHA that failed CI | Executor refuses or re-verifies and does not deploy |

## 5. Teardown

Delete the VM and the sandbox GitHub repository. Nothing from this plan
touches the production server, its database, its `.env` or the real repository.
