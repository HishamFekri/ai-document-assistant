"""Parallel, deduplicated Datalab image uploads (Phase 2 P2), offline.

Run: python -B tests/test_image_uploads.py
Cloudinary is replaced by an in-memory fake; no network.
"""

import base64
import hashlib
import importlib
import os
import random
import sys
import threading
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch


FOLDER_DIR = Path("/tmp/document_7/batch_1")   # folder name only; nothing is written


def encode(data):
    return base64.b64encode(data).decode()


def image(seed):
    return f"synthetic-image-{seed}".encode() * 8


class FakeCloudinary:
    """Thread-safe upload double tracking calls and peak concurrency."""

    def __init__(self, delay=0.0, delays=None, fail=lambda digest, attempt: False, missing_url=()):
        self.delay, self.delays, self.fail, self.missing_url = delay, delays or {}, fail, set(missing_url)
        self.lock = threading.Lock()
        self.calls, self.in_flight, self.peak, self.attempts = [], 0, 0, {}

    def upload(self, file, folder=None, public_id=None, **options):
        data = file.read()
        digest = hashlib.sha256(data).hexdigest()
        with self.lock:
            self.calls.append(public_id)
            attempt = self.attempts[digest] = self.attempts.get(digest, 0) + 1
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
        try:
            time.sleep(self.delays.get(digest, self.delay))
            if self.fail(digest, attempt):
                raise RuntimeError("synthetic upload failure")
            if digest in self.missing_url:
                return {}
            return {"secure_url": f"https://res.cloudinary.com/demo/image/authenticated/v1/{folder}/{public_id}.png"}
        finally:
            with self.lock:
                self.in_flight -= 1


def legacy_save(images, output_directory, upload):
    """Reference copy of the pre-P2 sequential algorithm (one upload per image)."""
    folder = "ai-document-assistant/" + (Path(output_directory).parent.name or "document")
    saved = {}
    for filename, encoded in images.items():
        if not encoded or not Path(filename).name:
            continue
        try:
            data = base64.b64decode(encoded, validate=True)
        except Exception:
            continue
        public_id = f"img_{hashlib.sha256(data).hexdigest()[:40]}"
        try:
            result = upload(__import__("io").BytesIO(data), folder=folder, public_id=public_id)
        except Exception:
            continue
        if result.get("secure_url"):
            saved[Path(filename).name] = str(result["secure_url"])
    return saved


class ImageUploadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stack = ExitStack()
        cls.stack.enter_context(patch.dict(os.environ, {
            "PYTHON_DOTENV_DISABLED": "1", "ENVIRONMENT": "test",
            "DATALAB_API_KEY": "offline", "CLOUDINARY_URL": "cloudinary://key:secret@demo",
        }))
        os.environ.pop("DATALAB_IMAGE_UPLOAD_CONCURRENCY", None)
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
        cls.datalab = importlib.import_module("app.services.datalab_service")

    @classmethod
    def tearDownClass(cls):
        cls.stack.close()

    def save(self, cloud, images, **kwargs):
        with patch.object(self.datalab.cloudinary.uploader, "upload", cloud.upload), \
                patch.object(self.datalab, "log_exception"), patch.object(self.datalab, "log_event"):
            return self.datalab.save_datalab_images(images, FOLDER_DIR, **kwargs)

    def test_default_concurrency_is_bounded_at_four(self):
        cloud = FakeCloudinary(delay=0.05)
        images = {f"figure_{index}.png": encode(image(index)) for index in range(12)}
        result = self.save(cloud, images)
        self.assertEqual(self.datalab.IMAGE_UPLOAD_CONCURRENCY, 4)
        self.assertEqual(cloud.peak, 4)
        self.assertEqual(len(cloud.calls), 12)
        self.assertEqual(list(result), list(images))

    def test_concurrency_is_configurable_and_validated(self):
        cloud = FakeCloudinary(delay=0.05)
        with patch.object(self.datalab, "IMAGE_UPLOAD_CONCURRENCY", 2):
            self.save(cloud, {f"f{index}.png": encode(image(index)) for index in range(6)})
        self.assertEqual(cloud.peak, 2)
        for bad in ("0", "17"):
            with self.subTest(value=bad), patch.dict(os.environ, {"DATALAB_IMAGE_UPLOAD_CONCURRENCY": bad}):
                with self.assertRaises(RuntimeError):
                    importlib.reload(self.datalab)
        importlib.reload(self.datalab)   # restore the default module state

    def test_identical_images_upload_once_and_keep_every_reference(self):
        cloud = FakeCloudinary()
        logo = encode(image("logo"))
        images = {"p1_logo.png": logo, "p1_chart.png": encode(image("chart")),
                  "p2_logo.png": logo, "p3_logo.png": logo, "p3_table.png": encode(image("table"))}
        result = self.save(cloud, images)
        self.assertEqual(len(cloud.calls), 3)
        self.assertEqual(list(result), list(images))
        self.assertEqual(len({result["p1_logo.png"], result["p2_logo.png"], result["p3_logo.png"]}), 1)
        self.assertNotEqual(result["p1_logo.png"], result["p1_chart.png"])

    def test_output_order_is_independent_of_completion_order(self):
        names = [f"figure_{index}.png" for index in range(8)]
        images = {name: encode(image(index)) for index, name in enumerate(names)}
        # Earlier images finish last.
        delays = {hashlib.sha256(image(index)).hexdigest(): 0.02 * (8 - index) for index in range(8)}
        result = self.save(FakeCloudinary(delays=delays), images)
        self.assertEqual(list(result), names)
        reference = legacy_save(images, FOLDER_DIR, FakeCloudinary().upload)
        self.assertEqual(result, reference)
        self.assertEqual(list(result.items()), list(reference.items()))

    def test_failed_upload_is_skipped_and_its_duplicate_retried(self):
        broken = hashlib.sha256(image("logo")).hexdigest()
        cloud = FakeCloudinary(fail=lambda digest, attempt: digest == broken and attempt == 1)
        logo = encode(image("logo"))
        images = {"a.png": logo, "b.png": encode(image("b")), "c.png": logo, "d.png": logo}
        result = self.save(cloud, images)
        # As before: the first copy failed, the next copy was attempted and succeeded.
        self.assertEqual(list(result), ["b.png", "c.png", "d.png"])
        self.assertEqual(cloud.attempts[broken], 2)
        self.assertEqual(len(cloud.calls), 3)

    def test_permanently_failing_and_url_less_uploads_are_skipped(self):
        broken = hashlib.sha256(image("broken")).hexdigest()
        url_less = hashlib.sha256(image("no-url")).hexdigest()
        cloud = FakeCloudinary(fail=lambda digest, attempt: digest == broken, missing_url=[url_less])
        images = {"ok.png": encode(image("ok")), "broken.png": encode(image("broken")),
                  "broken_copy.png": encode(image("broken")), "no_url.png": encode(image("no-url"))}
        result = self.save(cloud, images)
        self.assertEqual(list(result), ["ok.png"])
        self.assertEqual(cloud.attempts[broken], 2)   # each copy tried once, as before

    def test_skips_empty_unsafe_and_undecodable_entries_without_uploading(self):
        cloud = FakeCloudinary()
        # "/" has an empty basename. (Pre-existing: a name like "../" keeps basename "..",
        # which was and is accepted; names are only result keys, the public_id is a hash.)
        images = {"empty.png": "", "/": encode(image(1)), "bad.png": "not base64!!", "good.png": encode(image(2))}
        result = self.save(cloud, images)
        self.assertEqual(list(result), ["good.png"])
        self.assertEqual(len(cloud.calls), 1)

    def test_cache_reuses_uploads_across_batches_of_one_job_only(self):
        cloud = FakeCloudinary()
        cache = {}
        first = self.save(cloud, {"a.png": encode(image("logo"))}, upload_cache=cache)
        second = self.save(cloud, {"b.png": encode(image("logo")), "c.png": encode(image("new"))}, upload_cache=cache)
        self.assertEqual(len(cloud.calls), 2)
        self.assertEqual(first["a.png"], second["b.png"])
        # Another document's job has its own cache and folder.
        other = FakeCloudinary()
        with patch.object(self.datalab.cloudinary.uploader, "upload", other.upload):
            self.datalab.save_datalab_images({"a.png": encode(image("logo"))}, Path("/tmp/document_8/batch_1"), upload_cache={})
        self.assertEqual(len(other.calls), 1)

    def test_failed_uploads_are_never_cached(self):
        cache = {}
        always = FakeCloudinary(fail=lambda digest, attempt: True)
        self.save(always, {"a.png": encode(image("x"))}, upload_cache=cache)
        self.assertEqual(cache, {})
        retry = FakeCloudinary()
        self.assertEqual(list(self.save(retry, {"b.png": encode(image("x"))}, upload_cache=cache)), ["b.png"])
        self.assertEqual(len(retry.calls), 1)

    def test_no_worker_threads_outlive_the_call(self):
        before = threading.active_count()
        self.save(FakeCloudinary(delay=0.02), {f"f{index}.png": encode(image(index)) for index in range(9)})
        self.assertEqual(threading.active_count(), before)

    def test_matches_previous_algorithm_on_random_inputs(self):
        rng = random.Random(20261025)
        for case in range(200):
            pool = [image(f"{case}-{index}") for index in range(rng.randint(1, 5))]
            images = {}
            for index in range(rng.randint(0, 10)):
                kind = rng.random()
                if kind < 0.08:
                    images[f"bad_{index}.png"] = "%%%"
                elif kind < 0.12:
                    images[f"{index}/"] = encode(rng.choice(pool))
                else:
                    images[f"{rng.choice(['a', 'b'])}/fig_{index}.png"] = encode(rng.choice(pool))
            failing = {hashlib.sha256(data).hexdigest(): rng.randint(0, 2) for data in pool}
            # Realistic failure model: the first k attempts for an image fail, then it exists.
            model = lambda digest, attempt, failing=failing: attempt <= failing.get(digest, 0)
            with self.subTest(case=case):
                expected = legacy_save(images, FOLDER_DIR, FakeCloudinary(fail=model).upload)
                actual = self.save(FakeCloudinary(fail=model), images)
                self.assertEqual(list(actual.items()), list(expected.items()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
