"""Processing benchmark: determinism, output baseline and stage timing, ONLY through
conftest's isolated TEST_DATABASE_URL fixture (loopback PostgreSQL with pgvector).

Providers are simulated by scripts/benchmark_processing.py with network access
refused, so no paid API can be called. Run in its own pytest process.
"""

import importlib.util
import json
from pathlib import Path

import pytest


BACKEND_DIR = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("benchmark_processing", BACKEND_DIR / "scripts" / "benchmark_processing.py")
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)
# Applied at collection, before the database fixture imports the application.
benchmark.configure_environment()


@pytest.fixture(scope="module")
def runs(test_database):
    scenarios = benchmark.DETERMINISM_SCENARIOS + ("concurrent_queue",)
    first = benchmark.run_benchmark(scenarios, latency="zero")
    second = benchmark.run_benchmark(scenarios, latency="zero")
    return first, second


def documents(result):
    return [(scenario["scenario"], document) for scenario in result["scenarios"] for document in scenario["documents"]]


def test_every_document_reaches_ready(runs):
    for name, document in documents(runs[0]):
        status = document["fingerprint"]["status"]
        assert document["status_code"] == 200, name
        assert (status["processing_status"], status["processing_stage"], status["processing_progress"]) == ("ready", "ready", 100), name
        assert status["processing_error"] is None
        assert document["fingerprint"]["counts"]["chunks"] > 0


def test_outputs_are_deterministic_across_runs(runs):
    assert benchmark.fingerprints_of(runs[0]) == benchmark.fingerprints_of(runs[1])


def test_outputs_match_committed_baseline(runs):
    baseline = json.loads(benchmark.BASELINE_PATH.read_text(encoding="utf-8"))
    current = benchmark.fingerprints_of(runs[0])
    for scenario, expected in baseline.items():
        if scenario in current:
            assert current[scenario] == expected, f"{scenario} output changed; a quality change needs explicit approval"


def test_simulated_providers_were_exercised_and_images_deduplicated_by_name(runs):
    calls = runs[0]["provider_calls"]
    assert calls["original_upload"] == calls["original_download"] > 0
    assert calls["datalab_submit"] > 0 and calls["voyage"] > 0 and calls["image_upload"] > 0
    fingerprints = benchmark.fingerprints_of(runs[0])
    duplicate = fingerprints["duplicate_images"]["duplicate_images"]
    assert duplicate["counts"]["mapped_assets"] == 30   # 10 complex pages x 3 identical images


def test_stage_timings_are_complete_additive_and_identifier_free(runs):
    required = {"upload.total", "queue_wait", "slot_acquisition", "materialize_original", "extraction",
                "extraction.page_classification", "checkpoint_save", "embeddings", "embeddings.provider",
                "embeddings.update", "finalize", "processing_active", "request_to_ready"}
    for name, document in documents(runs[0]):
        stages = document["stages"]
        assert required <= set(stages), (name, required - set(stages))
        assert not any(stage["failed"] for stage in stages.values()), name
        total = stages["request_to_ready"]["total_ms"]
        # Sequential top-level stages never exceed the end-to-end duration.
        assert document["top_level_stage_sum_ms"] <= total + 1.0, name
        assert total <= document["request_to_ready_wall_ms"] + 1.0, name
    mixed = dict(documents(runs[0]))["mixed"]["stages"]
    assert {"extraction.datalab", "extraction.datalab_provider", "extraction.images"} <= set(mixed)


def test_concurrent_uploads_record_queue_waiting(runs):
    queued = [document for name, document in documents(runs[0]) if name == "concurrent_queue"]
    waits = sorted(document["stages"]["queue_wait"]["total_ms"] for document in queued)
    assert len(queued) == 3
    # One document starts at once; the others wait for the single background slot.
    assert waits[-1] > 1000
