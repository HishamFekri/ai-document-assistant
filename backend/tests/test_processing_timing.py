"""Stage-timing instrumentation (Phase 2 P0), offline.

Run: python -B tests/test_processing_timing.py
"""

import importlib
import io
import json
import logging
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_resource_admission as harness


class ProcessingTimingTests(unittest.TestCase):
    __test__ = __name__ == "__main__"

    @classmethod
    def setUpClass(cls):
        harness.ResourceAdmissionTests.setUpClass.__func__(cls)
        cls.obs = importlib.import_module("app.services.observability")
        cls.ingress = importlib.import_module("app.services.upload_ingress")
        cls.processing = importlib.import_module("app.services.document_processing_service")

    def setUp(self):
        harness.ResourceAdmissionTests.setUp(self)
        self.output = io.StringIO()
        handler = logging.StreamHandler(self.output)
        handler.setFormatter(self.obs.SafeJSONFormatter())
        self.timing = logging.getLogger("app.timing")
        self.previous = (self.timing.level, self.timing.propagate, self.timing.disabled, list(self.timing.handlers))
        self.timing.handlers = [handler]
        self.timing.setLevel(logging.INFO)
        self.timing.propagate = False
        self.timing.disabled = False
        self.addCleanup(self.restore)

    def restore(self):
        self.timing.level, self.timing.propagate, self.timing.disabled, self.timing.handlers = self.previous

    def events(self):
        return [json.loads(line) for line in self.output.getvalue().splitlines() if line.strip()]

    def test_timed_stage_records_duration_counts_and_success(self):
        with patch.object(self.obs, "perf_counter", side_effect=[10.0, 10.25]):
            with self.obs.timed_stage("extraction", pages=3) as metrics:
                metrics["chunks"] = 6
        (event,) = self.events()
        self.assertEqual(event["event"], "processing_stage_completed")
        self.assertEqual(event["logger"], "app.timing")
        self.assertEqual((event["stage"], event["duration_ms"], event["outcome"]), ("extraction", 250.0, "success"))
        self.assertEqual((event["pages"], event["chunks"]), (3, 6))

    def test_failure_is_recorded_and_the_same_exception_is_reraised(self):
        error = TimeoutError("private provider detail")
        with self.assertRaises(TimeoutError) as caught:
            with self.obs.timed_stage("embeddings.provider", batch=2, chunks=5):
                raise error
        self.assertIs(caught.exception, error)
        (event,) = self.events()
        self.assertEqual((event["outcome"], event["exception_type"]), ("failed", "builtins.TimeoutError"))
        self.assertNotIn("private provider detail", self.output.getvalue())

    def test_raw_record_ids_are_stripped_but_correlation_ids_kept(self):
        job_id, request_id = str(uuid4()), str(uuid4())
        operational = logging.getLogger("timing_test_operational")
        operational.handlers = self.timing.handlers
        operational.propagate = False
        operational.setLevel(logging.INFO)
        with self.obs.log_context(document_id=4321, chat_id=77, summary_id=88, message_id=99,
                                  job_id=job_id, request_id=request_id):
            self.obs.log_stage("finalize", 1.5, pages=4)
            self.obs.log_event(operational, logging.INFO, "operational_event")
        timing_event, operational_event = self.events()
        for key in ("document_id", "chat_id", "summary_id", "message_id"):
            self.assertNotIn(key, timing_event)
        self.assertEqual((timing_event["job_id"], timing_event["request_id"]), (job_id, request_id))
        # Existing operational events are unchanged and still carry their context.
        self.assertEqual(operational_event["document_id"], 4321)

    def test_only_allowlisted_fields_survive(self):
        self.obs.log_stage("extraction", 2.0, filename="private-report.pdf", content="secret text",
                           ip="203.0.113.9", api_key="sk-secret", pages=2, bytes=1024)
        text = self.output.getvalue()
        for leaked in ("private-report", "secret text", "203.0.113.9", "sk-secret"):
            self.assertNotIn(leaked, text)
        (event,) = self.events()
        self.assertEqual((event["pages"], event["bytes"]), (2, 1024))

    def test_upload_phase_emits_legacy_event_and_id_free_stage(self):
        legacy = []
        handler = logging.Handler()
        handler.emit = lambda record: legacy.append(record.safe_event)
        self.ingress.logger.addHandler(handler)
        self.addCleanup(self.ingress.logger.removeHandler, handler)
        self.addCleanup(self.ingress.logger.setLevel, self.ingress.logger.level)
        self.ingress.logger.setLevel(logging.INFO)
        with self.ingress.upload_phase("upload_validation", document_id=55):
            pass
        (stage,) = self.events()
        self.assertEqual((stage["stage"], stage["outcome"]), ("upload.validation", "success"))
        self.assertNotIn("document_id", stage)
        self.assertEqual((legacy[0]["operation"], legacy[0]["document_id"]), ("upload_validation", 55))

    def test_queue_wait_and_slot_acquisition_are_additive(self):
        timing = self.processing.AttemptTiming(started=100.0, queued_at=97.5, accepted_at=95.0)
        with patch.object(self.processing, "perf_counter", return_value=106.0):
            self.processing.log_wait_stages(timing, acquired=100.4)
            self.processing.log_completion_stages(timing, acquired=100.4)
        stages = {event["stage"]: event["duration_ms"] for event in self.events()}
        self.assertEqual(stages["queue_wait"], 2500.0)
        self.assertAlmostEqual(stages["slot_acquisition"], 400.0, places=3)
        self.assertAlmostEqual(stages["processing_active"], 5600.0, places=3)
        self.assertEqual(stages["request_to_ready"], 11000.0)

    def test_unknown_or_inconsistent_queue_times_log_only_measurable_stages(self):
        # Celery/direct calls carry no queue or request times; a reading after the
        # attempt start can never produce a negative wait.
        for timing in (self.processing.AttemptTiming(started=5.0),
                       self.processing.AttemptTiming(started=5.0, queued_at=6.0)):
            self.output.seek(0)
            self.output.truncate()
            with patch.object(self.processing, "perf_counter", return_value=9.0):
                self.processing.log_wait_stages(timing, acquired=5.5)
                self.processing.log_completion_stages(timing, acquired=5.5)
            stages = {event["stage"] for event in self.events()}
            self.assertEqual(stages, {"slot_acquisition", "processing_active"})

    def test_request_start_is_recorded_only_inside_a_request(self):
        self.assertIsNone(self.obs.request_started_at())
        seen = []

        async def app(scope, receive, send):
            seen.append(self.obs.request_started_at())
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        import asyncio
        async def receive():
            return {"type": "http.request"}
        async def send(message):
            pass
        asyncio.run(self.obs.RequestObservabilityMiddleware(app)(
            {"type": "http", "method": "GET", "headers": []}, receive, send))
        self.assertIsInstance(seen[0], float)
        self.assertIsNone(self.obs.request_started_at())

    def test_overhead_per_stage_event_is_small(self):
        rounds = 2000
        started = time.perf_counter()
        for _ in range(rounds):
            with self.obs.timed_stage("embeddings.select", batch=1) as metrics:
                metrics["chunks"] = 128
        per_event_ms = (time.perf_counter() - started) * 1000 / rounds
        print(f"\n[overhead] {per_event_ms * 1000:.1f} microseconds per timed stage including JSON formatting")
        self.assertLess(per_event_ms, 2.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
