"""Provider timeout and retry limits: python -B tests/test_provider_timeouts.py.

Mocked HTTP only. Sockets are blocked, so no DeepSeek or Voyage request can be
made; the SDK's retry decisions run for real against an in-memory transport.
"""

import json
import os
import sys
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch


BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

PROFILE_EXPECTATIONS = {
    "answer": (90.0, 2),
    "summary": (120.0, 2),
    "summary_assistant": (60.0, 1),
    "title": (20.0, 1),
    "intent": (15.0, 0),
}


def http_response(status, body=None, headers=None):
    import requests

    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body if body is not None else {}).encode()
    response.headers.update(headers or {})
    response.url = "https://api.voyageai.invalid/v1/embeddings"
    return response


def voyage_success(count):
    return http_response(200, {"data": [
        {"index": index, "embedding": [1.0] + [0.0] * 511} for index in range(count)
    ]})


class FakeClock:
    def __init__(self, request_seconds=0.0):
        self.now = 0.0
        self.sleeps = []
        self.request_seconds = request_seconds

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class ProviderTimeoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stack = ExitStack()
        cls.stack.enter_context(patch.dict(os.environ, {
            "PYTHON_DOTENV_DISABLED": "1",
            "ENVIRONMENT": "test",
            "DATABASE_URL": "postgresql+psycopg://synthetic@127.0.0.1:1/synthetic_test",
            "DEEPSEEK_API_KEY": "synthetic-deepseek-key",
            "VOYAGE_API_KEY": "synthetic-voyage-key",
        }, clear=False))
        # Defaults only: ambient overrides are removed and restored with the patch above.
        for name in list(os.environ):
            if name.startswith("LLM_") or (name.startswith("VOYAGE_") and name != "VOYAGE_API_KEY"):
                os.environ.pop(name)
        cls.stack.enter_context(patch("dotenv.load_dotenv", return_value=False))
        cls.stack.enter_context(patch("socket.create_connection", side_effect=AssertionError("No network in this suite")))

        import openai
        import openai._base_client as base_client
        from app.services import embedding_service, llm_client
        from app.services.document_processing_errors import is_retryable_processing_error

        cls.openai = openai
        cls.base_client = base_client
        cls.llm_client = llm_client
        cls.embedding = embedding_service
        cls.is_retryable = staticmethod(is_retryable_processing_error)

    @classmethod
    def tearDownClass(cls):
        cls.stack.close()

    # ---- DeepSeek client limits -------------------------------------------------

    def test_every_profile_has_explicit_timeout_and_bounded_retries(self):
        for purpose, (seconds, retries) in PROFILE_EXPECTATIONS.items():
            with self.subTest(purpose=purpose):
                client = self.llm_client.deepseek_client(purpose, api_key="synthetic", base_url="https://deepseek.invalid")
                self.assertIsInstance(client.timeout, self.openai.Timeout)
                self.assertEqual(client.timeout.read, seconds)
                self.assertEqual(client.timeout.connect, 10.0)
                self.assertEqual(client.max_retries, retries)
                self.assertLess(client.timeout.read, 600, "must not inherit the SDK's 600 second default")

    def test_timeout_overrides_are_validated_without_echoing_values(self):
        with patch.dict(os.environ, {"LLM_INTENT_TIMEOUT_SECONDS": "7.5"}):
            timeout, retries = self.llm_client.client_limits("intent")
        self.assertEqual((timeout.read, timeout.connect, retries), (7.5, 7.5, 0))
        for bad in ("abc-secret-marker", "0", "-3", "7200"):
            with self.subTest(value=bad), patch.dict(os.environ, {"LLM_SUMMARY_TIMEOUT_SECONDS": bad}):
                with self.assertRaises(ValueError) as caught:
                    self.llm_client.client_limits("summary")
                self.assertNotIn(bad, str(caught.exception))
                self.assertIn("LLM_SUMMARY_TIMEOUT_SECONDS", str(caught.exception))

    def test_application_clients_use_their_profiles(self):
        from app.services import chat_intent_service, chat_title_service, llm_service
        from app.services.summaries import summary_assistant_service, summary_generation_service

        chat_title_service.get_deepseek_client.cache_clear()
        clients = {
            "answer": llm_service.client,
            "intent": chat_intent_service.client,
            "title": chat_title_service.get_deepseek_client(),
            "summary": summary_generation_service.client,
            "summary_assistant": summary_assistant_service.client,
        }
        chat_title_service.get_deepseek_client.cache_clear()
        for purpose, client in clients.items():
            seconds, retries = PROFILE_EXPECTATIONS[purpose]
            with self.subTest(purpose=purpose):
                self.assertEqual((client.timeout.read, client.max_retries), (seconds, retries))

    def sdk_client(self, purpose, responses):
        import httpx2

        calls = []
        queue = list(responses)

        def handler(request):
            calls.append(request)
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            status, body = item
            return httpx2.Response(status, json=body, request=request)

        timeout, retries = self.llm_client.client_limits(purpose)
        client = self.openai.OpenAI(
            api_key="synthetic", base_url="https://deepseek.invalid/v1",
            timeout=timeout, max_retries=retries,
            http_client=httpx2.Client(transport=httpx2.MockTransport(handler), timeout=timeout),
        )
        sleeps = []
        patcher = patch.object(self.base_client.SyncAPIClient, "_sleep_for_retry",
                               lambda self, **kwargs: sleeps.append(kwargs["retries_taken"]))
        patcher.start()
        self.addCleanup(patcher.stop)
        return client, calls, sleeps

    @staticmethod
    def completion(text="ok"):
        return {"id": "c", "object": "chat.completion", "created": 0, "model": "m",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": text}}]}

    def create(self, client):
        return client.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}])

    def test_sdk_retries_transient_5xx_and_429_within_profile_budget(self):
        client, calls, sleeps = self.sdk_client("answer", [(503, {}), (429, {}), (200, self.completion())])
        self.assertEqual(self.create(client).choices[0].message.content, "ok")
        self.assertEqual((len(calls), len(sleeps)), (3, 2))

    def test_sdk_gives_up_after_profile_retry_budget(self):
        client, calls, _ = self.sdk_client("summary_assistant", [(502, {}), (502, {}), (200, self.completion())])
        with self.assertRaises(self.openai.InternalServerError):
            self.create(client)
        self.assertEqual(len(calls), 2)  # one retry, never a third request

    def test_intent_routing_fails_fast_without_retry(self):
        client, calls, sleeps = self.sdk_client("intent", [(503, {}), (200, self.completion())])
        with self.assertRaises(self.openai.InternalServerError):
            self.create(client)
        self.assertEqual((len(calls), len(sleeps)), (1, 0))

    def test_sdk_does_not_retry_client_errors(self):
        client, calls, _ = self.sdk_client("answer", [(400, {"error": {"message": "bad"}}), (200, self.completion())])
        with self.assertRaises(self.openai.BadRequestError):
            self.create(client)
        self.assertEqual(len(calls), 1)

    def test_sdk_network_timeout_is_bounded_and_classified_as_timeout(self):
        import httpx2
        from app.services.error_service import TIMEOUT_TYPES

        client, calls, _ = self.sdk_client("title", [httpx2.ReadTimeout("synthetic"), httpx2.ReadTimeout("synthetic")])
        with self.assertRaises(self.openai.APITimeoutError) as caught:
            self.create(client)
        self.assertEqual(len(calls), 2)
        self.assertIsInstance(caught.exception, TIMEOUT_TYPES)

    def test_answer_stream_still_yields_deltas_through_limited_client(self):
        import httpx2
        from app.services import llm_service

        def chunk(content):
            return "data: " + json.dumps({"id": "c", "object": "chat.completion.chunk", "created": 0, "model": "m",
                                          "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}]})
        body = "\n\n".join([chunk("Hel"), chunk("lo"), "data: [DONE]"]) + "\n\n"

        def handler(request):
            return httpx2.Response(200, text=body, headers={"content-type": "text/event-stream"}, request=request)

        timeout, retries = self.llm_client.client_limits("answer")
        client = self.openai.OpenAI(api_key="synthetic", base_url="https://deepseek.invalid/v1",
                                    timeout=timeout, max_retries=retries,
                                    http_client=httpx2.Client(transport=httpx2.MockTransport(handler), timeout=timeout))
        with patch.object(llm_service, "client", client):
            self.assertEqual("".join(llm_service.generate_answer_stream("q", "ctx", [])), "Hello")

    # ---- Voyage embeddings -------------------------------------------------------

    def run_voyage(self, responses, *, query=False, texts=("synthetic passage",)):
        clock = FakeClock()
        queue = list(responses)
        calls = []

        def post(url, **kwargs):
            calls.append(kwargs["timeout"])
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        with patch.object(self.embedding, "time", SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep)), \
                patch.object(self.embedding.requests, "post", side_effect=post):
            if query:
                result = self.embedding.create_query_embedding(texts[0])
            else:
                result = self.embedding.create_passage_embeddings(list(texts))
        return result, calls, clock

    def run_voyage_failure(self, responses, *, query=False):
        clock = FakeClock()
        queue = list(responses)
        calls = []

        def post(url, **kwargs):
            calls.append(kwargs["timeout"])
            item = queue.pop(0) if len(queue) > 1 else queue[0]
            if isinstance(item, Exception):
                raise item
            return item

        with patch.object(self.embedding, "time", SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep)), \
                patch.object(self.embedding.requests, "post", side_effect=post):
            with self.assertRaises(Exception) as caught:
                if query:
                    self.embedding.create_query_embedding("synthetic query")
                else:
                    self.embedding.create_passage_embeddings(["synthetic passage"])
        return caught.exception, calls, clock

    def test_voyage_retries_429_honouring_retry_after_seconds(self):
        result, calls, clock = self.run_voyage([http_response(429, headers={"Retry-After": "2"}), voyage_success(1)])
        self.assertEqual(len(result), 1)
        self.assertEqual((len(calls), clock.sleeps), (2, [2.0]))
        self.assertEqual(calls[0], (10.0, 60.0))

    def test_voyage_retries_5xx_and_network_errors_with_backoff(self):
        import requests

        result, calls, clock = self.run_voyage([
            http_response(503), requests.exceptions.ConnectionError("synthetic"),
            http_response(502), requests.exceptions.Timeout("synthetic"), voyage_success(1),
        ])
        self.assertEqual(len(result), 1)
        self.assertEqual(len(calls), 5)
        self.assertEqual(clock.sleeps, [2.0, 4.0, 8.0, 16.0])

    def test_voyage_does_not_retry_client_errors(self):
        import requests

        error, calls, clock = self.run_voyage_failure([http_response(400)])
        self.assertIsInstance(error, requests.exceptions.HTTPError)
        self.assertEqual((len(calls), clock.sleeps), (1, []))
        self.assertFalse(self.is_retryable(error))

    def test_voyage_passage_attempts_are_bounded_and_final_error_stays_retryable(self):
        import requests

        error, calls, _ = self.run_voyage_failure([http_response(503)])
        self.assertIsInstance(error, requests.exceptions.HTTPError)
        self.assertEqual(len(calls), self.embedding.VOYAGE_MAX_RETRIES)
        self.assertTrue(self.is_retryable(error))

        error, calls, _ = self.run_voyage_failure([requests.exceptions.Timeout("synthetic")])
        self.assertIsInstance(error, requests.exceptions.Timeout)
        self.assertEqual(len(calls), self.embedding.VOYAGE_MAX_RETRIES)
        self.assertTrue(self.is_retryable(error))

    def test_voyage_query_fails_faster_than_document_processing(self):
        error, calls, clock = self.run_voyage_failure([http_response(429, headers={"Retry-After": "20"})], query=True)
        # 20s wait fits once inside the 30s query deadline; a second would not.
        self.assertEqual(len(calls), 2)
        self.assertEqual(clock.sleeps, [20.0])
        self.assertEqual(calls[1], (10.0, 10.0))  # read timeout shrinks to the time left
        self.assertLessEqual(clock.now + calls[-1][1], self.embedding.QUERY_DEADLINE_SECONDS)

    def test_voyage_retry_after_http_date_and_huge_values_are_capped(self):
        future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=12), usegmt=True)
        delay = self.embedding.retry_delay_seconds(0, future)
        self.assertTrue(10.0 <= delay <= 12.0, delay)
        self.assertEqual(self.embedding.retry_delay_seconds(0, "3600"), self.embedding.VOYAGE_MAX_RETRY_DELAY_SECONDS)
        self.assertEqual(self.embedding.retry_delay_seconds(2, "not-a-date"), 8.0)
        self.assertEqual(self.embedding.retry_delay_seconds(0, "nan"), 2.0)
        past = format_datetime(datetime.now(timezone.utc) - timedelta(seconds=30), usegmt=True)
        self.assertEqual(self.embedding.retry_delay_seconds(0, past), 0.0)

    def test_voyage_total_duration_never_exceeds_passage_deadline(self):
        error, calls, clock = self.run_voyage_failure([http_response(503, headers={"Retry-After": "30"})])
        deadline = self.embedding.PASSAGE_DEADLINE_SECONDS
        self.assertLessEqual(clock.now + calls[-1][1], deadline + self.embedding.VOYAGE_TIMEOUT_SECONDS)
        self.assertLessEqual(sum(clock.sleeps), deadline)
        self.assertTrue(self.is_retryable(error))

    def test_voyage_batches_keep_their_size_and_order(self):
        result, calls, _ = self.run_voyage([voyage_success(3)], texts=("a", "b", "c"))
        self.assertEqual((len(result), len(calls)), (3, 1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
