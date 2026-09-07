"""Opt-in live checks using Batch 1's unchanged database isolation guards.

Separate physical sessions exercise per-user admission across resources. No
providers or workers are invoked. Never run against an application database.
"""

from contextlib import ExitStack
from dataclasses import replace

import pytest
from sqlalchemy import insert


@pytest.fixture
def admission(test_database, monkeypatch):
    from app.services import resource_admission
    from app.services.resource_limits import resource_limits
    settings = replace(resource_limits(), concurrency={"chat": 2, "search": 2, "summary": 1, "processing": 1})
    monkeypatch.setattr(resource_admission, "resource_limits", lambda: settings)
    return resource_admission


def test_postgres_chat_capacity_shared_by_sessions_and_users_isolated(admission):
    with ExitStack() as scope:
        for _ in range(2):
            permit = admission.acquire_permit(101, "chat")
            scope.callback(permit.release)
            assert not permit.connection.in_transaction()
        with pytest.raises(admission.ResourceRejected) as error:
            admission.acquire_permit(101, "chat")
        assert error.value.status_code == 429
        other = admission.acquire_permit(102, "chat")
        scope.callback(other.release)
    with admission.user_operation(101, "chat", rate=False):
        pass


@pytest.mark.parametrize("category", ["summary", "processing", "upload_quota"])
def test_postgres_one_owner_across_distinct_resources(admission, category):
    with admission.user_operation(101, category, rate=False):
        with pytest.raises(admission.ResourceRejected):
            admission.acquire_permit(101, category)
        with admission.user_operation(102, category, rate=False):
            pass
    with admission.user_operation(101, category, rate=False):
        pass


def test_postgres_exception_and_invalidated_session_release_capacity(admission):
    with pytest.raises(ValueError):
        with admission.user_operation(101, "summary", rate=False):
            raise ValueError("Synthetic failure")
    permit = admission.acquire_permit(101, "summary")
    permit.connection.invalidate()
    with pytest.raises(admission.AdmissionUnavailable):
        permit.check()
    permit.release()
    with admission.user_operation(101, "summary", rate=False):
        pass


def test_postgres_borrowed_document_claim_retains_context_after_user_release(admission):
    from app.services.document_processing_claim import claim_document_processing
    with claim_document_processing(501) as claim:
        with admission.user_operation(101, "processing", rate=False, connection=claim.connection):
            assert not claim.connection.in_transaction()
            with pytest.raises(admission.ResourceRejected):
                admission.acquire_permit(101, "processing")
        assert not claim.connection.closed
        with claim_document_processing(501) as duplicate:
            assert duplicate is None
    with claim_document_processing(501) as successor:
        assert successor is not None


def test_postgres_quota_reservation_sees_committed_upload_from_other_session(test_database, admission, monkeypatch):
    from app.database.models import Document, User
    from app.services import upload_quota_service as quota
    settings = replace(quota.resource_limits(), max_documents=1)
    monkeypatch.setattr(quota, "resource_limits", lambda: settings)
    with test_database.engine.begin() as connection:
        user_id = connection.scalar(insert(User.__table__).values(
            google_sub="resource-test", email="resource-test@example.invalid",
        ).returning(User.id))
    with quota.upload_quota_session(user_id, incoming_bytes=1) as db:
        with pytest.raises(admission.ResourceRejected):
            with quota.upload_quota_session(user_id, incoming_bytes=1):
                pytest.fail("Competing reservation admitted")
        db.execute(insert(Document.__table__).values(
            user_id=user_id, filename="synthetic.txt", processing_status="ready",
        ))
        db.commit()
    with pytest.raises(admission.ResourceRejected) as error:
        with quota.upload_quota_session(user_id, incoming_bytes=1):
            pytest.fail("Committed count quota bypassed")
    assert error.value.code == "document_quota"
