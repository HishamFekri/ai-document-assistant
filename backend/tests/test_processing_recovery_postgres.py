"""Automatic recovery of interrupted background processing, ONLY through conftest's
isolated TEST_DATABASE_URL fixture (loopback PostgreSQL with pgvector).

Real advisory locks and transactions; the end-to-end cases run the real upload
route and processing with providers simulated by scripts/benchmark_processing.py
(network refused). Run in the same pytest process as the benchmark test or its own.
"""

import importlib.util
import threading
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import text


BACKEND_DIR = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("benchmark_processing", BACKEND_DIR / "scripts" / "benchmark_processing.py")
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)
# Applied at collection, before the database fixture imports the application.
benchmark.configure_environment()


class Interrupted(BaseException):
    """Stands in for the process dying: escapes every `except Exception`."""


@pytest.fixture
def recovery(test_database):
    from app.services import processing_recovery
    from app.services.recovery_settings import RecoverySettings
    settings = RecoverySettings(enabled=True, stale_seconds=300, interval_seconds=60, max_age_seconds=86400)
    return processing_recovery, settings, test_database


def insert_document(engine, *, status="processing", stage="analyzing_document", age_seconds=600,
                    attempts=0, label=None):
    with engine.begin() as connection:
        user_id = connection.execute(text(
            "INSERT INTO users (google_sub, email, name) VALUES (gen_random_uuid()::text, "
            "gen_random_uuid()::text || '@example.invalid', 'Owner') RETURNING id")).scalar()
        return connection.execute(text(
            "INSERT INTO documents (user_id, filename, file_type, file_path, processing_status, processing_stage, "
            "processing_progress, processing_recovery_attempts, processing_updated_at) "
            "VALUES (:user, :name, 'pdf', 'missing.pdf', :status, :stage, 20, :attempts, "
            "now() - make_interval(secs => :age)) RETURNING id"),
            {"user": user_id, "name": label or "doc.pdf", "status": status, "stage": stage,
             "attempts": attempts, "age": age_seconds}).scalar()


def row(engine, document_id):
    with engine.connect() as connection:
        return connection.execute(text(
            "SELECT processing_status, processing_stage, processing_progress, processing_error, "
            "processing_recovery_attempts, now() - processing_updated_at < interval '5 seconds' AS fresh "
            "FROM documents WHERE id = :id"), {"id": document_id}).mappings().one()


def scan(recovery, dispatched=None):
    module, settings, database = recovery
    dispatched = [] if dispatched is None else dispatched
    outcomes = module.scan_once(database.SessionLocal, settings, dispatch=dispatched.append)
    return outcomes, dispatched


# ---- Detection and duplicate prevention ---------------------------------------------

def test_interrupted_document_is_recovered_once(recovery):
    engine = recovery[2].engine
    document_id = insert_document(engine)
    outcomes, dispatched = scan(recovery)
    assert dispatched == [document_id] and outcomes["dispatch"] == 1
    state = row(engine, document_id)
    assert (state["processing_status"], state["processing_stage"], state["processing_progress"]) == ("processing", "queued", 5)
    assert state["processing_recovery_attempts"] == 1 and state["fresh"]
    # The bumped timestamp makes it ineligible for the next scans.
    assert scan(recovery)[1] == []


def test_job_holding_its_lock_is_never_recovered_however_stale(recovery):
    # A long Datalab call: the job holds the advisory lock; its timestamp is old.
    engine = recovery[2].engine
    from app.services.document_processing_claim import claim_document_processing
    document_id = insert_document(engine, age_seconds=3000)
    with claim_document_processing(document_id) as claim:
        assert claim is not None
        outcomes, dispatched = scan(recovery)
    assert dispatched == [] and outcomes["active"] == 1
    assert row(engine, document_id)["processing_recovery_attempts"] == 0
    assert scan(recovery)[1] == [document_id]               # recoverable once the owner is gone


