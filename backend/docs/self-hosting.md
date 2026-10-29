# Self-hosting with local storage

`STORAGE_BACKEND=local` keeps original documents and Datalab-extracted images on
a persistent volume instead of Cloudinary. The default, `cloudinary`, is
unchanged, so existing Render/Vercel deployments need no configuration changes.

## Settings

| Variable | Default | Local deployment |
| --- | --- | --- |
| `STORAGE_BACKEND` | `cloudinary` | `local` |
| `UPLOADS_ROOT` | `uploads` (relative to `/app` in the image) | leave unset; mount the volume at `/app/uploads` |
| `CLOUDINARY_URL` | required for `cloudinary` | optional; only needed to serve images stored in Cloudinary before switching |
| `ENVIRONMENT` | `development` | `production` (secure cookies and the checks below) |

Layout under `UPLOADS_ROOT`:

```text
.storage-sentinel                 created by the operator, proves the volume is mounted
<uuid>.<pdf|docx|xlsx|txt>        original documents
assets/document_<id>/img_<sha256 prefix>.<png|jpg|gif|webp>
```

Database rows store these paths as written (relative `uploads/...` by default),
so keep `UPLOADS_ROOT` and the container working directory unchanged after the
first upload.

## Startup and readiness checks

With `ENVIRONMENT=production` (or `staging`) and `STORAGE_BACKEND=local`, the
API and worker refuse to start unless:

- `UPLOADS_ROOT` exists and is a directory;
- `UPLOADS_ROOT/.storage-sentinel` exists and is a regular file (not a symlink);
- a temporary file can be created and removed in `UPLOADS_ROOT`;
- `RENDER` is not set (Render disks are ephemeral).

`/ready` repeats the same check as `checks.storage`. Every original and image
write first checks that the sentinel is still there. If the volume is
unmounted, an upload fails with a 500 error, and processing fails as
retryable. Neither completes with silently missing files.

## Image handling

- The image format comes from the file's signature (PNG, JPEG, GIF or WebP),
  never from the provider's filename. SVG, HTML and unknown content are not
  stored, and those images count as missing.
- Images are limited to 20 MiB each, the same limit used when delivering them.
- Files are named by content hash and written to a temporary file in the same
  directory, then atomically renamed into place. An identical image within one
  document is stored once, and retries reuse it.
- The directory always comes from `UPLOADS_ROOT` plus the document ID, never
  from where the original was read. Symlinked directories or targets are
  refused.
- Images are delivered only through the existing authenticated routes, after
  the document ownership check, with `Cache-Control: private, no-cache`.

## Deletion

Deleting a document removes its original and only `assets/document_<id>/`.
A symlink in that place is refused and left alone, and so is its target. If
the document is deleted while it is still processing, images written after the
delete are removed when processing stops. A cleanup failure is logged and does
not fail the delete request. Images stored in Cloudinary are not removed
(unchanged behavior).

## Host setup (Ubuntu)

```bash
# Mount the SSD persistently by UUID (example; adjust filesystem/options).
# /etc/fstab:
# UUID=<ssd-uuid>  /srv/storage  ext4  defaults,nofail,x-systemd.device-timeout=10s  0 2

sudo mkdir -p /srv/storage/uploads/ai-document-assistant
sudo touch /srv/storage/uploads/ai-document-assistant/.storage-sentinel
sudo chown -R 10001:10001 /srv/storage/uploads/ai-document-assistant
sudo chmod 750 /srv/storage/uploads/ai-document-assistant
```

Create the sentinel **on the mounted SSD**. If the SSD is not mounted, the
directory is an empty placeholder on the root disk, and the backend refuses to
start or write.

For Docker to wait for the mount, add `RequiresMountsFor=/srv/storage` to a
drop-in for `docker.service`.

## Container (example)

```yaml
services:
  backend:
    environment:
      ENVIRONMENT: production
      STORAGE_BACKEND: local
    volumes:
      - /srv/storage/uploads/ai-document-assistant:/app/uploads:rw
    # No published port for files: the reverse proxy forwards only to the API.
```

If you run a Celery worker, it must mount the same volume at the same path.

## Never serve the uploads directory directly

- Do not add a `file_server`, `root` or `handle_path /uploads*` block in Caddy
  for this directory. Proxy only to the frontend and the backend API.
- Do not copy or symlink uploads into the Next.js `public/` directory.
- Do not publish the volume through another container or port.

Every image must go through `/documents/{id}/assets/{asset_id}/file` or
`/documents/{id}/image-chunks/{chunk_id}/file`, which check ownership.

## Backups

Back up `UPLOADS_ROOT` together with the PostgreSQL database. Database rows
reference these files, and an image file without its row (or the reverse) is
unusable.
