# Production rollback runbook

Covers the release that adds `chats.last_activity_at` (revision `4b8d2e6f1a93`,
parent `8e4a1d7c3f25`) and any later rollback past it. Read the warning first.

> **WARNING — never roll back the backend code while the database is at a revision
> the older code does not know.** The container starts with
> `python -m alembic upgrade head && exec python runtime.py`. Older code has no
> migration file for `4b8d2e6f1a93`, so its start fails with
> `Can't locate revision identified by '4b8d2e6f1a93'` and the service does not
> boot. Render's rollback reuses the old build *and its start command*, so a
> dashboard or API rollback of the backend fails the same way. **Downgrade the
> database first, using the newer release**, then roll back the code.

## Facts this runbook relies on

| | Verified locally |
| --- | --- |
| Previous backend + new schema (deploy overlap) | Works: create, stream and list return 200; its sends do not update activity |
| New frontend + previous backend | Works: ordering falls back to creation time; no `chat_activity` event |
| Previous frontend + new backend | Unknown stream events are ignored by the previous client (code inspection) |
| Previous backend started on a database at `4b8d2e6f1a93` | **Fails to start** (Alembic cannot locate the revision) |
| Downgrade to `8e4a1d7c3f25`, then previous backend | Starts and serves requests |
| Downgrade data loss | Only `chats.last_activity_at`; a later upgrade re-derives it from messages |

Rehearsed end to end on disposable PostgreSQL 18 databases, including a
`pg_dump`/`pg_restore` drill; see "Rehearsal" at the end.

## Before every deploy that includes a migration

1. Take a backup and note the time.
   - Render paid Postgres: confirm the latest automatic backup or point-in-time
     recovery window in the database's **Recovery** page.
   - Any plan: from a trusted machine, with the **external** connection string
     kept out of shell history and never committed:
     ```sh
     pg_dump --format=custom --no-owner --file=backup-$(date +%Y%m%d-%H%M).dump "$DATABASE_URL"
     ```
2. Record the current revision (Render Shell, or the trusted machine with the
   repository at the deployed commit):
   ```sh
   python -m alembic current      # expect 8e4a1d7c3f25 before this release
   ```
3. Record the deploy IDs you would roll back to: the current Render deploy and the
   current Vercel production deployment.

## Choose the smallest rollback

| Symptom | Action |
| --- | --- |
| UI problem only; API healthy | **A. Frontend-only rollback** (no database step) |
| Backend problem unrelated to the new column | Prefer a forward fix; otherwise **B** |
| Migration failed or was interrupted during deploy | **C. Failed migration recovery** (usually no rollback needed) |
| Data damaged | **D. Restore from backup** |

The new backend is compatible with the previous frontend, so A never requires B.

## A. Frontend-only rollback (Vercel)

Safe with either backend. No database step.

- Dashboard: project overview → Production Deployment → **Instant Rollback** →
  choose the previous deployment → **Confirm Rollback**.
- CLI:
  ```sh
  vercel rollback                       # previous production deployment
  vercel rollback <deployment-url-or-id>
  vercel rollback status
  ```
- Afterwards Vercel stops auto-assigning production domains, so new pushes do not
  go live. When the fix is deployed, undo it:
  ```sh
  vercel promote <deployment-url-or-id>
  ```

## B. Backend rollback (Render) — database first

1. **Stop new deploys from undoing the rollback**: in the Render service settings,
   turn auto-deploy off (the dashboard rollback also does this; the API does not).
2. **Downgrade the database with the release that is currently deployed** (it has
   the `4b8d2e6f1a93` migration file):
   - Render paid instance — Shell tab, or an ephemeral instance that does not
     serve traffic:
     ```sh
     render ssh <service-id> --ephemeral
     python -m alembic downgrade 8e4a1d7c3f25
     python -m alembic current           # must print 8e4a1d7c3f25
     ```
   - Free instance (no Shell or one-off jobs): from a trusted machine, check out the
     **currently deployed** commit, install `backend/requirements.txt`, and run the
     same two commands with `DATABASE_URL` set to the database's external
     connection string for this command only. Do not use an older checkout: it
     cannot downgrade a revision it does not know.

   The downgrade drops the activity index concurrently, then the column with a
   bounded lock wait. If it reports `lock timeout`, a long transaction is holding
   the table: see "Find what is holding a lock" in C and retry.
