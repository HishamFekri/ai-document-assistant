"""Offline upload-to-Ready benchmark with deterministic output fingerprints.

Exercises the real upload route (TestClient), background-mode processing, pypdf
classification, chunking, Datalab block normalization, asset and chunk
checkpoints and pgvector writes against a DISPOSABLE loopback PostgreSQL.
Only the network edge is simulated: Cloudinary (originals and images),
Datalab (submit/poll) and Voyage (deterministic vectors). Python socket
connections are refused, so no paid provider can be reached; the database is
reached through libpq.

Usage (creates and drops a fresh database named <base>_run_<uuid>):
    python -B scripts/benchmark_processing.py \
        --database-url postgresql://USER@127.0.0.1:55432/bench_test \
        [--latency realistic|zero] [--scale 1.0] [--scenarios small_text,mixed] \
        [--output results.json] [--write-baseline]

Simulated latencies are ASSUMPTIONS (see LATENCY_PROFILES), not measurements of
any provider. Datalab polling uses the application's real poll interval.
"""

import argparse
import base64
import hashlib
import io
import json
import logging
import os
import re
import socket
import struct
import sys
import threading
import time
import zlib
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4


BACKEND_DIR = Path(__file__).resolve().parents[1]
BASELINE_PATH = BACKEND_DIR / "tests" / "fixtures" / "processing_baseline.json"
# Top-level stages are sequential and additive; dotted stages nest inside a parent.
TOP_LEVEL_STAGES = ("queue_wait", "slot_acquisition", "materialize_original", "extraction",
                    "checkpoint_save", "embeddings", "finalize")

LATENCY_PROFILES = {
    # Assumed, round numbers for relative comparison only.
    "realistic": dict(original_upload_base=0.25, original_upload_mbps=20.0,
                      original_download_base=0.15, original_download_mbps=40.0,
                      datalab_submit_base=0.4, datalab_submit_mbps=10.0,
                      datalab_base=4.0, datalab_per_page=1.0,
                      image_upload=0.35, voyage_base=0.25, voyage_per_input=0.002),
    "zero": dict(original_upload_base=0.0, original_upload_mbps=0.0,
                 original_download_base=0.0, original_download_mbps=0.0,
                 datalab_submit_base=0.0, datalab_submit_mbps=0.0,
                 datalab_base=0.0, datalab_per_page=0.0,
                 image_upload=0.0, voyage_base=0.0, voyage_per_input=0.0),
}


@dataclass(frozen=True)
class DocumentSpec:
    label: str
    text_pages: int
    complex_pages: int = 0
    images_per_complex_page: int = 0
    duplicate_images: bool = False


@dataclass(frozen=True)
class Scenario:
    name: str
    documents: tuple
    concurrent: bool = False


SCENARIOS = {
    "small_text": Scenario("small_text", (DocumentSpec("small_text", 3),)),
    "large_text": Scenario("large_text", (DocumentSpec("large_text", 150),)),
    "mixed": Scenario("mixed", (DocumentSpec("mixed", 24, 6, 1),)),
    "image_heavy": Scenario("image_heavy", (DocumentSpec("image_heavy", 4, 40, 2),)),
    "duplicate_images": Scenario("duplicate_images", (DocumentSpec("duplicate_images", 2, 10, 3, True),)),
    "concurrent_queue": Scenario("concurrent_queue", tuple(
        DocumentSpec(f"queued_{index}", 8, 2, 1) for index in range(1, 4)), concurrent=True),
}
# Fast subset used by the determinism test.
DETERMINISM_SCENARIOS = ("small_text", "mixed", "duplicate_images")


# ---- Synthetic inputs -----------------------------------------------------------

WORDS = ("ledger", "revenue", "policy", "quarter", "analysis", "contract", "section", "figure",
         "summary", "delivery", "metric", "forecast", "schedule", "clause", "supplier", "region")


