"""Prepared integration checks; NOT run as part of offline Batch 12 validation.

Requires Batch 1's isolated local TEST_DATABASE_URL fixture. That fixture creates
an ephemeral database and runs migrations: execute only under separate approval
for that test lifecycle. Never point this suite at staging/production directly.
No provider calls or real document data. Printed metrics are synthetic only.
"""

import json
import math
from time import perf_counter

from sqlalchemy import insert, text
from sqlalchemy.orm import Session


def test_filtered_cosine_candidates_and_exact_hnsw_comparison(test_database):
    from app.database.models import Document, DocumentChunk, User
    from app.services.search_service import vector_candidates
    from app.services.vector_retrieval_validation import compare_candidate_ids

    with Session(test_database.engine) as db:
        user = db.scalar(insert(User).values(google_sub='vector-test', email='vector@example.invalid').returning(User.id))
        other_user = db.scalar(insert(User).values(google_sub='vector-other', email='other@example.invalid').returning(User.id))
        documents = [db.scalar(insert(Document).values(user_id=owner, filename='synthetic.pdf',
                     processing_status='ready').returning(Document.id)) for owner in (user, user, other_user)]
        records = []
        types = ['text', 'table', 'formula', 'equation', 'code', 'image']
        for i in range(96):
            angle = .01 * (i + 1)
            doc = documents[i % 3]
            kind = types[(i // 3) % len(types)]
            chunk_id = db.scalar(insert(DocumentChunk).values(
                document_id=doc, content=f'Synthetic unique chunk {i}', content_type=kind,
                location=f'Page {i + 1}', chunk_metadata={'page': i + 1},
                embedding=[math.cos(angle), math.sin(angle)] + [0.] * 510,
            ).returning(DocumentChunk.id))
            records.append((chunk_id, doc, kind))
        db.flush()
        version = db.execute(text("SELECT extversion FROM pg_extension WHERE extname='vector'")).scalar_one()
        postgres = db.execute(text('SHOW server_version')).scalar_one()
        db.execute(text('ANALYZE document_chunks'))
        vector = [1.] + [0.] * 511
        selected = documents[:2]
        selected_types = ['text', 'table', 'formula', 'equation', 'code']
        expected = [id for id, doc, kind in records if doc in selected and kind in selected_types]
        reports = {'postgres': postgres, 'pgvector': version, 'comparisons': []}
        for scope_name, scope, content_types in (
            ('unfiltered', documents, types), ('filtered', selected, selected_types),
        ):
            measurements = {}
            for exact in (True, False):
                # Local test transaction only. No application HNSW settings changed.
                db.execute(text('SET LOCAL enable_seqscan = off'))
                start = perf_counter()
                rows = vector_candidates(db, vector, scope, content_types, 40, exact=exact)
                elapsed = (perf_counter() - start) * 1000
                ids = [chunk.id for chunk, _ in rows]
                assert all(chunk.document_id in scope and chunk.content_type in content_types for chunk, _ in rows)
                measurements[exact] = (ids, elapsed)
            if scope_name == 'filtered':
                assert measurements[True][0] == expected[:40]
            else:
                assert measurements[True][0] == [r[0] for r in records[:40]]
            for k in (8, 40):
                report = compare_candidate_ids(measurements[True][0], measurements[False][0], k)
                report.update(scope=scope_name, exact_ms=measurements[True][1], ann_ms=measurements[False][1])
                reports['comparisons'].append(report)

        # Inspect actual equivalent SQL plans. An ANN label alone proves nothing.
        embedding = '[' + ','.join(map(str, vector)) + ']'
        plans = {}
        for exact in (True, False):
            for filtered in (True, False):
                where = ('document_id = ANY(:ids) AND content_type = ANY(:types) AND '
                         'embedding IS NOT NULL AND vector_dims(embedding) = 512 AND '
                         "vector_norm(embedding) > 0 AND content ~ '[^[:space:]]'")
                order = '(embedding <=> CAST(:vector AS vector))' + (' + 0.0' if exact else '')
                plan = db.execute(text('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) SELECT id FROM document_chunks '
                                       f'WHERE {where} ORDER BY {order} LIMIT 40'),
                                  {'ids': selected if filtered else documents,
                                   'types': selected_types if filtered else types, 'vector': embedding}).scalar_one()
                serialized = json.dumps(plan)
                if exact:
                    assert 'ix_document_chunks_embedding_hnsw' not in serialized
                plans[f'exact={exact},filtered={filtered}'] = plan
        reports['plans'] = plans
        reports['ann_index_observed'] = {
            key: 'ix_document_chunks_embedding_hnsw' in json.dumps(plan)
            for key, plan in plans.items() if key.startswith('exact=False')
        }
        print(json.dumps(reports, indent=2))
        # Roll back synthetic rows; Batch 1 independently drops its generated DB.
        db.rollback()
