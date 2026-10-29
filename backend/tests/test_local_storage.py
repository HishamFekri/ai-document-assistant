"""STORAGE_BACKEND=local: originals and extracted images on a persistent volume, offline.

Run: python -B tests/test_local_storage.py
Uses the blocked-network/DB import harness; every filesystem write goes to a
temporary UPLOADS_ROOT.
"""

import base64
from contextlib import ExitStack, contextmanager
import hashlib
import importlib
import io
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import test_resource_admission as harness


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jM1sAAAAASUVORK5CYII=")
JPEG = b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF\x00" + b"synthetic-jpeg" * 4
GIF = b"GIF89a" + b"\x01\x00\x01\x00" + b"synthetic-gif"
WEBP = b"RIFF\x20\x00\x00\x00WEBPVP8 " + b"synthetic-webp"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>'
HTML = b"<!doctype html><script>alert(1)</script>"
PRODUCTION = {
    "ENVIRONMENT": "production", "FRONTEND_URL": "https://frontend.example.test",
    "FRONTEND_URLS": "https://frontend.example.test", "TASK_QUEUE": "background",
    "DATABASE_URL": "postgresql+psycopg://test:synthetic@127.0.0.1/app",
    "RESOURCE_REDIS_URL": "redis://127.0.0.1/2",
    "JWT_SECRET_KEY": "synthetic-long-deployment-secret-" * 2,
}


def encode(data):
    return base64.b64encode(data).decode()


def can_symlink():
    with tempfile.TemporaryDirectory() as directory:
        try:
            os.symlink(directory, Path(directory) / "link", target_is_directory=True)
        except (OSError, NotImplementedError):
            return False
    return True


SYMLINKS = can_symlink()
needs_symlinks = unittest.skipUnless(SYMLINKS, "symlink creation is not permitted here")


def link_directory(target, link):
    """A symlink, or an unprivileged junction on Windows (also a directory link)."""
    if SYMLINKS:
        os.symlink(target, link, target_is_directory=True)
    else:
        import _winapi
        _winapi.CreateJunction(str(target), str(link))


def unlink_directory(link):
    os.rmdir(link) if os.name == "nt" else os.unlink(link)


needs_directory_links = unittest.skipUnless(SYMLINKS or os.name == "nt", "directory links unavailable")


