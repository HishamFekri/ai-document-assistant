"""Parallel, deduplicated, retried Datalab image uploads (Phase 2 P2/P3), offline.

Run: python -B tests/test_image_uploads.py
Cloudinary is replaced by an in-memory fake, or the real SDK is pointed at a
loopback HTTP server; no external network.
"""

import base64
import email.parser
import hashlib
import importlib
import json
import os
import random
import socket
import sys
import threading
import time
import unittest
from contextlib import ExitStack, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


FOLDER_DIR = Path("/tmp/document_7/batch_1")   # folder name only; nothing is written


def encode(data):
    return base64.b64encode(data).decode()


def image(seed):
    return f"synthetic-image-{seed}".encode() * 8


def digest_of(seed):
    return hashlib.sha256(image(seed)).hexdigest()


class FakeCloudinary:
    """Thread-safe upload double tracking calls, options and peak concurrency.

    fail(digest, attempt) returns a falsy value for success, an int HTTP status
    (returned as a JSON error, as the SDK does with return_error=True), an
    exception instance to raise, or True for a generic (permanent) exception.
    """

    def __init__(self, delay=0.0, delays=None, fail=lambda digest, attempt: None, missing_url=()):
        self.delay, self.delays, self.fail, self.missing_url = delay, delays or {}, fail, set(missing_url)
        self.lock = threading.Lock()
        self.calls, self.options, self.in_flight, self.peak, self.attempts = [], [], 0, 0, {}

    def upload(self, file, folder=None, public_id=None, **options):
        data = file.read()
        digest = hashlib.sha256(data).hexdigest()
        with self.lock:
            self.calls.append(public_id)
            self.options.append(options)
            attempt = self.attempts[digest] = self.attempts.get(digest, 0) + 1
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
        try:
            time.sleep(self.delays.get(digest, self.delay))
            outcome = self.fail(digest, attempt)
            if outcome is True:
                raise RuntimeError("synthetic upload failure")
            if isinstance(outcome, BaseException):
                raise outcome
            if isinstance(outcome, int):
                return {"error": {"message": "synthetic", "http_code": outcome}}
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


def chained(error, cause):
    """An SDK Error raised while handling `cause`, exactly as uploader.call_api does."""
    try:
        try:
            raise cause
        except Exception:
            raise error
    except Exception as raised:
        return raised


class ImageUploadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stack = ExitStack()
        cls.stack.enter_context(patch.dict(os.environ, {
            "PYTHON_DOTENV_DISABLED": "1", "ENVIRONMENT": "test",
            "DATALAB_API_KEY": "offline", "CLOUDINARY_URL": "cloudinary://key:secret@demo",
        }))
        for name in ("DATALAB_IMAGE_UPLOAD_CONCURRENCY", "DATALAB_IMAGE_UPLOAD_TIMEOUT_SECONDS",
                     "DATALAB_IMAGE_UPLOAD_CONNECT_TIMEOUT_SECONDS", "DATALAB_IMAGE_UPLOAD_MAX_ATTEMPTS"):
            os.environ.pop(name, None)
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
        cls.datalab = importlib.import_module("app.services.datalab_service")
        cls.exceptions = importlib.import_module("cloudinary.exceptions")
        cls.urllib3 = importlib.import_module("urllib3.exceptions")
        cls.urllib3_util = importlib.import_module("urllib3.util")

    @classmethod
    def tearDownClass(cls):
        cls.stack.close()

    def save(self, cloud, images, backoff=0.0, events=None, **kwargs):
        log_event = self.datalab.log_event
        def record(target, level, event, **fields):
            if events is not None:
                events.append((event, fields))
        with patch.object(self.datalab.cloudinary.uploader, "upload", cloud.upload), \
                patch.object(self.datalab, "image_upload_backoff", lambda attempt: backoff), \
                patch.object(self.datalab, "log_exception"), \
                patch.object(self.datalab, "log_event", side_effect=record):
            return self.datalab.save_datalab_images(images, FOLDER_DIR, **kwargs)

    # Concurrency, ordering and deduplication (P2) -------------------------------

    def test_default_concurrency_is_bounded_at_four(self):
        cloud = FakeCloudinary(delay=0.05)
        images = {f"figure_{index}.png": encode(image(index)) for index in range(12)}
        result = self.save(cloud, images)
        self.assertEqual(self.datalab.IMAGE_UPLOAD_CONCURRENCY, 4)
        self.assertLessEqual(cloud.peak, 4)
        self.assertGreater(cloud.peak, 1)               # uploads do overlap
        self.assertEqual(len(cloud.calls), 12)
        self.assertEqual(list(result), list(images))

    def test_concurrency_bound_holds_while_retries_back_off(self):
        # Every image fails twice with 503 and then succeeds; backoff sleeps in the worker.
        cloud = FakeCloudinary(delay=0.01, fail=lambda digest, attempt: 503 if attempt <= 2 else None)
        images = {f"figure_{index}.png": encode(image(index)) for index in range(10)}
        result = self.save(cloud, images, backoff=0.02)
        self.assertLessEqual(cloud.peak, 4)
        self.assertEqual(len(cloud.calls), 30)
        self.assertEqual(list(result), list(images))

    def test_concurrency_is_configurable_and_validated(self):
        cloud = FakeCloudinary(delay=0.05)
        with patch.object(self.datalab, "IMAGE_UPLOAD_CONCURRENCY", 2):
            self.save(cloud, {f"f{index}.png": encode(image(index)) for index in range(6)})
        self.assertLessEqual(cloud.peak, 2)
        cases = [("DATALAB_IMAGE_UPLOAD_CONCURRENCY", "0"), ("DATALAB_IMAGE_UPLOAD_CONCURRENCY", "17"),
                 ("DATALAB_IMAGE_UPLOAD_TIMEOUT_SECONDS", "0"), ("DATALAB_IMAGE_UPLOAD_TIMEOUT_SECONDS", "301"),
                 ("DATALAB_IMAGE_UPLOAD_CONNECT_TIMEOUT_SECONDS", "0"),
                 ("DATALAB_IMAGE_UPLOAD_CONNECT_TIMEOUT_SECONDS", "61"),
                 ("DATALAB_IMAGE_UPLOAD_MAX_ATTEMPTS", "0"), ("DATALAB_IMAGE_UPLOAD_MAX_ATTEMPTS", "7")]
        try:
            for name, bad in cases:
                with self.subTest(name=name, value=bad), patch.dict(os.environ, {name: bad}):
                    with self.assertRaises(RuntimeError):
                        importlib.reload(self.datalab)
            with patch.dict(os.environ, {"DATALAB_IMAGE_UPLOAD_TIMEOUT_SECONDS": "12.5",
                                         "DATALAB_IMAGE_UPLOAD_MAX_ATTEMPTS": "5"}):
                importlib.reload(self.datalab)
                self.assertEqual((self.datalab.IMAGE_UPLOAD_TIMEOUT_SECONDS,
                                  self.datalab.IMAGE_UPLOAD_MAX_ATTEMPTS), (12.5, 5))
        finally:
            importlib.reload(self.datalab)   # restore the default module state
        self.assertEqual((self.datalab.IMAGE_UPLOAD_TIMEOUT_SECONDS, self.datalab.IMAGE_UPLOAD_MAX_ATTEMPTS), (30.0, 3))

    def test_every_upload_has_a_finite_timeout_and_reports_errors_with_status(self):
        cloud = FakeCloudinary()
        self.save(cloud, {"a.png": encode(image("a"))})
        options = cloud.options[0]
        self.assertIsInstance(options["timeout"], self.urllib3_util.Timeout)
        self.assertEqual((options["timeout"].connect_timeout, options["timeout"].read_timeout), (5.0, 30.0))
        self.assertIsNone(options["timeout"].total)
        self.assertIs(options["return_error"], True)
        self.assertEqual((options["type"], options["overwrite"], options["resource_type"]),
                         ("authenticated", False, "image"))

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
        delays = {digest_of(index): 0.02 * (8 - index) for index in range(8)}
        result = self.save(FakeCloudinary(delays=delays), images)
        self.assertEqual(list(result), names)
        reference = legacy_save(images, FOLDER_DIR, FakeCloudinary().upload)
        self.assertEqual(list(result.items()), list(reference.items()))

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

    # Failure classification and retries (P3) -----------------------------------

    def test_failure_classification(self):
        exceptions, urllib3 = self.exceptions, self.urllib3
        rejected = self.datalab.CloudinaryUploadRejected
        pool = object()
        cases = {
            "rate_limited": [rejected(420), rejected(429), exceptions.RateLimited("x")],
            "server_error": [rejected(500), rejected(502), rejected(503), rejected(504), exceptions.GeneralError("x")],
            "timeout": [chained(exceptions.Error("x"), urllib3.ReadTimeoutError(pool, "/", "read timed out")),
                        chained(exceptions.Error("x"), socket.timeout("timed out")),
                        chained(exceptions.Error("x"), urllib3.MaxRetryError(
                            pool, "/", urllib3.ConnectTimeoutError(pool, "connect timed out")))],
            "network": [chained(exceptions.Error("x"), urllib3.ProtocolError("reset")),
                        chained(exceptions.Error("x"), ConnectionResetError()),
                        chained(exceptions.Error("x"), urllib3.MaxRetryError(
                            pool, "/", urllib3.NewConnectionError(pool, "refused")))],
            "invalid_response": [chained(exceptions.Error("x"), json.JSONDecodeError("bad", "<html>", 0))],
        }
        for category, errors in cases.items():
            for error in errors:
                with self.subTest(error=repr(error)):
                    self.assertEqual(self.datalab.upload_failure_category(error), (category, True))
        permanent = [rejected(400), rejected(401), rejected(403), rejected(404), rejected(409), rejected(0),
                     exceptions.BadRequest("x"), exceptions.AuthorizationRequired("x"), exceptions.NotAllowed("x"),
                     exceptions.NotFound("x"), exceptions.AlreadyExists("x"), exceptions.Error("unchained"),
                     ValueError("Must supply api_key"), RuntimeError("x")]
        for error in permanent:
            with self.subTest(error=repr(error)):
                self.assertEqual(self.datalab.upload_failure_category(error), ("rejected", False))

    def test_transient_failures_are_retried_and_recover(self):
        for status in (420, 429, 500, 503):
            with self.subTest(status=status):
                broken = digest_of("logo")
                cloud = FakeCloudinary(fail=lambda digest, attempt: status if digest == broken and attempt <= 2 else None)
                logo = encode(image("logo"))
                images = {"a.png": logo, "b.png": encode(image("b")), "c.png": logo}
                events = []
                result = self.save(cloud, images, events=events)
                self.assertEqual(list(result), ["a.png", "b.png", "c.png"])
                self.assertEqual(cloud.attempts[broken], 3)
                summary = dict(events)["datalab_images_uploaded"]
                self.assertEqual((summary["failed"], summary["retry"], summary["count"]), (0, 2, 2))
                self.assertNotIn("datalab_images_upload_incomplete", dict(events))

    def test_permanent_failures_are_not_retried(self):
        rejected = [400, 401, 403, 404, True, self.exceptions.AuthorizationRequired("bad credentials")]
        for outcome in rejected:
            with self.subTest(outcome=outcome):
                cloud = FakeCloudinary(fail=lambda digest, attempt: outcome if digest == digest_of("bad") else None)
                images = {"ok.png": encode(image("ok")), "bad.png": encode(image("bad")),
                          "bad_copy.png": encode(image("bad"))}
                events = []
                result = self.save(cloud, images, events=events)
                self.assertEqual(list(result), ["ok.png"])
                self.assertEqual(cloud.attempts[digest_of("bad")], 1)   # copies are not re-attempted
                self.assertEqual(dict(events)["datalab_images_uploaded"]["failed"], 1)

    def test_retry_exhaustion_is_bounded_and_visible(self):
        broken = digest_of("broken")
        cloud = FakeCloudinary(fail=lambda digest, attempt: 503 if digest == broken else None)
        images = {"ok.png": encode(image("ok")), "broken.png": encode(image("broken")),
                  "broken_copy.png": encode(image("broken"))}
        events = []
        result = self.save(cloud, images, events=events)
        self.assertEqual(list(result), ["ok.png"])
        self.assertEqual(cloud.attempts[broken], 3)                 # IMAGE_UPLOAD_MAX_ATTEMPTS, once per distinct image
        recorded = dict(events)
        self.assertEqual(recorded["datalab_images_uploaded"]["failed"], 1)
        # One distinct failure, two missing occurrences (the image and its copy).
        self.assertEqual(recorded["datalab_images_upload_incomplete"], {"failed": 1, "missing": 2, "images": 1})
        retries = [fields for event, fields in events if event == "datalab_image_upload_retry"]
        self.assertEqual(retries, [{"failure_category": "server_error", "retry": 1},
                                   {"failure_category": "server_error", "retry": 2}])

    def test_exhaustion_is_logged_with_category_and_no_message_or_url(self):
        cloud = FakeCloudinary(fail=lambda digest, attempt: 429)
        with patch.object(self.datalab.cloudinary.uploader, "upload", cloud.upload), \
                patch.object(self.datalab, "image_upload_backoff", lambda attempt: 0), \
                patch.object(self.datalab, "log_exception") as failure:
            self.datalab.save_datalab_images({"a.png": encode(image("a"))}, FOLDER_DIR)
        failure.assert_called_once()
        args, fields = failure.call_args
        self.assertEqual(args[1], "datalab_image_upload")
        self.assertIsInstance(args[2], self.datalab.CloudinaryUploadRejected)
        self.assertEqual(args[2].status_code, 429)
        self.assertEqual(fields, {"failure_category": "rate_limited", "retry": 2})

    def test_after_one_image_exhausts_retries_the_rest_of_the_batch_gets_one_attempt(self):
        # A Cloudinary outage must not cost max_attempts x timeout for every image.
        cloud = FakeCloudinary(fail=lambda digest, attempt: 503)
        images = {f"f{index}.png": encode(image(index)) for index in range(6)}
        with patch.object(self.datalab, "IMAGE_UPLOAD_CONCURRENCY", 1):
            result = self.save(cloud, images)
        self.assertEqual(result, {})
        self.assertEqual(sorted(cloud.attempts.values()), [1, 1, 1, 1, 1, 3])
        # The next batch starts fresh.
        recovered = FakeCloudinary(fail=lambda digest, attempt: 503 if attempt == 1 else None)
        self.assertEqual(len(self.save(recovered, images)), 6)

    def test_url_less_success_is_skipped_without_retry(self):
        url_less = digest_of("no-url")
        cloud = FakeCloudinary(missing_url=[url_less])
        events = []
        result = self.save(cloud, {"ok.png": encode(image("ok")), "no_url.png": encode(image("no-url"))}, events=events)
        self.assertEqual(list(result), ["ok.png"])
        self.assertEqual(cloud.attempts[url_less], 1)
        self.assertEqual(dict(events)["datalab_images_uploaded"]["failed"], 1)

    def test_missing_occurrences_are_counted_across_batches_and_duplicates(self):
        logo = encode(image("logo"))
        cloud = FakeCloudinary(fail=lambda digest, attempt: 400 if digest == digest_of("logo") else None)
        cache, report = {}, {}
        first = self.save(cloud, {"p1_logo.png": logo, "p1_fig.png": encode(image("f1")), "p2_logo.png": logo},
                          upload_cache=cache, report=report)
        self.assertEqual(report, {"missing_images": 2})
        second = self.save(cloud, {"p3_logo.png": logo, "p3_fig.png": encode(image("f3"))},
                           upload_cache=cache, report=report)
        self.assertEqual(report, {"missing_images": 3})          # occurrences, not distinct failures
        self.assertEqual((list(first), list(second)), (["p1_fig.png"], ["p3_fig.png"]))

    def test_missing_count_includes_undecodable_and_url_less_but_not_non_images(self):
        cloud = FakeCloudinary(missing_url=[digest_of("no-url")])
        report = {"missing_images": 5}                           # accumulates onto earlier batches
        images = {"empty.png": "", "/": encode(image(1)), "bad.png": "not base64!!",
                  "no_url.png": encode(image("no-url")), "ok.png": encode(image("ok"))}
        self.assertEqual(list(self.save(cloud, images, report=report)), ["ok.png"])
        self.assertEqual(report, {"missing_images": 5 + 2})
        self.assertEqual(self.save(FakeCloudinary(), {"a.png": encode(image("a"))}, report=report), {
            "a.png": "https://res.cloudinary.com/demo/image/authenticated/v1/ai-document-assistant/document_7/"
                     f"img_{digest_of('a')[:40]}.png"})
        self.assertEqual(report, {"missing_images": 7})

    def test_recovered_and_cached_images_are_not_missing(self):
        cache, report = {}, {}
        flaky = FakeCloudinary(fail=lambda digest, attempt: 503 if attempt == 1 else None)
        self.save(flaky, {"a.png": encode(image("a")), "b.png": encode(image("a"))}, upload_cache=cache, report=report)
        self.save(FakeCloudinary(), {"c.png": encode(image("a"))}, upload_cache=cache, report=report)
        self.assertEqual(report, {"missing_images": 0})

    def test_backoff_is_exponential_jittered_and_capped(self):
        rng_state = random.getstate()
        try:
            random.seed(7)
            for attempt, ceiling in ((1, 1.0), (2, 2.0), (3, 4.0), (4, 8.0), (6, 8.0)):
                delays = [self.datalab.image_upload_backoff(attempt) for _ in range(200)]
                self.assertGreaterEqual(min(delays), ceiling / 2)
                self.assertLessEqual(max(delays), ceiling)
                self.assertGreater(len(set(delays)), 100)   # jittered, not synchronised
        finally:
            random.setstate(rng_state)

    def test_matches_previous_algorithm_on_random_inputs(self):
        """Without failures: identical to the pre-P2 algorithm. With transient failures that
        recover within the retry budget: identical to the failure-free result. With permanent
        failures: exactly the failure-free result minus the images that were rejected."""
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
            digests = [hashlib.sha256(data).hexdigest() for data in pool]
            transient = {digest: rng.randint(0, 2) for digest in digests}
            rejected = {digest for digest in digests if rng.random() < 0.2}
            expected = legacy_save(images, FOLDER_DIR, FakeCloudinary().upload)
            with self.subTest(case=case):
                self.assertEqual(list(self.save(FakeCloudinary(), images).items()), list(expected.items()))
                flaky = FakeCloudinary(fail=lambda digest, attempt: 503 if attempt <= transient[digest] else None)
                self.assertEqual(list(self.save(flaky, images).items()), list(expected.items()))
                failing = FakeCloudinary(fail=lambda digest, attempt: 400 if digest in rejected else None)
                kept = [(name, url) for name, url in expected.items()
                        if not any(f"img_{digest[:40]}" in url for digest in rejected)]
                self.assertEqual(list(self.save(failing, images).items()), kept)