def prose(seed, words):
    return " ".join(WORDS[(seed * 7 + index * 3 + index // 5) % len(WORDS)] for index in range(words))


def png_bytes(seed):
    """A valid 4x4 RGB PNG whose pixels depend on seed."""
    color = bytes(((seed * 53) % 256, (seed * 97) % 256, (seed * 151) % 256))
    raw = b"".join(b"\x00" + color * 4 for _ in range(4))
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def page_layout(spec):
    """Page numbers (1-based) of complex pages, spread through the document."""
    total = spec.text_pages + spec.complex_pages
    if not spec.complex_pages:
        return total, set()
    step = total / spec.complex_pages
    return total, {int(index * step) + 1 for index in range(spec.complex_pages)}


def build_pdf(spec):
    total, complex_pages = page_layout(spec)
    objects = {1: b"<< /Type /Catalog /Pages 2 0 R >>",
               3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"}
    image_data = bytes([200, 30, 30, 30, 200, 30, 30, 30, 200, 240, 240, 240])
    objects[4] = (b"<< /Type /XObject /Subtype /Image /Width 2 /Height 2 /ColorSpace /DeviceRGB "
                  b"/BitsPerComponent 8 /Length %d >>\nstream\n" % len(image_data) + image_data + b"\nendstream")
    kids, next_id = [], 5
    for page in range(1, total + 1):
        if page in complex_pages:
            text = f"Figure {page}"
            content = (b"BT /F1 12 Tf 72 720 Td (" + text.encode() + b") Tj ET\n"
                       b"q 200 0 0 150 72 400 cm /Im1 Do Q")
            resources = b"<< /Font << /F1 3 0 R >> /XObject << /Im1 4 0 R >> >>"
        else:
            lines = [prose(page * 31 + line, 14) for line in range(25)]
            content = b"BT /F1 9 Tf 50 760 Td 11 TL " + b" ".join(
                b"(" + line.encode() + b") '" for line in lines) + b" ET"
            resources = b"<< /Font << /F1 3 0 R >> >>"
        objects[next_id] = b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream"
        objects[next_id + 1] = (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R "
                                b"/Resources %s >>" % (next_id, resources))
        kids.append(next_id + 1)
        next_id += 2
    objects[2] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(b"%d 0 R" % k for k in kids), len(kids))
    out, offsets = io.BytesIO(), {}
    out.write(b"%PDF-1.7\n")
    for number in sorted(objects):
        offsets[number] = out.tell()
        out.write(b"%d 0 obj\n" % number + objects[number] + b"\nendobj\n")
    xref, size = out.tell(), max(objects) + 1
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % size)
    for number in range(1, size):
        out.write(b"%010d 00000 n \n" % offsets[number])
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, xref))
    return out.getvalue()


def deterministic_vector(text):
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [((digest[index % 32] + index * 7) % 251 + 1) / 256 for index in range(512)]


# ---- Simulated network edge -----------------------------------------------------

class FakeResponse:
    def __init__(self, status, payload=None, body=None, headers=None):
        from requests.structures import CaseInsensitiveDict
        self.status_code = status
        self._body = body if body is not None else json.dumps(payload).encode("utf-8")
        self.headers = CaseInsensitiveDict(headers or {})
        self.headers.setdefault("Content-Length", str(len(self._body)))

    @property
    def ok(self):
        return 200 <= self.status_code < 400

    def raise_for_status(self):
        import requests
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} simulated error", response=self)

    def iter_content(self, chunk_size=65536):
        for start in range(0, len(self._body), chunk_size):
            yield self._body[start:start + chunk_size]

    def json(self):
        return json.loads(self._body)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def parse_page_range(value):
    pages = []
    for part in value.split(","):
        if "-" in part:
            start, end = (int(item) for item in part.split("-"))
            pages.extend(range(start, end + 1))
        elif part:
            pages.append(int(part))
    return pages


