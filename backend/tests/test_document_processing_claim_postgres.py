"""Live claim checks, ONLY through conftest's isolated TEST_DATABASE_URL fixture.

No provider or worker is started. Do not run this file with application credentials
or bypass the fixture. Tests were prepared but not run without TEST_DATABASE_URL.
"""

from concurrent.futures import ThreadPoolExecutor

import pytest


def test_only_one_postgres_session_can_claim_document(test_database):
    from app.services.document_processing_claim import claim_document_processing

    def compete():
        with claim_document_processing(123) as claim:
            return claim is not None

    with claim_document_processing(123) as owner:
        assert owner is not None
        assert not owner.connection.in_transaction()
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(compete).result(timeout=5) is False
        with owner.session() as db:
            from sqlalchemy import text
            assert db.scalar(text("SELECT 1")) == 1
        assert not owner.connection.in_transaction()
    assert compete() is True


def test_postgres_claim_releases_after_processing_exception(test_database):
    from app.services.document_processing_claim import claim_document_processing

    with pytest.raises(ValueError):
        with claim_document_processing(124) as owner:
            assert owner is not None
            raise ValueError("Synthetic processing failure")
    with claim_document_processing(124) as successor:
        assert successor is not None


def test_invalidated_postgres_claim_cannot_start_another_transaction(test_database):
    from app.services.document_processing_claim import claim_document_processing
    from app.services.document_processing_errors import ProcessingClaimLost

    with claim_document_processing(125) as owner:
        owner.connection.invalidate()
        with pytest.raises(ProcessingClaimLost):
            with owner.session():
                pytest.fail("Invalidated claim yielded a session")
