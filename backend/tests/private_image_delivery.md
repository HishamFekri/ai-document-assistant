# Private document image delivery

This batch protects application delivery and future uploads. It does **not** revoke
previously public Cloudinary URLs, migrate stored records, or modify existing objects.

## Delivery and compatibility

| Reference | Application behavior |
| --- | --- |
| Existing Cloudinary URL in an asset or chunk | Check document ownership, validate the configured Cloudinary account and document folder, then proxy bytes. The stored URL is unchanged. |
| Future extracted image | Upload with `type="authenticated"`, `overwrite=False`. Sign the retrieval URL on the server after authorization. No fallback to public uploads. |
| Asset ID used by summaries | Keep `/documents/{document_id}/assets/{asset_id}/file`, including its asset/document association check. |
| RAG source with a chunk ID | Use `/documents/{document_id}/image-chunks/{chunk_id}/file`, including document association and image-type checks. Also supports chunks predating asset rows. |
| Historical message source | Normalize during response serialization, without changing the stored message. Prefer asset/chunk IDs; retain the filename route as a fallback. Unresolvable origin references are suppressed. |
| Legacy filename route | Keep matching asset metadata and older chunk metadata; proxy remote bytes instead of redirecting. |
| Local image | Resolve the file and enforce containment under `uploads/assets/document_{id}` or the earlier upload-stem directory. Reject cross-document traversal and UNC/device paths. |

Asset response paths and search metadata are normalized as well. The frontend only
fetches application image paths, sends credentials, and rejects redirects. Normal
application logos, profile images, authentication semantics, and summary asset IDs
are unchanged.

## Caching

Every successful image response uses:

```http
Cache-Control: private, no-cache
Vary: Cookie, Authorization, Origin
X-Content-Type-Options: nosniff
```

Browsers may store images but must contact the application before reuse. Shared
caches cannot store these private responses. The `Vary` dimensions distinguish
cookie/bearer credentials and CORS origins. Both image components use `cache:
"no-cache"` so the previous chat-image `force-cache` setting cannot bypass that
check. Existing CDN/browser copies from before this change cannot be recalled.

## Retrieval limits

- Only HTTPS on `res.cloudinary.com`, using the Cloudinary account from the existing
  server configuration and this application's document folder pattern. Custom CDNs,
  transformations, other accounts/documents, query strings and arbitrary hosts fail closed.
- Redirects are rejected. Browser cookies, bearer tokens, implicit `.netrc` credentials,
  and environment HTTP proxies are not forwarded to storage.
- Connect/read timeouts: 5/15 seconds. An additional elapsed-time check runs between
  received chunks; it is not a replacement for the socket read timeout.
- At most 20 MiB per remote image, checked against both Content-Length and actual
  bytes. Reads use 64 KiB chunks and a temporary spool, spilling to disk after 1 MiB.
  The response begins only after the download passes validation. Spools are closed
  on success and failure.
- Allow PNG, JPEG, GIF, WebP, AVIF, BMP and TIFF media types. Reject SVG/HTML, missing
  or invalid types, unexpected content encodings, malformed lengths, empty and
  truncated responses. These are image-delivery limits, not document-upload limits.

## Verification

From `backend`, run the database-free suite:

```powershell
.\venv\Scripts\python.exe -B tests/test_private_images.py
.\venv\Scripts\python.exe -B tests/test_database_safety.py
```

The image suite uses actual route handlers and the authentication dependency with
synthetic tokens, a query double interpreting their equality filters, and mocked
Cloudinary/HTTP calls. It prevents real database engine creation, local dotenv
loading and network connections. It is not a substitute for live PostgreSQL or
Cloudinary integration tests. The pytest entry runs the harness in a subprocess
to preserve Batch 1's database-fixture safeguards.

Cloudinary authenticated delivery is supported by the existing SDK. No account or
credential changes were made, and live account restrictions were not verified.
Before production rollout, confirm a newly created authenticated image can be
retrieved through the owner endpoint and that its unsigned origin URL is denied.
There is no public-upload fallback if the account rejects authenticated uploads.

## Deferred legacy remediation

Anyone who already knows a previously public origin URL may still retrieve that
image directly. Changing application URLs does not revoke those origin objects or
previously cached copies. A later, separately reviewed storage-side batch must
inventory legacy objects, verify account controls and derived/CDN copies, plan
access changes and URL compatibility, and verify old URLs no longer work.

References: [Cloudinary access control](https://cloudinary.com/documentation/control_access_to_media),
[upload API](https://cloudinary.com/documentation/image_upload_api_reference).