class SimulatedProviders:
    """Cloudinary, Datalab and Voyage stand-ins with assumed latencies."""

    def __init__(self, profile, scale=1.0):
        self.latency = {key: value * (scale if not key.endswith("mbps") else 1) for key, value in profile.items()}
        self.lock = threading.Lock()
        self.originals = {}
        self.jobs = {}
        self.specs = {}
        self.calls = {"original_upload": 0, "original_download": 0, "datalab_submit": 0,
                      "datalab_poll": 0, "image_upload": 0, "voyage": 0}

    def wait(self, base_key, size=0, rate_key=None):
        seconds = self.latency[base_key]
        if rate_key and self.latency[rate_key]:
            seconds += size / (self.latency[rate_key] * 1024 * 1024)
        if seconds > 0:
            time.sleep(seconds)

    def count(self, name):
        with self.lock:
            self.calls[name] += 1

    def register(self, sha256, spec):
        with self.lock:
            self.specs[sha256] = spec

    # Cloudinary originals
    def upload_large(self, stream, public_id=None, **options):
        data = stream.read()
        self.count("original_upload")
        self.wait("original_upload_base", len(data), "original_upload_mbps")
        with self.lock:
            self.originals[public_id] = data
        return {"public_id": public_id, "resource_type": "raw", "type": "authenticated", "bytes": len(data)}

    def session_get(self, session, url, **kwargs):
        public_id = parse_qs(urlsplit(url).query).get("public_id", [""])[0]
        data = self.originals.get(public_id) or self.originals.get(public_id.rsplit(".", 1)[0])
        if data is None:
            return FakeResponse(404, {"error": "missing"})
        self.count("original_download")
        self.wait("original_download_base", len(data), "original_download_mbps")
        return FakeResponse(200, body=data, headers={"Content-Type": "application/octet-stream"})

    # Cloudinary images
    def upload_image(self, file, folder=None, public_id=None, **options):
        file.read()
        self.count("image_upload")
        self.wait("image_upload")
        return {"secure_url": f"https://res.cloudinary.com/benchcloud/image/authenticated/v1/{folder}/{public_id}.png",
                "public_id": f"{folder}/{public_id}", "resource_type": "image", "type": "authenticated"}

    # Datalab and Voyage
    def post(self, url, **kwargs):
        if "voyageai.com" in url:
            return self.voyage(kwargs["json"])
        if url.startswith("https://www.datalab.to/"):
            return self.datalab_submit(kwargs)
        raise AssertionError("Unexpected POST in offline benchmark")

    def get(self, url, **kwargs):
        if url.startswith("https://datalab.invalid/check/"):
            return self.datalab_poll(url.rsplit("/", 1)[-1])
        raise AssertionError("Unexpected GET in offline benchmark")

    def voyage(self, body):
        self.count("voyage")
        self.wait("voyage_base")
        if self.latency["voyage_per_input"]:
            time.sleep(self.latency["voyage_per_input"] * len(body["input"]))
        return FakeResponse(200, {"data": [{"index": index, "embedding": deterministic_vector(text)}
                                           for index, text in enumerate(body["input"])]})

    def datalab_submit(self, kwargs):
        upload = kwargs["files"]["file"][1]
        data = upload.read()
        spec = self.specs[hashlib.sha256(data).hexdigest()]
        pages = parse_page_range(kwargs["data"]["page_range"])
        self.count("datalab_submit")
        self.wait("datalab_submit_base", len(data), "datalab_submit_mbps")
        job = uuid4().hex
        ready_at = time.monotonic() + self.latency["datalab_base"] + self.latency["datalab_per_page"] * len(pages)
        with self.lock:
            self.jobs[job] = (spec, pages, ready_at)
        return FakeResponse(200, {"success": True, "request_id": job,
                                  "request_check_url": f"https://datalab.invalid/check/{job}"})

    def datalab_poll(self, job):
        self.count("datalab_poll")
        spec, pages, ready_at = self.jobs[job]
        if time.monotonic() < ready_at:
            return FakeResponse(200, {"status": "processing"})
        return FakeResponse(200, self.datalab_result(spec, pages))

    @staticmethod
    def datalab_result(spec, zero_based_pages):
        children, images = [], {}
        for zero_page in zero_based_pages:
            page = zero_page + 1
            blocks = [{"id": f"/page/{zero_page}/Text/1", "block_type": "Text",
                       "html": f"<p>{prose(page * 13, 40)}</p>", "children": None, "images": {}}]
            if page % 3 == 0:
                blocks.append({"id": f"/page/{zero_page}/Table/2", "block_type": "Table",
                               "html": f"<table><tr><th>Metric</th><th>Value</th></tr>"
                                       f"<tr><td>{WORDS[page % len(WORDS)]}</td><td>{page * 17}</td></tr></table>",
                               "children": None, "images": {}})
            for image in range(spec.images_per_complex_page):
                name = f"page_{page}_figure_{image + 1}.png"
                encoded = base64.b64encode(png_bytes(1 if spec.duplicate_images else page * 10 + image)).decode()
                images[name] = encoded
                blocks.append({"id": f"/page/{zero_page}/Picture/{image + 3}", "block_type": "Picture",
                               "html": "", "children": None, "images": {name: encoded}})
            children.append({"id": f"/page/{zero_page}/Page/{zero_page}", "block_type": "Page",
                             "html": "", "children": blocks})
        return {"status": "complete", "json": {"block_type": "Document", "children": children,
                                               "metadata": {}}, "images": images}