class LoopbackCloudinary:
    """A loopback HTTP stand-in for the Upload API, driven by a list of responses.

    Each response is (status, content_type, body) or "stall" (never answers).
    """

    def __init__(self):
        self.responses, self.requests, self.connections = [], [], 0
        self.lock = threading.Lock()
        self.release = threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self):
                super().setup()
                with owner.lock:
                    owner.connections += 1

            def log_message(self, *args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                with owner.lock:
                    owner.requests.append((self.path, self.headers["Content-Type"], body))
                    response = owner.responses.pop(0) if owner.responses else (
                        200, "application/json", json.dumps({"secure_url": "https://res.cloudinary.com/x.png"}))
                if response == "stall":
                    owner.release.wait(10)
                    return
                status, content_type, payload = response
                payload = payload.encode()
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def prefix(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def form(self, index):
        _, content_type, body = self.requests[index]
        message = email.parser.BytesParser().parsebytes(
            b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body)
        fields = {}
        for part in message.get_payload():
            name = part.get_param("name", header="content-disposition")
            fields[name] = part.get_payload(decode=True)
        return fields


class RealSDKUploadTests(unittest.TestCase):
    """The pinned Cloudinary SDK, unpatched, against a loopback server."""

    @classmethod
    def setUpClass(cls):
        cls.stack = ExitStack()
        cls.stack.enter_context(patch.dict(os.environ, {
            "PYTHON_DOTENV_DISABLED": "1", "ENVIRONMENT": "test",
            "DATALAB_API_KEY": "offline", "CLOUDINARY_URL": "cloudinary://key:secret@demo",
        }))
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
        cls.datalab = importlib.import_module("app.services.datalab_service")
        import cloudinary
        cls.cloudinary = cloudinary

    @classmethod
    def tearDownClass(cls):
        cls.stack.close()

    @contextmanager
    def server(self, responses=(), timeout=5.0):
        loopback = LoopbackCloudinary()
        loopback.responses.extend(responses)
        loopback.thread.start()
        config = self.cloudinary.config()
        previous = config.upload_prefix
        try:
            config.upload_prefix = loopback.prefix
            with patch.object(self.datalab, "IMAGE_UPLOAD_TIMEOUT_SECONDS", timeout), \
                    patch.object(self.datalab, "image_upload_backoff", lambda attempt: 0), \
                    patch.object(self.datalab, "log_exception"), patch.object(self.datalab, "log_event"):
                yield loopback
        finally:
            config.upload_prefix = previous
            loopback.release.set()
            loopback.server.shutdown()
            loopback.server.server_close()

    def upload(self, data=b"png-bytes"):
        stopped = threading.Event()
        return self.datalab._upload_image(data, "ai-document-assistant/document_7", "img_abc", stopped)

    def test_success_and_unchanged_signed_form_fields(self):
        with self.server() as loopback:
            self.assertEqual(self.upload(), ("https://res.cloudinary.com/x.png", None, 0))
            # The same SDK call without this change's options, for comparison.
            self.cloudinary.uploader.upload(
                __import__("io").BytesIO(b"png-bytes"), folder="ai-document-assistant/document_7",
                public_id="img_abc", resource_type="image", type="authenticated",
                overwrite=False, unique_filename=False, use_filename=False)
        path, _, _ = loopback.requests[0]
        self.assertEqual(path, "/v1_1/demo/image/upload")
        new, old = loopback.form(0), loopback.form(1)
        for fields in (new, old):
            fields.pop("timestamp"), fields.pop("signature")
        # timeout/return_error are transport options: never sent, never signed.
        self.assertEqual(new, old)
        self.assertEqual(new["type"], b"authenticated")
        self.assertEqual(new["public_id"], b"img_abc")

    def test_rate_limits_and_server_errors_are_retried_then_succeed(self):
        error = lambda status: (status, "application/json", json.dumps({"error": {"message": "limit"}}))
        with self.server([error(420), error(429)]) as loopback:
            self.assertEqual(self.upload(), ("https://res.cloudinary.com/x.png", None, 2))
        with self.server([error(500), error(503)]) as loopback:
            self.assertEqual(self.upload(), ("https://res.cloudinary.com/x.png", None, 2))
        self.assertEqual(len(loopback.requests), 3)

    def test_gateway_html_error_page_is_retried(self):
        with self.server([(502, "text/html", "<html>Bad gateway</html>")]):
            self.assertEqual(self.upload(), ("https://res.cloudinary.com/x.png", None, 1))

    def test_permanent_rejections_are_not_retried(self):
        for status in (400, 401, 403, 404):
            with self.subTest(status=status), self.server([
                    (status, "application/json", json.dumps({"error": {"message": "no"}}))]) as loopback:
                self.assertEqual(self.upload(), (None, "rejected", 0))
                self.assertEqual(len(loopback.requests), 1)

    def test_retry_exhaustion_on_persistent_server_errors(self):
        with self.server([(503, "application/json", json.dumps({"error": {"message": "x"}}))] * 5) as loopback:
            self.assertEqual(self.upload(), (None, "server_error", 2))
        self.assertEqual(len(loopback.requests), 3)

    def test_stalled_server_times_out_and_is_retried(self):
        with self.server(["stall"], timeout=1.0) as loopback:
            started = time.monotonic()
            result = self.upload()
            elapsed = time.monotonic() - started
        self.assertEqual(result, ("https://res.cloudinary.com/x.png", None, 1))
        self.assertLess(elapsed, 8)             # bounded, not indefinite
        self.assertEqual(len(loopback.requests), 2)

    def test_unreachable_host_is_bounded_by_the_connect_timeout(self):
        """The real SDK and urllib3 with connection attempts intercepted: every attempt
        uses the connect timeout, urllib3 adds at most 3 connection retries per
        upload attempt, and the total is bounded."""
        import urllib3.util.connection as connection
        seen = []

        def unreachable(address, timeout=None, *args, **kwargs):
            seen.append(timeout)
            raise socket.timeout("connect timed out")         # as a blackholed SYN would
        with self.server() as loopback, patch.object(connection, "create_connection", unreachable):
            self.assertEqual(self.upload(), (None, "timeout", 2))
        attempts = self.datalab.IMAGE_UPLOAD_MAX_ATTEMPTS
        self.assertEqual(len(seen), 4 * attempts)                 # 1 + 3 urllib3 connect retries, per attempt
        self.assertEqual(set(seen), {self.datalab.IMAGE_UPLOAD_CONNECT_TIMEOUT_SECONDS})
        self.assertEqual(loopback.requests, [])
        # Effective worst case for one image: 4 x 5s x 3 attempts = 60s (plus backoff),
        # instead of 4 x 30s x 3 = 360s with a single 30s timeout.
        self.assertEqual(len(seen) * self.datalab.IMAGE_UPLOAD_CONNECT_TIMEOUT_SECONDS, 60)

    def test_read_timeout_is_not_retried_inside_urllib3(self):
        with self.server(["stall", "stall"], timeout=1.0) as loopback:
            started = time.monotonic()
            with patch.object(self.datalab, "IMAGE_UPLOAD_MAX_ATTEMPTS", 2):
                result = self.upload()
            elapsed = time.monotonic() - started
        self.assertEqual(result, (None, "timeout", 1))
        self.assertEqual(len(loopback.requests), 2)               # exactly one request per attempt
        self.assertGreaterEqual(elapsed, 2 * 1.0 * 0.9)
        self.assertLess(elapsed, 2 * 1.0 + 6)                     # bounded by attempts x read timeout

    def test_refused_connection_is_a_transient_network_failure(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()                           # nothing listens on this port now
        config = self.cloudinary.config()
        previous = config.upload_prefix
        try:
            config.upload_prefix = f"http://127.0.0.1:{port}"
            with patch.object(self.datalab, "image_upload_backoff", lambda attempt: 0), \
                    patch.object(self.datalab, "log_exception"), patch.object(self.datalab, "log_event"):
                self.assertEqual(self.upload(), (None, "network", 2))
        finally:
            config.upload_prefix = previous


if __name__ == "__main__":
    unittest.main(verbosity=2)
