"""Background processing queue: slot waiting and handoff (Phase 2 P1), offline.

Run: python -B tests/test_processing_queue.py
The real _run_document_processing loop is exercised with a stand-in for
process_document that uses the processing slot exactly like
process_claimed_document: acquire(blocking=False), hold, release().
"""

import importlib
import os
import sys
import threading
import time
import types
import unittest
from contextlib import ExitStack
from threading import BoundedSemaphore
from unittest.mock import patch


class Stop(Exception):
    pass


class ProcessingQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stack = ExitStack()
        cls.stack.enter_context(patch.dict(os.environ, {"PYTHON_DOTENV_DISABLED": "1", "ENVIRONMENT": "test"}))
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
        cls.queue = importlib.import_module("app.services.task_queue")
        cls.errors = importlib.import_module("app.services.document_processing_errors")

    @classmethod
    def tearDownClass(cls):
        cls.stack.close()

    def setUp(self):
        self.slots = BoundedSemaphore(1)
        self.processing = types.ModuleType("app.services.document_processing_service")
        self.processing.mark_processing_retries_exhausted = lambda document_id: None
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(sys.modules, {"app.services.document_processing_service": self.processing}))
        self.stack.enter_context(patch.object(self.queue, "_BACKGROUND_PROCESSING_SLOTS", self.slots))
        self.order, self.lock = [], threading.Lock()
        self.active, self.max_active = 0, 0

    def assert_slot_free(self):
        self.assertTrue(self.slots.acquire(blocking=False), "processing slot leaked")
        self.slots.release()

    def realistic_process(self, work_seconds):
        """Behaves like process_claimed_document's capacity handling."""
        def process_document(document_id, processing_capacity=None, **timing):
            if not processing_capacity.acquire(blocking=False):
                return "deferred"
            try:
                with self.lock:
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                    self.order.append(document_id)
                time.sleep(work_seconds)
                return "completed"
            finally:
                with self.lock:
                    self.active -= 1
                processing_capacity.release()
        return process_document

    def run_concurrently(self, document_ids, stagger=0.0):
        results = {}
        def worker(document_id):
            results[document_id] = self.queue._run_document_processing(document_id)
        threads = []
        for document_id in document_ids:
            threads.append(threading.Thread(target=worker, args=(document_id,)))
            threads[-1].start()
            time.sleep(stagger)
        for thread in threads:
            thread.join(timeout=30)
        return results

    def test_concurrent_uploads_hand_the_slot_over_without_idle_time(self):
        self.processing.process_document = self.realistic_process(0.3)
        with patch.object(self.queue, "sleep") as sleep:
            started = time.monotonic()
            results = self.run_concurrently([1, 2, 3])
            elapsed = time.monotonic() - started
        self.assertEqual(results, {1: "completed", 2: "completed", 3: "completed"})
        self.assertEqual(self.max_active, 1)          # concurrency limit preserved
        sleep.assert_not_called()                     # no blind backoff for capacity
        self.assertLess(elapsed, 0.9 + 0.6)           # 3 x 0.3s of work; old minimum was 0.3 + 2 + 4
        self.assert_slot_free()

    def test_waiters_are_served_in_arrival_order(self):
        self.processing.process_document = self.realistic_process(0.15)
        self.slots.acquire()                          # an earlier document holds the slot
        threading.Timer(0.4, self.slots.release).start()
        with patch.object(self.queue, "sleep"):
            results = self.run_concurrently([1, 2, 3], stagger=0.08)
        self.assertEqual(set(results.values()), {"completed"})
        self.assertEqual(self.order, [1, 2, 3])
        self.assert_slot_free()

    def test_handed_off_slot_is_returned_when_the_attempt_does_not_claim_it(self):
        for finish in ("already_complete", "busy", "permanent_failure", "deleted"):
            with self.subTest(finish=finish):
                outcomes = iter(["deferred", finish])
                self.processing.process_document = lambda document_id, processing_capacity=None, **kw: next(outcomes)
                self.slots.acquire()
                threading.Timer(0.1, self.slots.release).start()
                with patch.object(self.queue, "sleep") as sleep:
                    self.assertEqual(self.queue._run_document_processing(7), finish)
                sleep.assert_not_called()
                self.assert_slot_free()

    def test_handed_off_slot_is_returned_once_on_failures(self):
        retryable = self.errors.RetryableDocumentProcessingError("safe")
        calls = {"count": 0}

        def process_document(document_id, processing_capacity=None, **timing):
            calls["count"] += 1
            if calls["count"] == 1:
                return "deferred"
            if calls["count"] == 2:
                # Claims the handed-off slot, then fails transiently (callee releases it).
                assert processing_capacity.acquire(blocking=False)
                processing_capacity.release()
                raise retryable
            if calls["count"] == 3:
                raise ValueError("permanent")
        self.processing.process_document = process_document
        self.slots.acquire()
        threading.Timer(0.1, self.slots.release).start()
        with patch.object(self.queue, "sleep") as sleep, \
                patch.object(self.queue, "log_generation_failure"):
            # BoundedSemaphore raises ValueError on a double release.
            self.assertEqual(self.queue._run_document_processing(8), "failed")
        self.assertEqual(calls["count"], 3)
        self.assertEqual(sleep.call_count, 1)         # one failure-retry backoff, unchanged
        self.assert_slot_free()

    def test_permit_limited_deferrals_fall_back_to_backoff_without_spinning(self):
        calls = {"count": 0}

        def always_deferred(document_id, processing_capacity=None, **timing):
            calls["count"] += 1
            return "deferred"                         # slot free: the cause is elsewhere
        self.processing.process_document = always_deferred
        sleeps = []

        def sleep(delay):
            sleeps.append(delay)
            if len(sleeps) == 3:
                raise Stop()
        with patch.object(self.queue, "sleep", side_effect=sleep), self.assertRaises(Stop):
            self.queue._run_document_processing(9)
        # One immediate retry while holding the slot, then the original backoff.
        self.assertEqual(calls["count"], 4)
        self.assertEqual(sleeps, [4, 8, 16])
        self.assert_slot_free()

    def test_wait_times_out_and_retries_on_the_existing_schedule(self):
        self.processing.process_document = self.realistic_process(0.0)
        self.slots.acquire()
        threading.Timer(0.35, self.slots.release).start()
        attempts = []
        original = self.processing.process_document

        def counting(document_id, processing_capacity=None, **timing):
            attempts.append(time.monotonic())
            return original(document_id, processing_capacity=processing_capacity, **timing)
        self.processing.process_document = counting
        with patch.object(self.queue, "retry_delay", lambda retries: 0.1), \
                patch.object(self.queue, "sleep") as sleep:
            self.assertEqual(self.queue._run_document_processing(10), "completed")
        sleep.assert_not_called()
        # Each timed-out wait led to a normal attempt (which refreshes liveness).
        self.assertGreaterEqual(len(attempts), 3)
        self.assert_slot_free()

    def test_genuine_processing_failures_keep_their_bounded_retries(self):
        retryable = self.errors.RetryableDocumentProcessingError("safe")
        calls = {"count": 0}

        def failing(document_id, processing_capacity=None, **timing):
            calls["count"] += 1
            raise retryable
        exhausted = []
        self.processing.process_document = failing
        self.processing.mark_processing_retries_exhausted = exhausted.append
        with patch.object(self.queue, "sleep") as sleep:
            self.assertEqual(self.queue._run_document_processing(11), "retry_exhausted")
        self.assertEqual(calls["count"], 4)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [2, 4, 8])
        self.assertEqual(exhausted, [11])
        self.assert_slot_free()


if __name__ == "__main__":
    unittest.main(verbosity=2)