@contextmanager
def offline_network(providers):
    """Refuse non-loopback Python sockets and route provider SDK calls to the simulators.

    Loopback stays available because asyncio's event loop uses a local socket pair.
    """
    import cloudinary.uploader
    import requests

    create_connection, connect = socket.create_connection, socket.socket.connect

    def loopback(address):
        host = address[0] if isinstance(address, tuple) else None
        return host in {"127.0.0.1", "::1", "localhost"}

    def guarded_create_connection(address, *args, **kwargs):
        if not loopback(address):
            raise AssertionError("Network access is disabled in the offline benchmark")
        return create_connection(address, *args, **kwargs)

    def guarded_connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and not loopback(address):
            raise AssertionError("Network access is disabled in the offline benchmark")
        return connect(sock, address)

    with ExitStack() as stack:
        stack.enter_context(patch("socket.create_connection", guarded_create_connection))
        stack.enter_context(patch.object(socket.socket, "connect", guarded_connect))
        stack.enter_context(patch.object(requests, "post", providers.post))
        stack.enter_context(patch.object(requests, "get", providers.get))
        stack.enter_context(patch.object(requests.Session, "get",
                                         lambda session, url, **kw: providers.session_get(session, url, **kw)))
        stack.enter_context(patch.object(cloudinary.uploader, "upload_large", providers.upload_large))
        stack.enter_context(patch.object(cloudinary.uploader, "upload", providers.upload_image))
        yield


# ---- Timing capture ---------------------------------------------------------------

QUEUE_EVENTS = ("document_processing_deferred", "document_processing_slot_ready", "document_retry_scheduled")


class TimingCapture(logging.Handler):
    """logging.Handler.handle already serializes emit() with the handler's own lock.

    Stage events get a harness-only capture time (perf_counter) so slot idle time
    can be computed from interval overlap; it is never added to application logs.
    """

    def __init__(self):
        super().__init__(logging.INFO)
        self.events = []
        self.queue_events = {name: 0 for name in QUEUE_EVENTS}

    def emit(self, record):
        event = getattr(record, "safe_event", None)
        if not event:
            return
        if event.get("event") == "processing_stage_completed":
            self.events.append({**event, "captured_at": time.perf_counter()})
        elif event.get("event") in self.queue_events:
            self.queue_events[event["event"]] += 1


@contextmanager
def capture_timings():
    handler = TimingCapture()
    loggers = [logging.getLogger("app.timing"), logging.getLogger("app.services.task_queue")]
    previous = [(item.level, item.propagate, item.disabled) for item in loggers]
    for item in loggers:
        item.addHandler(handler)
        # Alembic's fileConfig may have disabled pre-existing loggers; keep events local.
        item.setLevel(logging.INFO)
        item.propagate = False
        item.disabled = False
    try:
        yield handler
    finally:
        for item, state in zip(loggers, previous):
            item.removeHandler(handler)
            item.level, item.propagate, item.disabled = state