3. **Roll back the code**:
   - Dashboard: service → **Events** → previous successful deploy → **Rollback**.
   - API:
     ```sh
     curl -X POST "https://api.render.com/v1/services/<service-id>/rollback" \
       -H "Authorization: Bearer $RENDER_API_KEY" -H "Content-Type: application/json" \
       -d '{"deployId": "<previous-deploy-id>"}'
     ```
   A rollback is only possible while that deploy's build artifact is retained.
4. **Verify**: `GET /health` and `GET /ready` return 200, sign-in works, the chat
   list loads, a message streams. The frontend can stay on the new release.
5. **Return to normal**: deploy the fix, which re-applies `4b8d2e6f1a93` at start
   (activity is re-derived from messages), then turn auto-deploy back on.

## C. Failed or interrupted migration during a deploy

The migration runs before the server listens. If it fails, the new instance never
becomes healthy and the previous instance keeps serving (assumption: Render keeps
the live instance until the new one passes its health check). The database stays
at `8e4a1d7c3f25`, which the running previous release understands. **No rollback is
needed**; fix the cause and redeploy.

What the logs show:

| Log | State | Action |
| --- | --- | --- |
| `canceling statement due to lock timeout` with no `Building … did not complete` | The column step waited more than `MIGRATION_LOCK_TIMEOUT` (5s) and rolled back. Nothing changed. | Find the blocking transaction (below), then redeploy |
| `Building ix_chats_user_archive_pin_activity_id did not complete` | The column committed; the index build waited more than `MIGRATION_INDEX_LOCK_TIMEOUT` (60s); its invalid index was dropped. Revision unchanged. | Redeploy; the column step is idempotent |
| `relation "ix_chats_user_archive_pin_activity_id" already exists` | A **valid** index of that name exists that this migration did not create | Inspect it (below); drop it only if it is a leftover of this change, then redeploy |
| Process killed mid-build (no log) | An invalid index may remain | Redeploy: invalid leftovers are dropped automatically |

Find what is holding a lock:

```sql
SELECT pid, state, now() - xact_start AS in_transaction, wait_event_type, left(query, 80) AS query
FROM pg_stat_activity
WHERE datname = current_database() AND xact_start IS NOT NULL
ORDER BY xact_start;
```

Inspect the index:

```sql
SELECT c.relname, i.indisvalid, pg_get_indexdef(i.indexrelid)
FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
WHERE c.relname = 'ix_chats_user_archive_pin_activity_id';
```

The two limits can be raised for one deploy through environment variables
(`MIGRATION_LOCK_TIMEOUT`, `MIGRATION_INDEX_LOCK_TIMEOUT`; values such as `10s` or
`500ms`, validated). Prefer removing the long transaction over raising them: a
waiting `ALTER TABLE` blocks every chat query queued behind it.

## D. Restore from backup

Last resort for damaged data; it loses everything written after the backup.

1. Put the app in a safe state: roll back the frontend (A) or pause the backend.
2. Restore into a **new** database (never over the live one):
   - Render: Recovery page → restore to a new database from a backup or point in time.
   - From a dump:
     ```sh
     createdb <new-database>          # with the vector extension available
     pg_restore --no-owner --dbname "$NEW_DATABASE_URL" backup-YYYYMMDD-HHMM.dump
     ```
3. Check `SELECT version_num FROM alembic_version` and row counts against what you
   recorded, then point the backend's `DATABASE_URL` at the restored database.
   Deploy code whose migrations include the restored revision (B's warning applies).

## Rehearsal

Performed on disposable local PostgreSQL 18 databases with test credentials,
using the previous release (`a11fd09`) and this release:

1. Previous release at `8e4a1d7c3f25` served create/stream/list (200).
2. `pg_dump` backup taken; this release upgraded to `4b8d2e6f1a93`.
3. Previous release start command failed: `Can't locate revision identified by '4b8d2e6f1a93'`.
4. This release downgraded to `8e4a1d7c3f25`; the previous release then started and served requests (200).
5. Upgrading again re-derived activity for every chat (0 mismatches).
6. The backup restored into a new database at `8e4a1d7c3f25` with its rows.
7. Lock contention: the column step failed after about 6 s with `lock timeout`
   and changed nothing; a blocked index build failed, dropped its invalid index,
   and the retry succeeded.
