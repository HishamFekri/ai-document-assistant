"""Live checks ONLY through Batch 1's isolated TEST_DATABASE_URL fixture.

No application URL fallback, provider calls, workers, or source files. Run only
with an explicitly configured safe local test target. Separate physical sessions
exercise the same PostgreSQL synchronization used across API processes.
"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from sqlalchemy import insert, select
from sqlalchemy.orm import Session


@pytest.fixture
def summary_context(test_database):
    from app.database.models import Chat, Document, User
    import app.database.document_asset_models  # Register relationship targets.
    from app.database.summary_models import DocumentSummary

    with test_database.engine.begin() as connection:
        user_id = connection.scalar(insert(User.__table__).values(
            google_sub="summary-concurrency-test", email="summary-test@example.invalid",
        ).returning(User.id))
        chat_id = connection.scalar(insert(Chat.__table__).values(
            user_id=user_id, title="Synthetic summary test",
        ).returning(Chat.id))
        document_id = connection.scalar(insert(Document.__table__).values(
            user_id=user_id, filename="synthetic.txt", processing_status="ready",
        ).returning(Document.id))
    return chat_id, document_id, "summary"


def test_postgres_concurrent_starts_have_one_owner_and_one_paid_call(test_database, summary_context):
    from app.services.summaries.summary_claim import summary_generation_session, SummaryGenerationBusy
    from app.services.summaries.summary_service import (
        create_summary_record, mark_summary_generating, mark_summary_completed,
    )

    entered, release = Event(), Event()
    calls = []
    def start():
        with summary_generation_session(*summary_context) as db:
            row = create_summary_record(db, *summary_context)
            assert mark_summary_generating(db, row)
            assert not db.in_transaction()
            assert not db.get_bind().in_transaction()
            calls.append(row.id)  # Synthetic provider boundary.
            entered.set()
            assert release.wait(5)
            return mark_summary_completed(db, row, {"title": "Synthetic", "sections": []})

    with ThreadPoolExecutor(max_workers=2) as executor:
        owner = executor.submit(start)
        try:
            assert entered.wait(5)
            duplicate = executor.submit(start)
            with pytest.raises(SummaryGenerationBusy):
                duplicate.result(timeout=5)
        finally:
            release.set()
        result = owner.result(timeout=5)
    assert len(calls) == 1
    assert result.version == 1 and result.status == "completed"
    with summary_generation_session(*summary_context) as db:
        successor = create_summary_record(db, *summary_context)
        assert successor.version == 2


def test_postgres_version_allocator_serializes_two_physical_sessions(test_database, summary_context):
    from app.database.summary_models import DocumentSummary
    from app.services.summaries.summary_service import get_next_summary_version

    barrier = Barrier(2)
    def allocate():
        with Session(test_database.engine) as db:
            barrier.wait(timeout=5)
            version = get_next_summary_version(db, *summary_context)
            db.execute(insert(DocumentSummary.__table__).values(
                chat_id=summary_context[0], document_id=summary_context[1],
                mode=summary_context[2], version=version, status="failed",
            ))
            db.commit()
            return version
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [executor.submit(allocate) for _ in range(2)]
        assert sorted(result.result(timeout=10) for result in results) == [1, 2]


def test_postgres_cancel_during_provider_is_durable_and_does_not_release_owner(test_database, summary_context):
    from app.services.summaries.summary_claim import summary_generation_session, SummaryGenerationBusy
    from app.services.summaries.summary_service import (
        create_summary_record, mark_summary_generating, mark_summary_cancelled,
        mark_summary_completed,
    )

    with summary_generation_session(*summary_context) as owner:
        row = create_summary_record(owner, *summary_context)
        assert mark_summary_generating(owner, row)
        def cancel():
            with Session(test_database.engine) as db:
                return mark_summary_cancelled(db, row)
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(cancel).result(timeout=5).status == "cancelled"
        assert not owner.in_transaction()
        with pytest.raises(SummaryGenerationBusy):
            with summary_generation_session(*summary_context):
                pytest.fail("Cancelled owner still has a provider in flight")
        late = mark_summary_completed(owner, row, {"title": "Late", "sections": []})
        assert late.status == "cancelled" and late.content is None


def test_postgres_cleanup_preserves_cancelled_inflight_and_active_rows(test_database, summary_context):
    from app.database.summary_models import DocumentSummary
    from app.services.summaries.summary_claim import summary_generation_session
    from app.services.summaries.summary_service import cleanup_old_summaries

    with Session(test_database.engine) as db:
        ids = []
        for version, status in enumerate(("cancelled", "pending", "generating", "completed"), 1):
            ids.append(db.scalar(insert(DocumentSummary.__table__).values(
                chat_id=summary_context[0], document_id=summary_context[1],
                mode=summary_context[2], version=version, status=status,
                is_selected=version == 4,
            ).returning(DocumentSummary.id)))
        db.commit()
    with summary_generation_session(*summary_context):
        with Session(test_database.engine) as cleaner:
            cleanup_old_summaries(cleaner, *summary_context, ids[-1])
            cleaner.commit()
            assert set(cleaner.scalars(select(DocumentSummary.id))) == set(ids)
    with Session(test_database.engine) as cleaner:
        cleanup_old_summaries(cleaner, *summary_context, ids[-1])
        cleaner.commit()
        assert set(cleaner.scalars(select(DocumentSummary.id))) == set(ids[1:])


def test_postgres_claim_released_on_failure_and_modes_are_independent(test_database, summary_context):
    from app.services.summaries.summary_claim import summary_generation_session

    with pytest.raises(ValueError):
        with summary_generation_session(*summary_context):
            with summary_generation_session(*summary_context[:2], "transcription"):
                pass
            raise ValueError("Synthetic provider failure")
    with summary_generation_session(*summary_context) as successor:
        assert not successor.in_transaction()


def test_postgres_lost_claim_cannot_reconnect_for_late_write(test_database, summary_context):
    from app.services.summaries.summary_claim import summary_generation_session, SummaryClaimLost
    from app.services.summaries.summary_service import create_summary_record, mark_summary_failed

    with summary_generation_session(*summary_context) as owner:
        row = create_summary_record(owner, *summary_context)
        owner.get_bind().invalidate()
        with pytest.raises(SummaryClaimLost):
            mark_summary_failed(owner, row, "Synthetic failure")