@contextmanager
def count_sql(engine):
    """Every statement the application sends through its engine (database load)."""
    from sqlalchemy import event
    counter = {"statements": 0}
    lock = threading.Lock()

    def before_cursor_execute(*args, **kwargs):
        with lock:
            counter["statements"] += 1

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        yield counter
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)


def interval_union(intervals):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def queue_idle_ms(results):
    """Time when at least one document waited for the slot while none was processing."""
    waiting, active = [], []
    for result in results:
        for event in result.timings:
            end = event["captured_at"]
            if event["stage"] == "queue_wait":
                waiting.append((end - event["duration_ms"] / 1000, end))
            elif event["stage"] == "processing_active":
                active.append((end - event["duration_ms"] / 1000, end))
    idle = 0.0
    busy = interval_union(active)
    for start, end in interval_union(waiting):
        overlap = sum(max(0.0, min(end, b_end) - max(start, b_start)) for b_start, b_end in busy)
        idle += (end - start) - overlap
    return round(idle * 1000, 3)


def summarize_stages(events):
    stages = {}
    for event in events:
        entry = stages.setdefault(event["stage"], {"count": 0, "total_ms": 0.0, "max_ms": 0.0, "failed": 0})
        entry["count"] += 1
        entry["total_ms"] = round(entry["total_ms"] + event["duration_ms"], 3)
        entry["max_ms"] = round(max(entry["max_ms"], event["duration_ms"]), 3)
        if event.get("outcome") == "failed":
            entry["failed"] += 1
            entry["exception_type"] = event.get("exception_type")
    return stages


# ---- Fingerprints -----------------------------------------------------------------

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def without_own_id(rows, document_id):
    """Asset paths contain the Cloudinary folder document_<database id>, which differs
    between runs; replace only this document's own id, keeping the rest of the path."""
    return json.loads(canonical(rows).replace(f"/document_{document_id}/", "/document_<self>/"))


def digest(rows):
    return hashlib.sha256(canonical(rows).encode("utf-8")).hexdigest()


def document_fingerprint(connection, document_id):
    from sqlalchemy import text
    document = connection.execute(text(
        "SELECT filename, file_type, file_size_bytes, file_sha256, pages_count, processing_status, "
        "processing_stage, processing_progress, processing_error FROM documents WHERE id = :id"),
        {"id": document_id}).mappings().one()
    chunks = without_own_id([dict(row) for row in connection.execute(text(
        "SELECT content, content_type, location, metadata, embedding::text AS embedding "
        "FROM document_chunks WHERE document_id = :id ORDER BY id"), {"id": document_id}).mappings()], document_id)
    assets = without_own_id([dict(row) for row in connection.execute(text(
        "SELECT asset_type, location, title, caption, content, file_path, metadata "
        "FROM document_assets WHERE document_id = :id ORDER BY id"), {"id": document_id}).mappings()], document_id)
    mapping = [(index, (chunk["metadata"] or {}).get("asset_filename"), (chunk["metadata"] or {}).get("asset_path"))
               for index, chunk in enumerate(chunks) if (chunk["metadata"] or {}).get("asset_path")]
    pages = sorted({(chunk["metadata"] or {}).get("page") for chunk in chunks} - {None})
    return {
        "status": {key: document[key] for key in ("processing_status", "processing_stage",
                                                  "processing_progress", "processing_error", "pages_count")},
        "counts": {"chunks": len(chunks), "assets": len(assets), "mapped_assets": len(mapping),
                   "pages_with_chunks": len(pages)},
        "text_and_order": digest([(c["content"], c["content_type"]) for c in chunks]),
        "metadata_and_pages": digest([(c["location"], c["metadata"]) for c in chunks]),
        "asset_mapping": digest({"assets": assets, "chunks": mapping}),
        "embeddings": digest([c["embedding"] for c in chunks]),
        "source": digest({key: document[key] for key in ("filename", "file_type", "file_size_bytes", "file_sha256")}),
    }


# ---- Benchmark run -----------------------------------------------------------------

@dataclass
class UploadResult:
    label: str
    document_id: int
    status_code: int
    request_id: str
    wall_seconds: float
    timings: list = field(default_factory=list)