class LocalStorageTests(unittest.TestCase):
    __test__ = __name__ == "__main__"

    @classmethod
    def setUpClass(cls):
        harness.ResourceAdmissionTests.setUpClass.__func__(cls)
        cls.settings = importlib.import_module("app.services.storage_settings")
        cls.local = importlib.import_module("app.services.assets.local_image_storage")
        cls.original = importlib.import_module("app.services.original_storage")
        cls.datalab = importlib.import_module("app.services.datalab_service")
        cls.hybrid = importlib.import_module("app.services.hybrid_pdf_service")
        cls.delivery = importlib.import_module("app.services.assets.image_delivery")
        cls.errors = importlib.import_module("app.services.document_processing_errors")
        cls.runtime = importlib.import_module("app.services.runtime_config")
        cls.ready = importlib.import_module("app.services.readiness")
        cls.auth_config = importlib.import_module("app.services.auth_config")

    def setUp(self):
        harness.ResourceAdmissionTests.setUp(self)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve() / "uploads"
        self.root.mkdir()
        self.stack.enter_context(patch.dict(os.environ, {"STORAGE_BACKEND": "local", "UPLOADS_ROOT": str(self.root)}))
        self.stack.enter_context(patch.object(self.delivery, "ASSET_ROOT", self.root / "assets"))
        self.cloud_upload = self.stack.enter_context(patch.object(self.datalab.cloudinary.uploader, "upload"))
        self.ready._cached, self.ready._expires = None, 0

    def production(self, sentinel=True, **values):
        self.stack.enter_context(patch.dict(os.environ, {**PRODUCTION, **values}))
        self.limits.resource_limits.cache_clear()
        self.auth_config.auth_settings.cache_clear()
        self.addCleanup(self.auth_config.auth_settings.cache_clear)
        if sentinel:
            (self.root / ".storage-sentinel").write_text("mounted")

    def directory(self, document_id):
        return self.root / "assets" / f"document_{document_id}"

    def files(self, document_id):
        directory = self.directory(document_id)
        return sorted(path.name for path in directory.iterdir()) if directory.exists() else []

    def save(self, images, document_id=7, output_directory=None, **kwargs):
        with patch.object(self.datalab, "log_event"), patch.object(self.datalab, "log_exception"):
            return self.datalab.save_datalab_images(
                images, output_directory or Path(tempfile.gettempdir()) / "assets" / f"document_{document_id}" / "batch_1",
                document_id=document_id, **kwargs,
            )

    # Settings -----------------------------------------------------------------

    def test_cloudinary_is_the_default_and_keeps_production_originals_shared(self):
        with patch.dict(os.environ, {"ENVIRONMENT": "production"}):
            os.environ.pop("STORAGE_BACKEND")
            self.assertEqual(self.settings.storage_backend(), "cloudinary")
            self.assertTrue(self.original.uses_shared_original_storage())
            os.environ["STORAGE_BACKEND"] = "local"
            self.assertFalse(self.original.uses_shared_original_storage())
        with patch.dict(os.environ, {"ENVIRONMENT": "development", "STORAGE_BACKEND": "cloudinary"}):
            self.assertFalse(self.original.uses_shared_original_storage())

    def test_invalid_backend_and_root_values_are_rejected(self):
        for value in ("s3", "Local ", "cloudinary,local"):
            with self.subTest(value=value), patch.dict(os.environ, {"STORAGE_BACKEND": value}):
                if value.strip().lower() == "local":
                    self.assertEqual(self.settings.storage_backend(), "local")
                else:
                    with self.assertRaises(ValueError):
                        self.settings.storage_backend()
        with patch.dict(os.environ, {"UPLOADS_ROOT": ""}):
            self.assertEqual(self.settings.uploads_root(), Path("uploads"))
        os.environ.pop("UPLOADS_ROOT")
        self.assertEqual(self.settings.uploads_root(), Path("uploads"))

    def test_document_directory_requires_a_positive_integer_id(self):
        self.assertEqual(self.settings.document_asset_directory(7), self.root / "assets" / "document_7")
        for value in (0, -1, True, "7", "../7", None, 7.0):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.settings.document_asset_directory(value)

    # Production validation ----------------------------------------------------

    def test_production_local_validates_root_sentinel_and_writes(self):
        self.production(sentinel=False)
        with self.assertRaisesRegex(self.settings.LocalStorageUnavailable, "sentinel"):
            self.settings.validate_local_storage_root()
        (self.root / ".storage-sentinel").mkdir()
        with self.assertRaisesRegex(self.settings.LocalStorageUnavailable, "regular file"):
            self.settings.validate_local_storage_root()
        (self.root / ".storage-sentinel").rmdir()
        (self.root / ".storage-sentinel").write_text("mounted")
        self.assertEqual(self.settings.validate_local_storage_root(), self.root)
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), [".storage-sentinel"])
        with patch.object(self.settings.tempfile, "mkstemp", side_effect=PermissionError("read-only")), \
                self.assertRaisesRegex(self.settings.LocalStorageUnavailable, "not writable"):
            self.settings.validate_local_storage_root()
        with patch.dict(os.environ, {"UPLOADS_ROOT": str(self.root / "missing")}), \
                self.assertRaisesRegex(self.settings.LocalStorageUnavailable, "missing"):
            self.settings.validate_local_storage_root()

    @needs_symlinks
    def test_symlinked_sentinel_is_not_proof_of_a_mount(self):
        self.production(sentinel=False)
        elsewhere = self.root.parent / "elsewhere"
        elsewhere.write_text("x")
        os.symlink(elsewhere, self.root / ".storage-sentinel")
        with self.assertRaises(self.settings.LocalStorageUnavailable):
            self.settings.validate_local_storage_root()

    def test_runtime_local_mode_does_not_require_cloudinary(self):
        self.production(CLOUDINARY_URL="")
        self.runtime.validate_runtime()
        (self.root / ".storage-sentinel").unlink()
        with self.assertRaises(self.settings.LocalStorageUnavailable):
            self.runtime.validate_runtime()

    def test_runtime_rejects_local_mode_on_render_and_keeps_cloudinary_requirement(self):
        self.production(RENDER="true")
        with self.assertRaisesRegex(ValueError, "Render"):
            self.runtime.validate_runtime()
        with patch.dict(os.environ, {"RENDER": "", "STORAGE_BACKEND": "cloudinary", "CLOUDINARY_URL": ""}), \
                self.assertRaisesRegex(ValueError, "CLOUDINARY_URL"):
            self.runtime.validate_runtime()
        with patch.dict(os.environ, {"RENDER": "", "STORAGE_BACKEND": "bogus"}), self.assertRaises(ValueError):
            self.runtime.validate_runtime()

    def test_readiness_reports_an_unmounted_volume(self):
        self.production()
        with patch.object(self.ready, "check_database"), patch.object(self.ready, "check_redis"), \
                patch.object(self.ready, "log_exception"):
            self.assertEqual(self.ready.readiness_status()[0]["checks"]["storage"], "ok")
            self.ready._cached = None
            (self.root / ".storage-sentinel").unlink()
            body, status = self.ready.readiness_status()
        self.assertEqual((body["checks"]["storage"], status), ("unavailable", 503))

    def test_readiness_has_no_storage_probe_for_cloudinary(self):
        self.production(STORAGE_BACKEND="cloudinary")
        with patch.object(self.ready, "check_database"), patch.object(self.ready, "check_redis"):
            self.assertNotIn("storage", self.ready.readiness_status()[0]["checks"])

    def test_import_needs_cloudinary_url_only_for_cloudinary_backend(self):
        backend = Path(__file__).resolve().parents[1]
        environment = {key: value for key, value in os.environ.items() if key.upper() in {"PATH", "SYSTEMROOT", "TEMP", "TMP"}}
        environment.update(PYTHON_DOTENV_DISABLED="1", ENVIRONMENT="test", UPLOADS_ROOT=str(self.root),
                           DATALAB_API_KEY="offline")
        script = ("import app.services.datalab_service, app.services.assets.image_delivery as d;"
                  "print(d.ASSET_ROOT.as_posix())")
        local = subprocess.run([sys.executable, "-B", "-c", script], cwd=backend, capture_output=True, text=True,
                               env={**environment, "STORAGE_BACKEND": "local"}, timeout=60)
        self.assertEqual(local.returncode, 0, local.stderr)
        self.assertEqual(local.stdout.strip(), (self.root / "assets").as_posix())
        cloud = subprocess.run([sys.executable, "-B", "-c", script], cwd=backend, capture_output=True, text=True,
                               env=environment, timeout=60)
        self.assertNotEqual(cloud.returncode, 0)
        self.assertIn("CLOUDINARY_URL is not set", cloud.stderr)

    # Originals ----------------------------------------------------------------

    def test_local_originals_are_stored_under_the_root_in_production(self):
        self.production()
        stored = self.original.store_original(io.BytesIO(b"hello"), ".txt", 5, 100, self.root)
        self.assertIsNone(stored.storage_key)
        self.assertEqual(Path(stored.file_path).parent, self.root)
        self.assertEqual(Path(stored.file_path).read_bytes(), b"hello")
        self.assertEqual(stored.file_sha256, hashlib.sha256(b"hello").hexdigest())

    def test_unmounted_volume_fails_original_upload_without_writing(self):
        self.production(sentinel=False)
        with patch("cloudinary.uploader.upload_large") as upload, \
                self.assertRaises(self.settings.LocalStorageUnavailable):
            self.original.store_original(io.BytesIO(b"hello"), ".txt", 5, 100, self.root)
        upload.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    # Image validation and storage ---------------------------------------------

    def test_supported_formats_are_detected_by_content_not_name(self):
        for data, extension in ((PNG, ".png"), (JPEG, ".jpg"), (GIF, ".gif"), (WEBP, ".webp")):
            with self.subTest(extension=extension):
                self.assertEqual(self.local.detect_image_extension(data), extension)
        for data in (SVG, HTML, b"", b"\x89PNG\r\n\x1a\n" + b"\x00" * 8, b"RIFF\x00\x00\x00\x00WAVE", b"GIF90a"):
            self.assertIsNone(self.local.detect_image_extension(data))

    def test_link_detection_is_portable(self):
        is_link = self.local._is_link
        self.assertTrue(is_link(SimpleNamespace(st_mode=stat.S_IFLNK | 0o777)))
        # POSIX stat results have no st_reparse_tag attribute at all.
        self.assertFalse(is_link(SimpleNamespace(st_mode=stat.S_IFDIR | 0o750)))
        self.assertFalse(is_link(SimpleNamespace(st_mode=stat.S_IFREG | 0o640)))
        self.assertFalse(is_link(os.lstat(__file__)))
        # Non-link reparse points (e.g. OneDrive cloud files) are not links.
        self.assertFalse(is_link(SimpleNamespace(st_mode=stat.S_IFREG, st_reparse_tag=0x9000001A)))
        for name in ("IO_REPARSE_TAG_SYMLINK", "IO_REPARSE_TAG_MOUNT_POINT"):
            if hasattr(stat, name):
                with self.subTest(tag=name):
                    self.assertTrue(is_link(SimpleNamespace(st_mode=stat.S_IFDIR, st_reparse_tag=getattr(stat, name))))
        self.assertEqual(len(self.local.WINDOWS_LINK_REPARSE_TAGS), 2 if os.name == "nt" else 0)

    def test_storage_works_without_windows_stat_constants(self):
        # Linux's stat module lacks IO_REPARSE_TAG_*; simulate it on any platform.
        script = """
import os, stat, sys
for name in ("IO_REPARSE_TAG_SYMLINK", "IO_REPARSE_TAG_MOUNT_POINT"):
    if hasattr(stat, name):
        delattr(stat, name)
from app.services.assets import local_image_storage as storage
assert storage.WINDOWS_LINK_REPARSE_TAGS == frozenset()
path = storage.save_local_image(bytes.fromhex(sys.argv[1]), 7)
assert storage.save_local_image(bytes.fromhex(sys.argv[1]), 7) == path
assert storage.delete_local_document_images(7) and not os.path.exists(path)
print("ok")
"""
        backend = Path(__file__).resolve().parents[1]
        environment = {key: value for key, value in os.environ.items() if key.upper() in {"PATH", "SYSTEMROOT", "TEMP", "TMP"}}
        environment.update(PYTHON_DOTENV_DISABLED="1", ENVIRONMENT="test", STORAGE_BACKEND="local",
                           UPLOADS_ROOT=str(self.root))
        result = subprocess.run([sys.executable, "-B", "-c", script, PNG.hex()], cwd=backend, env=environment,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "ok"), result.stderr)

    def test_images_use_content_hash_names_and_validated_extensions(self):
        result = self.save({"../../evil.svg": encode(PNG), "photo.png": encode(JPEG)}, report=(report := {}))
        digest = hashlib.sha256(PNG).hexdigest()[:40]
        expected = (self.directory(7) / f"img_{digest}.png").as_posix()
        self.assertEqual(result["evil.svg"], expected)
        self.assertTrue(result["photo.png"].endswith(".jpg"))
        self.assertEqual(self.files(7), sorted([f"img_{digest}.png",
                                                f"img_{hashlib.sha256(JPEG).hexdigest()[:40]}.jpg"]))
        self.assertEqual(report["missing_images"], 0)
        self.cloud_upload.assert_not_called()

    def test_unsafe_or_oversized_content_is_never_written_and_counts_as_missing(self):
        with patch.object(self.local, "MAX_STORED_IMAGE_BYTES", len(PNG) - 1):
            result = self.save({"a.svg": encode(SVG), "b.png": encode(HTML), "c.png": encode(PNG),
                                "d.png": "not base64!"}, report=(report := {}))
        self.assertEqual(result, {})
        self.assertEqual(report["missing_images"], 4)
        self.assertEqual(self.files(7), [])

    def test_temporary_pdf_location_never_decides_where_images_go(self):
        outside = Path(tempfile.gettempdir()) / "document-original-x" / "assets" / "document_7" / "batch_1"
        result = self.save({"a.png": encode(PNG)}, output_directory=outside)
        self.assertTrue(Path(result["a.png"]).is_relative_to(self.directory(7)))
        self.assertFalse(outside.exists())
        pdf = Path(tempfile.gettempdir()) / "document-original-abc.pdf"
        self.assertEqual(self.hybrid.get_asset_directory(pdf, 7), self.directory(7))

    def test_local_mode_requires_a_document_id(self):
        for value in (None, 0, True, "7"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.save({"a.png": encode(PNG)}, document_id=value)
        self.assertFalse((self.root / "assets").exists())

    def test_duplicates_are_written_once_and_retries_are_idempotent(self):
        cache = {}
        first = self.save({"a.png": encode(PNG), "b.png": encode(PNG)}, upload_cache=cache)
        self.assertEqual(first["a.png"], first["b.png"])
        path = Path(first["a.png"])
        stamp = path.stat().st_mtime_ns
        again = self.save({"c.png": encode(PNG)}, upload_cache={})   # a retried job, fresh cache
        self.assertEqual(again["c.png"], first["a.png"])
        self.assertEqual(path.stat().st_mtime_ns, stamp)
        self.assertEqual(len(self.files(7)), 1)
        self.assertEqual(path.read_bytes(), PNG)

    def test_corrupt_existing_file_is_atomically_replaced(self):
        path = Path(self.save({"a.png": encode(PNG)})["a.png"])
        path.write_bytes(b"truncated")
        self.save({"a.png": encode(PNG)}, upload_cache={})
        self.assertEqual(path.read_bytes(), PNG)
        self.assertEqual(self.files(7), [path.name])   # no leftover temporary files

    def test_failed_write_leaves_no_partial_file(self):
        with patch.object(self.local.os, "replace", side_effect=OSError("disk full")), \
                self.assertRaises(self.errors.RetryableDocumentProcessingError):
            self.save({"a.png": encode(PNG)})
        self.assertEqual(self.files(7), [])

    def test_documents_never_share_image_directories(self):
        seven = self.save({"a.png": encode(PNG)}, document_id=7)["a.png"]
        eight = self.save({"a.png": encode(PNG)}, document_id=8)["a.png"]
        self.assertNotEqual(seven, eight)
        self.assertTrue(Path(eight).is_relative_to(self.directory(8)))

    def test_unmounted_volume_fails_processing_instead_of_missing_images(self):
        self.production(sentinel=False)
        with self.assertRaises(self.errors.RetryableDocumentProcessingError) as caught:
            self.save({"a.png": encode(PNG)}, report={})
        self.assertTrue(self.errors.is_retryable_processing_error(caught.exception))
        self.assertFalse((self.root / "assets").exists())

    @needs_directory_links
    def test_symlinked_document_or_assets_directory_is_never_written_through(self):
        victim = self.root.parent / "victim"
        victim.mkdir()
        (self.root / "assets").mkdir()
        link_directory(victim, self.directory(7))
        with self.assertRaises(self.errors.RetryableDocumentProcessingError):
            self.save({"a.png": encode(PNG)})
        unlink_directory(self.directory(7))
        (self.root / "assets").rmdir()
        link_directory(victim, self.root / "assets")
        with self.assertRaises(self.errors.RetryableDocumentProcessingError):
            self.save({"a.png": encode(PNG)})
        self.assertEqual(list(victim.iterdir()), [])

    @needs_symlinks
    def test_symlink_at_image_target_is_refused(self):
        victim = self.root.parent / "victim.png"
        victim.write_bytes(b"victim")
        self.directory(7).mkdir(parents=True)
        name = f"img_{hashlib.sha256(PNG).hexdigest()[:40]}.png"
        os.symlink(victim, self.directory(7) / name)
        with self.assertRaises(self.errors.RetryableDocumentProcessingError):
            self.save({"a.png": encode(PNG)})
        self.assertEqual(victim.read_bytes(), b"victim")

    # Delivery -----------------------------------------------------------------

    def test_saved_images_are_served_only_within_their_document(self):
        stored = self.save({"a.png": encode(PNG)})["a.png"]
        response = self.delivery.image_file_response(stored, SimpleNamespace(id=7, file_path=None))
        self.assertEqual(Path(response.path).read_bytes(), PNG)
        self.assertEqual(response.media_type, "image/png")
        self.assertEqual(response.headers["Cache-Control"], "private, no-cache")
        for document_id in (8, 70):
            with self.subTest(document_id=document_id), self.assertRaises(self.delivery.HTTPException) as caught:
                self.delivery.image_file_response(stored, SimpleNamespace(id=document_id, file_path=None))
            self.assertEqual(caught.exception.status_code, 400)
        traversal = (self.directory(7) / ".." / "document_8" / "x.png").as_posix()
        with self.assertRaises(self.delivery.HTTPException):
            self.delivery.image_file_response(traversal, SimpleNamespace(id=7, file_path=None))

    def test_existing_cloudinary_images_remain_deliverable_in_local_mode(self):
        url = "https://res.cloudinary.com/synthetic/image/authenticated/v1/ai-document-assistant/document_7/img_a.png"
        with patch.object(self.delivery, "remote_image_response", return_value="proxied") as remote:
            self.assertEqual(self.delivery.image_file_response(url, SimpleNamespace(id=7, file_path=None)), "proxied")
        remote.assert_called_once()

    # Deletion -----------------------------------------------------------------

    def populate(self, *document_ids):
        for document_id in document_ids:
            self.save({"a.png": encode(PNG)}, document_id=document_id)

    def test_delete_removes_exactly_one_document_directory(self):
        self.populate(1, 10, 12, 7)
        (self.root / "assets" / "shared.txt").write_text("keep")
        self.assertTrue(self.local.delete_local_document_images(1))
        self.assertFalse(self.directory(1).exists())
        for document_id in (10, 12, 7):
            self.assertTrue(self.files(document_id))
        self.assertTrue((self.root / "assets" / "shared.txt").exists())
        self.assertFalse(self.local.delete_local_document_images(1))
        self.assertFalse(self.local.delete_local_document_images(99))
        for value in (0, -1, True, "1", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.local.delete_local_document_images(value)
        self.assertTrue((self.root / "assets").is_dir())

    def test_delete_without_any_assets_directory_is_a_no_op(self):
        self.assertFalse(self.local.delete_local_document_images(7))
        self.assertFalse((self.root / "assets").exists())

    @needs_directory_links
    def test_delete_refuses_symlinked_document_directory_and_keeps_its_target(self):
        self.populate(8)
        (self.root / "assets").mkdir(exist_ok=True)
        link_directory(self.directory(8), self.directory(7))
        with self.assertRaises(self.settings.LocalStorageUnavailable):
            self.local.delete_local_document_images(7)
        self.assertTrue(self.files(8))
        with patch.object(self.local, "log_exception") as logged:
            self.local.discard_local_document_images(7)   # logged, never raised
        logged.assert_called_once()
        self.assertTrue(self.files(8))

    @needs_directory_links
    def test_delete_does_not_follow_symlinks_inside_the_document_directory(self):
        self.populate(7, 8)
        link_directory(self.directory(8), self.directory(7) / "linked")
        self.local.delete_local_document_images(7)
        self.assertFalse(os.path.lexists(self.directory(7)))
        self.assertTrue(self.files(8))

    def test_delete_route_removes_original_and_only_that_documents_images(self):
        self.populate(7, 8)
        original = self.root / "original.pdf"
        original.write_bytes(b"%PDF")
        document = SimpleNamespace(id=7, file_path=str(original), storage_key=None)
        with patch.object(self.documents, "get_owned_document", return_value=document):
            body = self.documents.delete_document(document_id=7, db=MagicMock(), current_user=SimpleNamespace(id=1))
        self.assertEqual(body["document_id"], 7)
        self.assertFalse(original.exists())
        self.assertFalse(self.directory(7).exists())
        self.assertTrue(self.files(8))

    def test_delete_route_keeps_succeeding_when_image_cleanup_fails(self):
        self.populate(7)
        document = SimpleNamespace(id=7, file_path=None, storage_key=None)
        with patch.object(self.documents, "get_owned_document", return_value=document), \
                patch.object(self.local.shutil, "rmtree", side_effect=PermissionError("denied")), \
                patch.object(self.local, "log_exception") as logged:
            body = self.documents.delete_document(document_id=7, db=MagicMock(), current_user=SimpleNamespace(id=1))
        self.assertEqual(body["document_id"], 7)
        logged.assert_called_once()

    def test_failed_database_delete_keeps_images(self):
        self.populate(7)
        db = MagicMock()
        db.commit.side_effect = RuntimeError("database unavailable")
        with patch.object(self.documents, "get_owned_document",
                          return_value=SimpleNamespace(id=7, file_path=None, storage_key=None)), \
                self.assertRaises(self.documents.HTTPException):
            self.documents.delete_document(document_id=7, db=db, current_user=SimpleNamespace(id=1))
        self.assertTrue(self.files(7))

    def test_deletion_during_processing_removes_late_images_only(self):
        @contextmanager
        def claimed(document_id):
            yield SimpleNamespace(session=MagicMock())

        deleted = self.errors.DocumentDeletedDuringProcessing
        for case in ("claimed", "failure_state"):
            with self.subTest(case=case):
                self.populate(7, 8)
                patches = [patch.object(self.processing, "claim_document_processing", claimed)]
                if case == "claimed":
                    patches.append(patch.object(self.processing, "process_claimed_document", side_effect=deleted()))
                else:
                    patches += [patch.object(self.processing, "process_claimed_document", side_effect=ValueError("x")),
                                patch.object(self.processing, "processing_document", side_effect=deleted()),
                                patch.object(self.processing, "log_generation_failure")]
                with ExitStack() as scope:
                    scope.enter_context(patch.object(self.processing, "log_event"))
                    for item in patches:
                        scope.enter_context(item)
                    self.assertEqual(self.processing.process_document(7), "deleted")
                self.assertFalse(self.directory(7).exists())
                self.assertTrue(self.files(8))


def test_local_storage_in_isolated_process():
    result = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve())],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == "__main__":
    unittest.main(verbosity=2)