def test_recently_refreshed_queued_and_terminal_documents_are_left_alone(recovery):
    engine = recovery[2].engine
    queued = insert_document(engine, stage="queued", age_seconds=60)            # waiting job refreshed 60s ago
    fresh = insert_document(engine, age_seconds=299)
    ready = insert_document(engine, status="ready", stage="ready", age_seconds=900)
    permanent = insert_document(engine, status="failed", stage="permanent_failure", age_seconds=900)
    exhausted = insert_document(engine, status="failed", stage="retry_exhausted", age_seconds=900)
    dispatch_failed = insert_document(engine, status="failed", stage="dispatch_failed", age_seconds=900)
    ancient = insert_document(engine, age_seconds=2 * 86400)                    # beyond max age: manual flow
    outcomes, dispatched = scan(recovery)
    assert dispatched == [] and sum(outcomes.values()) == 0
    for document_id in (queued, fresh, ready, permanent, exhausted, dispatch_failed, ancient):
        assert row(recovery[2].engine, document_id)["processing_recovery_attempts"] == 0


def test_failed_attempt_whose_retry_loop_died_is_recovered(recovery):
    document_id = insert_document(recovery[2].engine, status="failed", stage="retryable_failure")
    assert scan(recovery)[1] == [document_id]
    assert row(recovery[2].engine, document_id)["processing_status"] == "processing"