def create_user(engine, index):
    from sqlalchemy import text
    from app.database.models import User
    with engine.begin() as connection:
        user_id = connection.execute(text(
            "INSERT INTO users (google_sub, email, name) VALUES (:sub, :email, 'Benchmark') RETURNING id"),
            {"sub": f"benchmark-{index}-{uuid4().hex}", "email": f"benchmark-{index}-{uuid4().hex}@example.invalid"}).scalar()
    from app.services.auth_service import create_access_token
    return create_access_token(User(id=user_id, email="benchmark@example.invalid"))


def upload(app, token, spec, pdf):
    from fastapi.testclient import TestClient
    started = time.perf_counter()
    with TestClient(app) as client:
        # Background tasks complete before TestClient returns: wall time = request -> Ready.
        response = client.post("/documents", headers={"Authorization": f"Bearer {token}"},
                               files={"file": (f"{spec.label}.pdf", pdf, "application/pdf")})
    wall = time.perf_counter() - started
    body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    return UploadResult(spec.label, body.get("id", -1), response.status_code,
                        response.headers.get("x-request-id", ""), wall)


def run_scenario(app, engine, providers, scenario, user_offset):
    documents =[(spec, build_pdf(spec)) for spec in scenario.documents]
    for spec, pdf in documents:
        providers.register(hashlib.sha256(pdf).hexdigest(), spec)
    tokens = [create_user(engine, user_offset + index) for index in range(len(documents))]
    with capture_timings() as capture, count_sql(engine) as sql:
        started = time.perf_counter()
        if scenario.concurrent:
            results = [None] * len(documents)
            def worker(position):
                spec, pdf = documents[position]
                results[position] = upload(app, tokens[position], spec, pdf)
            threads = [threading.Thread(target=worker, args=(index,)) for index in range(len(documents))]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        else:
            results = [upload(app, tokens[index], spec, pdf) for index, (spec, pdf) in enumerate(documents)]
        scenario_wall = time.perf_counter() - started
    for result in results:
        result.timings = [event for event in capture.events if event.get("request_id") == result.request_id]
    with engine.connect() as connection:
        fingerprints = {result.label: document_fingerprint(connection, result.document_id) for result in results}
    report = {"scenario": scenario.name, "concurrent": scenario.concurrent,
              "wall_seconds": round(scenario_wall, 3), "sql_statements": sql["statements"],
              "queue_events": dict(capture.queue_events), "queue_idle_ms": queue_idle_ms(results),
              "documents": []}
    for result in results:
        stages = summarize_stages(result.timings)
        top_level = round(sum(stages.get(name, {}).get("total_ms", 0.0) for name in TOP_LEVEL_STAGES), 3)
        report["documents"].append({
            "label": result.label, "status_code": result.status_code,
            "request_to_ready_wall_ms": round(result.wall_seconds * 1000, 3),
            "instrumented_request_to_ready_ms": stages.get("request_to_ready", {}).get("total_ms"),
            "top_level_stage_sum_ms": top_level,
            "stages": stages,
            "fingerprint": fingerprints[result.label],
        })
    return report


def run_benchmark(scenario_names, latency="realistic", scale=1.0):
    """Run scenarios against the database already bound through DATABASE_URL."""
    import importlib
    from app.database import database
    from app.services import observability, original_storage, resource_admission

    providers = SimulatedProviders(LATENCY_PROFILES[latency], scale)
    allow = type("AllowAll", (), {"eval": staticmethod(lambda *args: (1, 0))})()
    results = {"latency_profile": latency, "scale": scale, "scenarios": []}
    with ExitStack() as stack:
        stack.enter_context(offline_network(providers))
        stack.enter_context(patch.object(resource_admission, "rate_backend", lambda: allow))
        # Production stores originals in Cloudinary and downloads them for processing.
        stack.enter_context(patch.object(original_storage, "uses_shared_original_storage", lambda: True))
        stack.enter_context(patch.object(observability, "configure_logging", lambda: None))
        main = importlib.import_module("main")
        for offset, name in enumerate(scenario_names):
            results["scenarios"].append(run_scenario(main.app, database.engine, providers,
                                                     SCENARIOS[name], offset * 10))
    results["provider_calls"] = dict(providers.calls)
    return results


