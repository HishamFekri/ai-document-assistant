"""Database-free regression suite: python -B tests/test_private_images.py.

Real routers/authentication dependency, in-memory query double, mocked storage.
Does not import the application's database engine or load local environment files.
Integration pytest safeguards in conftest.py are deliberately unchanged.
"""

import base64
import copy
import importlib
import operator
import os
import subprocess
import sys
import unittest
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


class MemoryQuery:
    def __init__(self, rows):
        self.rows = list(rows)

    def filter(self, *conditions):
        for condition in conditions:
            # Interpret actual route predicates, so missing ownership or document
            # association filters cause the cross-user tests to fail.
            if condition.operator is not operator.eq:
                raise AssertionError("Unexpected query predicate")
            self.rows = [row for row in self.rows if getattr(row, condition.left.name) == condition.right.value]
        return self

    def order_by(self, *args):
        return self

    def first(self):
        return next(iter(self.rows), None)

    def all(self):
        return self.rows


class MemorySession:
    def __init__(self, rows):
        self.rows = rows

    def query(self, model):
        return MemoryQuery(self.rows.get(model, []))

    def get(self, model, identifier):
        return next((row for row in self.rows.get(model, []) if row.id == identifier), None)


class PrivateImageTests(unittest.TestCase):
    # pytest runs the subprocess wrapper below. Class-wide module/environment
    # doubles must never overlap the integration suite's real DB fixture teardown.
    __test__ = __name__ == "__main__"

    @classmethod
    def setUpClass(cls):
        cls.stack = ExitStack()
        cls.addClassCleanup(cls.stack.close)
        backend = str(Path(__file__).resolve().parents[1])
        cls.stack.enter_context(patch.object(sys, "path", [backend, *sys.path]))
        # Restore app modules on completion even if run in another test runner.
        cls.stack.enter_context(patch.dict(sys.modules))
        for name in list(sys.modules):
            if name == "app" or name.startswith("app."):
                del sys.modules[name]
        preserved = {key: value for key, value in os.environ.items() if key in {"PATH", "SYSTEMROOT", "TEMP", "TMP"}}
        cls.stack.enter_context(patch.dict(os.environ, {
            **preserved,
            "GOOGLE_CLIENT_ID": "synthetic-client",
            "JWT_SECRET_KEY": "synthetic-test-secret-never-used-in-production",
            "DEEPSEEK_API_KEY": "synthetic-key",
            "DATALAB_API_KEY": "synthetic-key",
            "VOYAGE_API_KEY": "synthetic-key",
            "CLOUDINARY_URL": "cloudinary://synthetic:synthetic@testcloud",
        }, clear=True))
        cls.stack.enter_context(patch("dotenv.load_dotenv", return_value=False))
        cls.stack.enter_context(patch("sqlalchemy.create_engine", side_effect=AssertionError("No database engines in this suite")))
        cls.stack.enter_context(patch("socket.create_connection", side_effect=AssertionError("No network in this suite")))
        cls.stack.enter_context(patch("openai.OpenAI"))

        from sqlalchemy.orm import DeclarativeBase
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        database = ModuleType("app.database.database")
        class Base(DeclarativeBase):
            pass
        database.Base = Base
        def forbidden_db():
            raise AssertionError("No real database sessions in this suite")
        database.get_db = forbidden_db
        database.SessionLocal = forbidden_db
        sys.modules[database.__name__] = database

        cls.models = importlib.import_module("app.database.models")
        cls.assets = importlib.import_module("app.routes.document_assets")
        cls.chats = importlib.import_module("app.routes.chats")
        cls.auth = importlib.import_module("app.routes.auth")
        cls.delivery = importlib.import_module("app.services.assets.image_delivery")
        cls.references = importlib.import_module("app.services.assets.image_references")
        cls.rag = importlib.import_module("app.services.rag_service")
        cls.datalab = importlib.import_module("app.services.datalab_service")
        cls.asset_schema = importlib.import_module("app.schemas.document_asset_schemas")
        cls.stack.enter_context(patch.object(cls.auth, "decode_access_token", side_effect=lambda token: {"sub": token}))
        cls.app = FastAPI()
        # Match production registration order.
        cls.app.include_router(cls.chats.router)
        cls.app.include_router(cls.assets.router)
        cls.TestClient = TestClient
        cls.database = database

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = Path(self.stack.enter_context(TemporaryDirectory())).resolve()
        self.stack.enter_context(patch.object(self.delivery, "ASSET_ROOT", self.directory))
        self.image = self.directory / "document_7" / "batch_1" / "figure.png"
        self.image.parent.mkdir(parents=True)
        self.image_bytes = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jM1sAAAAASUVORK5CYII=")
        self.image.write_bytes(self.image_bytes)
        self.document = SimpleNamespace(id=7, user_id=1, file_path="uploads/original.pdf", filename="original.pdf", processing_status="ready")
        self.other_document = SimpleNamespace(id=8, user_id=2, file_path="uploads/other.pdf")
        self.asset = SimpleNamespace(
            id=11, document_id=7, asset_type="image", file_path=str(self.image),
            asset_metadata={"asset_filename": "figure.png", "asset_path": str(self.image)},
            location="Page 1", title="Figure", caption=None, content=None, created_at=datetime(2026, 1, 1),
        )
        self.chunk = SimpleNamespace(id=21, document_id=7, content_type="image", chunk_metadata=copy.deepcopy(self.asset.asset_metadata), document=self.document, content="Figure", location="Page 1")
        self.rows = {
            self.models.User: [SimpleNamespace(id=1), SimpleNamespace(id=2)],
            self.models.Document: [self.document, self.other_document],
            self.models.DocumentAsset: [self.asset],
            self.models.DocumentChunk: [self.chunk],
            self.models.Chat: [SimpleNamespace(id=31, user_id=1, documents=[self.document])],
        }
        self.session = MemorySession(self.rows)
        self.app.dependency_overrides[self.database.get_db] = lambda: self.session
        self.addCleanup(self.app.dependency_overrides.clear)
        self.client = self.stack.enter_context(self.TestClient(self.app))
        self.http = self.stack.enter_context(patch.object(self.delivery.requests, "Session"))
        self.http_session = self.http.return_value.__enter__.return_value
        self.remote = self.http_session.get.return_value.__enter__.return_value
        self.remote.status_code = 200
        self.remote.headers = {"Content-Type": "image/png", "Content-Length": str(len(self.image_bytes))}
        self.remote.iter_content.return_value = [self.image_bytes]
        self.upload = self.stack.enter_context(patch.object(self.datalab.cloudinary.uploader, "upload"))
        self.public_url = "https://res.cloudinary.com/testcloud/image/upload/v123/ai-document-assistant/document_7/img_abc.png"

    def get(self, path="/documents/7/assets/11/file", user="1", **kwargs):
        return self.client.get(path, headers={"Authorization": f"Bearer {user}"} if user else {}, **kwargs)

    def use_remote(self, url=None):
        self.asset.file_path = url or self.public_url
        self.asset.asset_metadata["asset_path"] = self.asset.file_path
        self.chunk.chunk_metadata["asset_path"] = self.asset.file_path

    def assert_private(self, response):
        self.assertEqual(response.headers["Cache-Control"], "private, no-cache")
        self.assertEqual(set(response.headers["Vary"].split(", ")), {"Cookie", "Authorization", "Origin"})
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertNotIn("location", response.headers)

    def test_owner_can_read_all_local_image_routes(self):
        for path in ("/documents/7/assets/11/file", "/documents/7/assets/figure.png", "/documents/7/image-chunks/21/file"):
            with self.subTest(path=path):
                response = self.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, self.image_bytes)
                self.assert_private(response)
        self.http.assert_not_called()

    def test_other_user_and_unauthenticated_cannot_read_any_image_route(self):
        self.use_remote()
        for path in ("/documents/7/assets/11/file", "/documents/7/assets/figure.png", "/documents/7/image-chunks/21/file"):
            for user, status in (("2", 404), (None, 401), ("invalid", 401)):
                with self.subTest(path=path, user=user):
                    self.assertEqual(self.get(path, user).status_code, status)
        self.http.assert_not_called()

    def test_cookie_authentication_still_works(self):
        self.client.cookies.set("access_token", "1")
        self.assertEqual(self.get(user=None).status_code, 200)

    def test_missing_and_malformed_identifiers_are_safe(self):
        for path, expected in (
            ("/documents/7/assets/999/file", 404),
            ("/documents/7/assets/not-an-id/file", 422),
            ("/documents/7/assets/-1/file", 404),
            ("/documents/7/assets/missing.png", 404),
            ("/documents/7/image-chunks/999/file", 404),
            ("/documents/7/image-chunks/bad/file", 422),
        ):
            with self.subTest(path=path):
                self.assertEqual(self.get(path).status_code, expected)
        self.http.assert_not_called()

    def test_asset_and_chunk_document_mismatch_is_rejected(self):
        self.use_remote()
        self.asset.document_id = 8
        self.chunk.document_id = 8
        self.assertEqual(self.get().status_code, 404)
        self.assertEqual(self.get("/documents/7/image-chunks/21/file").status_code, 404)
        self.assertEqual(self.get("/documents/7/assets/figure.png").status_code, 404)
        self.http.assert_not_called()

    def test_non_image_assets_and_chunks_are_rejected(self):
        self.asset.asset_type = "table"
        self.chunk.content_type = "text"
        self.assertEqual(self.get().status_code, 400)
        self.assertEqual(self.get("/documents/7/image-chunks/21/file").status_code, 404)

    def test_existing_cloudinary_image_is_proxied_on_every_route(self):
        self.use_remote()
        for path in ("/documents/7/assets/11/file", "/documents/7/assets/figure.png", "/documents/7/image-chunks/21/file"):
            response = self.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, self.image_bytes)
            self.assert_private(response)
        kwargs = self.http_session.get.call_args.kwargs
        self.assertFalse(kwargs["allow_redirects"])
        self.assertTrue(kwargs["stream"])
        self.assertEqual(kwargs["timeout"], (5, 15))
        self.assertEqual(kwargs["headers"], {"Accept-Encoding": "identity"})
        self.assertFalse(self.http_session.trust_env)

    def test_private_cloudinary_image_is_signed_only_for_server_fetch(self):
        for delivery_type in ("authenticated", "private"):
            self.use_remote(self.public_url.replace("/upload/", f"/{delivery_type}/"))
            response = self.get()
            self.assertEqual(response.status_code, 200)
            url = self.http_session.get.call_args.args[0]
            self.assertIn(f"/image/{delivery_type}/s--", url)
            self.assertIn("res.cloudinary.com/testcloud/", url)
            self.assertNotIn(url.encode(), response.content)
            self.assert_private(response)

    def test_untrusted_remote_locations_are_rejected_before_http(self):
        for url in (
            self.public_url.replace("res.cloudinary.com", "127.0.0.1"),
            self.public_url.replace("res.cloudinary.com", "res.cloudinary.com.attacker.test"),
            self.public_url.replace("res.cloudinary.com", "res.cloudinary.com@attacker.test"),
            self.public_url.replace("res.cloudinary.com", "res.cloudinary.com:444"),
            self.public_url.replace("testcloud/", "othercloud/"),
            self.public_url.replace("document_7/", "document_8/"),
            self.public_url.replace("/upload/", "/fetch/"),
            self.public_url.replace("/v123/", "/w_100/v123/"),
            self.public_url.replace("/img_abc", "/../img_abc"),
            self.public_url.replace("/img_abc", "/%2e%2e/img_abc"),
            self.public_url.replace("https:", "http:"),
            self.public_url + "?target=http://localhost",
            self.public_url + "#fragment",
            "https://[broken/", "file:///etc/passwd",
        ):
            with self.subTest(url=url):
                self.use_remote(url)
                self.assertEqual(self.get().status_code, 400)
        self.http.assert_not_called()

    def test_redirects_and_upstream_errors_are_not_forwarded(self):
        self.use_remote()
        for status in (301, 302, 307, 308, 401, 404, 500):
            self.remote.status_code = status
            self.remote.headers["Location"] = "http://127.0.0.1/private"
            response = self.get()
            self.assertEqual(response.status_code, 502)
            self.assertNotIn("location", response.headers)
        self.remote.iter_content.assert_not_called()

    def test_oversized_declared_and_streamed_responses_are_rejected(self):
        self.use_remote()
        with patch.object(self.delivery, "MAX_IMAGE_BYTES", 8):
            self.assertEqual(self.get().status_code, 502)
            self.remote.iter_content.assert_not_called()
            self.remote.headers.pop("Content-Length")
            self.remote.iter_content.return_value = [b"12345", b"67890"]
            response = self.get()
            self.assertEqual(response.status_code, 502)
            self.assertNotIn(b"12345", response.content)

    def test_invalid_media_types_are_rejected(self):
        self.use_remote()
        for media_type in ("text/html", "application/json", "image/svg+xml", "", "application/octet-stream"):
            self.remote.headers["Content-Type"] = media_type
            self.assertEqual(self.get().status_code, 502)
        self.remote.iter_content.assert_not_called()

    def test_malformed_lengths_encoding_and_empty_responses_fail_safely(self):
        self.use_remote()
        for length in ("-1", "bad", "1.0", "9" * 100):
            self.remote.headers["Content-Length"] = length
            self.assertEqual(self.get().status_code, 502)
        self.remote.headers.pop("Content-Length")
        self.remote.headers["Content-Encoding"] = "gzip"
        self.assertEqual(self.get().status_code, 502)
        self.remote.headers.pop("Content-Encoding")
        self.remote.iter_content.return_value = []
        self.assertEqual(self.get().status_code, 502)

    def test_truncated_response_and_read_timeout_fail_before_sending_image(self):
        self.use_remote()
        self.remote.headers["Content-Length"] = str(len(self.image_bytes) + 1)
        self.assertEqual(self.get().status_code, 502)
        self.remote.iter_content.side_effect = self.delivery.requests.Timeout("synthetic internal URL")
        response = self.get()
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("synthetic internal URL", response.text)

    def test_large_images_spool_to_disk_and_close(self):
        self.use_remote()
        real_spool = self.delivery.SpooledTemporaryFile
        spools = []
        def create_spool(**kwargs):
            result = real_spool(**kwargs)
            spools.append(result)
            return result
        with patch.object(self.delivery, "SPOOL_MEMORY_BYTES", 8), patch.object(self.delivery, "SpooledTemporaryFile", side_effect=create_spool):
            self.assertEqual(self.get().content, self.image_bytes)
        self.assertTrue(spools[0]._rolled)
        self.assertTrue(spools[0].closed)

    def test_failed_remote_response_closes_spool(self):
        self.use_remote()
        spool = self.delivery.SpooledTemporaryFile()
        with patch.object(self.delivery, "SpooledTemporaryFile", return_value=spool):
            self.remote.status_code = 302
            self.assertEqual(self.get().status_code, 502)
        self.assertTrue(spool.closed)

    def test_local_traversal_sibling_directories_and_unc_are_rejected(self):
        outside = self.directory / "secret.png"
        outside.write_bytes(self.image_bytes)
        for path in (
            str(outside), str(self.image.parent / ".." / ".." / "secret.png"),
            str(self.directory / "document_8" / "figure.png"),
            str(self.directory / "document_7_evil" / "figure.png"),
            "\\\\server\\share\\secret.png", "//server/share/secret.png",
            str(self.image) + ":secret.png", "bad\x00.png",
        ):
            with self.subTest(path=path):
                self.asset.file_path = path
                self.assertEqual(self.get().status_code, 400)

    def test_resolved_local_path_cannot_escape_through_symlink(self):
        outside = self.directory / "outside.png"
        outside.write_bytes(self.image_bytes)
        link = self.image.parent / "linked.png"
        # Model the filesystem's resolved target without requiring Windows
        # administrator/developer-mode privileges to create a real symlink.
        resolve = Path.resolve
        def resolved(path, *args, **kwargs):
            return outside if path == link else resolve(path, *args, **kwargs)
        self.asset.file_path = str(link)
        with patch.object(Path, "resolve", resolved):
            self.assertEqual(self.get().status_code, 400)

    def test_legacy_filename_path_traversal_is_rejected(self):
        for name in ("..%5Csecret.png", "..%2Fsecret.png", "figure.png:secret", "%00.png"):
            self.assertIn(self.get(f"/documents/7/assets/{name}").status_code, (400, 404))
        self.http.assert_not_called()

    def test_legacy_upload_stem_directory_remains_supported(self):
        legacy = self.directory / "original" / "figure.png"
        legacy.parent.mkdir()
        legacy.write_bytes(self.image_bytes)
        self.asset.file_path = str(legacy)
        self.assertEqual(self.get().content, self.image_bytes)

    def test_chunk_only_legacy_file_remains_supported(self):
        self.rows[self.models.DocumentAsset] = []
        response = self.get("/documents/7/assets/figure.png")
        self.assertEqual(response.content, self.image_bytes)
        self.assert_private(response)
        self.use_remote()
        self.assertEqual(self.get("/documents/7/assets/figure.png").content, self.image_bytes)

    def test_legacy_lookup_skips_a_missing_local_chunk_file(self):
        self.rows[self.models.DocumentAsset] = []
        missing = copy.copy(self.chunk)
        missing.chunk_metadata = {
            "asset_filename": "figure.png",
            "asset_path": str(self.image.parent / "missing.png"),
        }
        self.rows[self.models.DocumentChunk] = [missing, self.chunk]
        self.assertEqual(self.get("/documents/7/assets/figure.png").content, self.image_bytes)

    def test_metadata_path_fallback_remains_supported(self):
        self.asset.file_path = None
        self.assertEqual(self.get().content, self.image_bytes)

    def test_missing_local_file_is_404(self):
        self.asset.file_path = str(self.image.parent / "missing.png")
        self.assertEqual(self.get().status_code, 404)

    def test_asset_metadata_serialization_hides_origins_without_mutation(self):
        self.use_remote()
        self.asset.asset_metadata["nested"] = {"image_url": self.public_url, "html": f'<img src="{self.public_url}">'}
        before = copy.deepcopy(self.asset.__dict__)
        response = self.get("/documents/7/assets")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("res.cloudinary.com", response.text)
        self.assertEqual(response.json()[0]["file_path"], "/documents/7/assets/11/file")
        self.assertEqual(self.asset.__dict__, before)
        self.assertEqual(self.get("/documents/7/assets", user="2").status_code, 404)

    def test_table_asset_serialization_is_unchanged(self):
        self.asset.asset_type = "table"
        self.asset.file_path = None
        self.asset.asset_metadata = {"rows": [["A", "B"]]}
        result = self.asset_schema.DocumentAssetResponse.model_validate(self.asset).model_dump()
        self.assertIsNone(result["file_path"])
        self.assertEqual(result["asset_metadata"], self.asset.asset_metadata)

    def test_historical_message_sources_are_normalized_without_db_writes(self):
        raw_sources = [{"document_id": 7, "chunk_id": 21, "content_type": "image", "asset_filename": "figure.png", "asset_url": self.public_url}]
        message = SimpleNamespace(id=41, chat_id=31, role="assistant", content="Answer", status="completed", error=None, sources=raw_sources, documents=[], created_at=datetime(2026, 1, 1))
        self.rows[self.models.Message] = [message]
        before = copy.deepcopy(raw_sources)
        response = self.get("/chats/31/messages")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["sources"][0]["asset_url"], "/documents/7/image-chunks/21/file")
        self.assertNotIn("res.cloudinary.com", response.text)
        self.assertEqual(message.sources, before)

    def test_new_rag_sources_reference_chunk_images(self):
        self.use_remote()
        sources = self.rag.build_sources([{"chunk": self.chunk, "similarity": 0.9}])
        self.assertEqual(sources[0]["asset_url"], "/documents/7/image-chunks/21/file")
        self.assertNotIn("res.cloudinary.com", str(sources))

    def test_search_metadata_normalization(self):
        self.use_remote()
        before = copy.deepcopy(self.chunk.chunk_metadata)
        with patch.object(self.chats, "search_similar_chunks", return_value=[{"chunk": self.chunk, "similarity": 0.9}]):
            response = self.client.post("/chats/31/search", json={"query": "figure"}, headers={"Authorization": "Bearer 1"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("res.cloudinary.com", response.text)
        self.assertEqual(response.json()[0]["metadata"]["asset_path"], "/documents/7/image-chunks/21/file")
        self.assertEqual(self.chunk.chunk_metadata, before)

    def test_old_source_without_chunk_id_uses_legacy_filename(self):
        source = {"document_id": 7, "content_type": "image", "asset_url": self.public_url}
        normalized = self.references.normalize_image_source(source)
        self.assertEqual(normalized["asset_url"], "/documents/7/assets/img_abc.png")
        self.use_remote()
        self.assertEqual(self.get(normalized["asset_url"]).status_code, 200)

    def test_unresolvable_historical_origin_is_not_returned(self):
        normalized = self.references.normalize_image_source({"asset_url": self.public_url})
        self.assertIsNone(normalized["asset_url"])
        normalized = self.references.normalize_image_source({"document_id": 7, "content_type": "image", "asset_url": None})
        self.assertIsNone(normalized["asset_url"])

    def test_future_upload_uses_authenticated_type_without_overwrite(self):
        protected_url = self.public_url.replace("/upload/", "/authenticated/")
        self.upload.return_value = {"secure_url": protected_url}
        result = self.datalab.save_datalab_images(
            {"figure.png": base64.b64encode(self.image_bytes).decode()},
            self.image.parent,
        )
        self.assertEqual(result, {"figure.png": protected_url})
        kwargs = self.upload.call_args.kwargs
        self.assertEqual(kwargs["type"], "authenticated")
        self.assertFalse(kwargs["overwrite"])
        self.assertEqual(kwargs["resource_type"], "image")
        self.assertEqual(kwargs["folder"], "ai-document-assistant/document_7")

    def test_upload_failure_never_falls_back_to_public_upload(self):
        self.upload.side_effect = RuntimeError("synthetic failure")
        with self.assertLogs(self.datalab.logger, level="ERROR"):
            result = self.datalab.save_datalab_images(
                {"figure.png": base64.b64encode(self.image_bytes).decode()}, self.image.parent,
            )
        self.assertEqual(result, {})
        self.upload.assert_called_once()
        self.assertEqual(self.upload.call_args.kwargs["type"], "authenticated")


def test_private_images_in_isolated_process():
    """Also runnable by the normal, safely configured integration test suite."""
    result = subprocess.run(
        [sys.executable, "-B", str(Path(__file__).resolve())],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == "__main__":
    unittest.main(verbosity=2)