def test_concurrent_scanners_dispatch_each_document_exactly_once(recovery):
    engine = recovery[2].engine
    documents = [insert_document(engine, label=f"d{index}.pdf") for index in range(6)]
    dispatched, lock = [], threading.Lock()
    barrier = threading.Barrier(8)

    def scanner():
        mine = []
        barrier.wait()
        scan(recovery, mine)
        with lock:
            dispatched.extend(mine)
    threads = [threading.Thread(target=scanner) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert sorted(dispatched) == sorted(documents)
    assert all(row(engine, document_id)["processing_recovery_attempts"] == 1 for document_id in documents)


def test_overlapping_instance_that_listed_candidates_earlier_skips(recovery):
    # Instance B lists candidates, instance A recovers them, then B gets the lock.
    module, settings, database = recovery
    document_id = insert_document(database.engine)
    with database.SessionLocal() as db:
        stale_list = module.recovery_candidates(db, settings)
    assert stale_list == [document_id]
    assert scan(recovery)[1] == [document_id]                                   # instance A
    assert module.recover_document(database.SessionLocal, document_id, settings) == "skipped"   # instance B


def test_document_completed_between_listing_and_recovery_is_skipped(recovery):
    module, settings, database = recovery
    document_id = insert_document(database.engine)
    with database.SessionLocal() as db:
        assert module.recovery_candidates(db, settings) == [document_id]
    with database.engine.begin() as connection:
        connection.execute(text("UPDATE documents SET processing_status = 'ready', processing_stage = 'ready' "
                                "WHERE id = :id"), {"id": document_id})
    assert module.recover_document(database.SessionLocal, document_id, settings) == "skipped"
    assert row(database.engine, document_id)["processing_status"] == "ready"


def test_migration_gives_existing_documents_a_zero_recovery_count(recovery):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect
    database = recovery[2]
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    database.engine.dispose()
    command.downgrade(config, "5c1e8f2a9b47")
    try:
        with database.engine.begin() as connection:
            user_id = connection.execute(text(
                "INSERT INTO users (google_sub, email, name) VALUES ('legacy-recovery', "
                "'legacy-recovery@example.invalid', 'Owner') RETURNING id")).scalar()
            legacy_id = connection.execute(text(
                "INSERT INTO documents (user_id, filename, file_type, processing_status, processing_stage, "
                "processing_progress) VALUES (:user, 'legacy.pdf', 'pdf', 'ready', 'ready', 100) RETURNING id"),
                {"user": user_id}).scalar()
    finally:
        database.engine.dispose()
        command.upgrade(config, "head")
    with database.engine.connect() as connection:
        column = {c["name"]: c for c in inspect(connection).get_columns("documents")}["processing_recovery_attempts"]
        assert column["nullable"] is False and str(column["type"]) == "INTEGER" and "0" in str(column["default"])
        assert connection.scalar(text("SELECT processing_recovery_attempts FROM documents WHERE id = :id"),
                                 {"id": legacy_id}) == 0
    database.engine.dispose()
    command.downgrade(config, "5c1e8f2a9b47")                 # clean downgrade with data present
    database.engine.dispose()
    command.upgrade(config, "head")


# ---- Recovery cap ----------------------------------------------------------------------

def test_recovery_is_capped_and_then_fails_with_a_retryable_message(recovery):
    module, settings, database = recovery
    document_id = insert_document(database.engine, attempts=module.MAX_AUTOMATIC_RECOVERIES)
    outcomes, dispatched = scan(recovery)
    assert dispatched == [] and outcomes["exhausted"] == 1
    state = row(database.engine, document_id)
    assert (state["processing_status"], state["processing_stage"]) == ("failed", "retry_exhausted")
    assert state["processing_error"] == module.RECOVERY_EXHAUSTED_ERROR
    assert "Please retry" in state["processing_error"]
    assert scan(recovery)[1] == []                                              # terminal for the scanner


def test_two_recoveries_then_exhaustion(recovery):
    module, settings, database = recovery
    document_id = insert_document(database.engine)
    for expected in (1, 2):
        assert scan(recovery)[1] == [document_id]
        assert row(database.engine, document_id)["processing_recovery_attempts"] == expected
        with database.engine.begin() as connection:   # the recovered job is lost again
            connection.execute(text("UPDATE documents SET processing_updated_at = now() - interval '10 minutes' "
                                    "WHERE id = :id"), {"id": document_id})
    assert scan(recovery)[0]["exhausted"] == 1
    assert row(database.engine, document_id)["processing_stage"] == "retry_exhausted"


def test_explicit_retry_resets_the_recovery_budget(recovery):
    module, settings, database = recovery
    document_id = insert_document(database.engine, status="failed", stage="retry_exhausted",
                                  attempts=module.MAX_AUTOMATIC_RECOVERIES)
    with database.engine.connect() as connection:
        user_id = connection.scalar(text("SELECT user_id FROM documents WHERE id = :id"), {"id": document_id})
    from fastapi.testclient import TestClient
    from app.database.models import User
    from app.services.auth_service import create_access_token
    import main
    token = create_access_token(User(id=user_id, email="owner@example.invalid"))
    with patch("app.routes.documents.enqueue_document_processing") as enqueue, TestClient(main.app) as client:
        response = client.post(f"/documents/{document_id}/retry", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    enqueue.assert_called_once()
    state = row(database.engine, document_id)
    assert (state["processing_status"], state["processing_recovery_attempts"]) == ("processing", 0)


def test_scanner_survives_database_errors_and_stops_promptly(recovery):
    module, settings, _ = recovery
    calls = []

    def failing(settings=None):
        calls.append(1)
        raise RuntimeError("database unavailable")
    fast = type(settings)(enabled=True, stale_seconds=300, interval_seconds=10, max_age_seconds=86400)
    with patch.object(module, "log_exception"):
        scanner = module.RecoveryScanner(fast, initial_delay=0.01, scan=failing).start()
        deadline = __import__("time").monotonic() + 5
        while not calls and __import__("time").monotonic() < deadline:
            __import__("time").sleep(0.01)
        assert scanner.stop(timeout=2)                   # wakes from its 10s wait immediately
    assert calls == [1]


# ---- End to end: real upload route and processing, simulated providers -----------------

@contextmanager
def application():
    providers = benchmark.SimulatedProviders(benchmark.LATENCY_PROFILES["zero"])
    with benchmark.simulated_application(providers) as app:
        yield app, providers


def upload_document(app, providers, engine, spec, index):
    pdf = benchmark.build_pdf(spec)
    providers.register(benchmark.hashlib.sha256(pdf).hexdigest(), spec)
    token = benchmark.create_user(engine, 900 + index)
    return benchmark.upload(app, token, spec, pdf)


def upload_then_die_during(app, providers, engine, spec, index, target, name):
    """Upload (dispatch suppressed), then run the real job until `target.name` is reached
    and the process "dies" there: the exception escapes every handler, as death would."""
    with patch("app.routes.documents.enqueue_document_processing"):
        result = upload_document(app, providers, engine, spec, index)
    from app.services.document_processing_service import process_document
    with patch.object(target, name, side_effect=Interrupted()):
        with pytest.raises(Interrupted):
            process_document(result.document_id)
    return result.document_id


def age(engine, document_id):
    with engine.begin() as connection:
        connection.execute(text("UPDATE documents SET processing_updated_at = now() - interval '10 minutes' "
                                "WHERE id = :id"), {"id": document_id})


def recover_synchronously(recovery):
    # The real recovery path (_run_recovered -> _run_document_processing), run inline.
    module, settings, database = recovery
    return module.scan_once(database.SessionLocal, settings, dispatch=module._run_recovered)


def test_job_lost_before_it_started_is_recovered_end_to_end(recovery):
    engine = recovery[2].engine
    spec = benchmark.DocumentSpec("lost_queued", 3, 2, 1)
    with application() as (app, providers):
        # The process dies right after the upload committed: the job never runs.
        with patch("app.routes.documents.enqueue_document_processing"):
            result = upload_document(app, providers, engine, spec, 1)
        assert row(engine, result.document_id)["processing_status"] == "processing"
        age(engine, result.document_id)
        assert recover_synchronously(recovery)["dispatch"] == 1
    state = row(engine, result.document_id)
    assert (state["processing_status"], state["processing_progress"]) == ("ready", 100)
    assert providers.calls["datalab_submit"] == 1


def test_interrupted_extraction_is_recovered_and_retries_extraction(recovery):
    engine = recovery[2].engine
    spec = benchmark.DocumentSpec("lost_extraction", 3, 2, 1)
    import app.services.hybrid_pdf_service as hybrid
    original = hybrid.save_datalab_images
    with application() as (app, providers):
        # Dies after Datalab returned, while uploading images (before any checkpoint).
        document_id = upload_then_die_during(app, providers, engine, spec, 2, hybrid, "save_datalab_images")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM document_chunks WHERE document_id = :id"),
                                     {"id": document_id}) == 0
        assert row(engine, document_id)["processing_status"] == "processing"
        age(engine, document_id)
        assert hybrid.save_datalab_images is original
        assert recover_synchronously(recovery)["dispatch"] == 1
    assert row(engine, document_id)["processing_status"] == "ready"
    # No checkpoint existed, so extraction (and Datalab) ran again: the one case
    # recovery cannot avoid repeating paid work.
    assert providers.calls["datalab_submit"] == 2


def test_interruption_after_the_checkpoint_resumes_without_repeating_extraction(recovery):
    engine = recovery[2].engine
    spec = benchmark.DocumentSpec("lost_embeddings", 3, 2, 1)
    import app.services.document_processing_service as processing
    with application() as (app, providers):
        # Dies while creating embeddings, after the chunk checkpoint committed.
        document_id = upload_then_die_during(app, providers, engine, spec, 3, processing, "create_passage_embeddings")
        with engine.connect() as connection:
            chunks_before = connection.execute(text(
                "SELECT id, content FROM document_chunks WHERE document_id = :id ORDER BY id"), {"id": document_id}).all()
        assert chunks_before and row(engine, document_id)["processing_stage"] == "creating_embeddings"
        submits = providers.calls["datalab_submit"]
        age(engine, document_id)
        assert recover_synchronously(recovery)["dispatch"] == 1
        with engine.connect() as connection:
            chunks_after = connection.execute(text(
                "SELECT id, content FROM document_chunks WHERE document_id = :id ORDER BY id"), {"id": document_id}).all()
            missing = connection.scalar(text("SELECT missing_image_count FROM documents WHERE id = :id"),
                                        {"id": document_id})
    assert row(engine, document_id)["processing_status"] == "ready"
    assert providers.calls["datalab_submit"] == submits                     # no repeated Datalab work
    assert chunks_after == chunks_before                                    # same checkpoint, same IDs
    assert missing == 0


def test_completed_document_is_never_reprocessed(recovery):
    engine = recovery[2].engine
    spec = benchmark.DocumentSpec("completed", 3, 2, 1)
    with application() as (app, providers):
        result = upload_document(app, providers, engine, spec, 4)
        assert row(engine, result.document_id)["processing_status"] == "ready"
        age(engine, result.document_id)
        calls = dict(providers.calls)
        assert sum(recover_synchronously(recovery).values()) == 0
        assert providers.calls == calls