def fingerprints_of(results):
    return {scenario["scenario"]: {document["label"]: document["fingerprint"] for document in scenario["documents"]}
            for scenario in results["scenarios"]}


# ---- CLI: disposable database lifecycle ----------------------------------------------

def checked_database_url(value):
    from sqlalchemy.engine import make_url
    url = make_url(value)
    name = url.database or ""
    if (url.drivername not in {"postgresql", "postgresql+psycopg"} or url.host not in {"127.0.0.1", "localhost", "::1"}
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,25}", name) or not ({"test", "bench"} & set(name.split("_")))
            or {"dev", "development", "staging", "prod", "production"} & set(name.split("_"))):
        raise SystemExit("--database-url must be a loopback PostgreSQL URL whose name contains a 'test' or 'bench' segment")
    return url.set(drivername="postgresql+psycopg")


# Dummy, non-secret settings; .env loading is disabled so no real key is ever read.
OFFLINE_ENVIRONMENT = dict(
    PYTHON_DOTENV_DISABLED="1", ENVIRONMENT="test", TASK_QUEUE="background",
    GOOGLE_CLIENT_ID="benchmark.apps.googleusercontent.com", JWT_SECRET_KEY="benchmark-" * 8,
    DEEPSEEK_API_KEY="offline-benchmark", VOYAGE_API_KEY="offline-benchmark",
    DATALAB_API_KEY="offline-benchmark", CLOUDINARY_URL="cloudinary://benchkey:benchsecret@benchcloud",
    RESOURCE_REDIS_URL="redis://127.0.0.1:1/0",
)
# Production defaults apply: overrides of the measured behaviour are removed.
CLEARED_SETTINGS = ("VOYAGE_BATCH_SIZE", "DATALAB_BATCH_SIZE", "DATALAB_PARALLEL_BATCHES",
                    "DATALAB_POLL_INTERVAL_SECONDS", "RESOURCE_PROCESSING_CONCURRENCY", "AUTH_CLIENT_IP_SOURCE")


def configure_environment(run_url=None):
    os.environ.update(OFFLINE_ENVIRONMENT)
    if run_url is not None:
        os.environ["DATABASE_URL"] = run_url.render_as_string(hide_password=False)
    for name in CLEARED_SETTINGS:
        os.environ.pop(name, None)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--latency", choices=sorted(LATENCY_PROFILES), default="realistic")
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--scenarios", default=",".join(SCENARIOS))
    parser.add_argument("--output")
    parser.add_argument("--write-baseline", action="store_true",
                        help=f"write output fingerprints to {BASELINE_PATH.relative_to(BACKEND_DIR)}")
    args = parser.parse_args(argv)
    names = [name for name in args.scenarios.split(",") if name]
    unknown = set(names) - set(SCENARIOS)
    if unknown:
        raise SystemExit(f"Unknown scenarios: {', '.join(sorted(unknown))}")

    base = checked_database_url(args.database_url)
    run_url = base.set(database=f"{base.database}_run_{uuid4().hex}")
    sys.path.insert(0, str(BACKEND_DIR))
    configure_environment(run_url)
    from sqlalchemy import create_engine, text
    admin = create_engine(run_url.set(database="postgres"), isolation_level="AUTOCOMMIT", hide_parameters=True)
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{run_url.database}"'))
    try:
        from alembic import command
        from alembic.config import Config
        command.upgrade(Config(str(BACKEND_DIR / "alembic.ini")), "head")
        results = run_benchmark(names, args.latency, args.scale)
    finally:
        if "app.database.database" in sys.modules:
            sys.modules["app.database.database"].engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{run_url.database}"'))
        admin.dispose()

    rendered = json.dumps(results, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    if args.write_baseline:
        BASELINE_PATH.write_text(json.dumps(fingerprints_of(results), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
