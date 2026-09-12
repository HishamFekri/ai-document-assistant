"""Batch 11: real SQL/ORM against disposable in-memory SQLite, no PostgreSQL.

Run: python -B tests/test_database_scalability.py. Providers/network are blocked.
PostgreSQL-specific statements and migration operations are compiled/mocked only.
"""

from datetime import datetime
import importlib
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine as memory_engine, event, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session
from pgvector.sqlalchemy import Vector
from fastapi import FastAPI, HTTPException, Response
from fastapi.testclient import TestClient

import test_resource_admission as harness


@compiles(JSONB, 'sqlite')
def sqlite_json(type_, compiler, **kw):
    return 'JSON'


@compiles(Vector, 'sqlite')
def sqlite_vector(type_, compiler, **kw):
    return 'TEXT'


class DatabaseScalabilityTests(unittest.TestCase):
    __test__ = __name__ == '__main__'

    @classmethod
    def setUpClass(cls):
        harness.ResourceAdmissionTests.setUpClass.__func__(cls)
        cls.models = importlib.import_module('app.database.models')
        cls.pagination = importlib.import_module('app.services.pagination')
        cls.queries = importlib.import_module('app.services.database_queries')
        cls.pool = importlib.import_module('app.database.pool_config')
        cls.rag = importlib.import_module('app.services.rag_service')
        cls.search = importlib.import_module('app.services.search_service')
        cls.title = importlib.import_module('app.services.chat_title_service')
        cls.context = importlib.import_module('app.services.summaries.summary_context_service')

    def setUp(self):
        harness.ResourceAdmissionTests.setUp(self)
        # Explicit, in-memory target. No application/test URL is consulted.
        from sqlalchemy.pool import StaticPool
        self.engine = memory_engine('sqlite+pysqlite:///:memory:', poolclass=StaticPool,
                                    connect_args={'check_same_thread': False})
        self.addCleanup(self.engine.dispose)
        self.database.Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.addCleanup(self.db.close)
        self.user = self.models.User(google_sub='local-1', email='one@example.invalid')
        other = self.models.User(google_sub='local-2', email='two@example.invalid')
        self.db.add_all([self.user, other]); self.db.commit()
        self.owner = NS(id=self.user.id)
        self.other_id = other.id
        self.now = datetime(2026, 9, 1, 12)

    def docs(self, count=73):
        rows = [self.models.Document(user_id=self.owner.id, filename=f'local-{i}.pdf',
                 created_at=self.now, file_type='pdf', pages_count=3) for i in range(count)]
        self.db.add_all(rows)
        self.db.add(self.models.Document(user_id=self.other_id, filename='private.pdf', created_at=self.now))
        self.db.commit()
        return rows

    def chats_fixture(self, count=13):
        documents = self.docs(2)
        chats = [self.models.Chat(user_id=self.owner.id, title=f'Chat {i}', created_at=self.now,
                 is_archived=i % 3 == 0, is_pinned=i % 2 == 0, documents=documents) for i in range(count)]
        self.db.add_all(chats); self.db.flush()
        self.db.add_all([self.models.Message(chat_id=c.id, role='user', content='question',
                         created_at=self.now, documents=documents) for c in chats])
        self.db.commit()
        return chats

    def client(self):
        app = FastAPI()
        app.include_router(self.documents.router)
        app.include_router(self.chats.router)
        app.dependency_overrides[self.database.get_db] = lambda: self.db
        app.dependency_overrides[self.chats.get_current_user] = lambda: self.owner
        return self.stack.enter_context(TestClient(app))

    def test_documents_default_bound_ties_older_rows_and_owner_scope(self):
        self.docs()
        client = self.client()
        first = client.get('/documents')
        self.assertEqual(first.status_code, 200)
        self.assertEqual(len(first.json()), 50)
        second = client.get('/documents', params={'cursor': first.headers['X-Next-Cursor']})
        all_rows = first.json() + second.json()
        self.assertEqual(len(all_rows), 73)
        self.assertEqual([r['id'] for r in all_rows], sorted([r['id'] for r in all_rows], reverse=True))
        self.assertTrue(all(r['user_id'] == self.owner.id for r in all_rows))
        self.assertNotIn('X-Next-Cursor', second.headers)

    def test_maximum_page_and_invalid_parameters(self):
        self.docs(101)
        client = self.client()
        self.assertEqual(len(client.get('/documents?limit=100').json()), 100)
        for query in ('limit=0', 'limit=-1', 'limit=101', 'limit=1.5', 'cursor=broken', 'cursor='):
            self.assertIn(client.get('/documents?' + query).status_code, (400, 422))

    def test_foreign_tampered_and_wrong_resource_cursors_are_rejected(self):
        self.docs()
        fields = [(self.models.Document.created_at, True), (self.models.Document.id, True)]
        for token in (
            self.pagination.encode_cursor(self.other_id, 'documents', [self.now, 50]),
            self.pagination.encode_cursor(self.owner.id, 'chats', [self.now, 50]),
            self.pagination.encode_cursor(self.owner.id, 'documents', [self.now, 50]) + 'x',
        ):
            with self.assertRaises(HTTPException):
                self.pagination.decode_cursor(token, self.owner.id, 'documents', fields)

    def test_chat_keyset_matches_approved_index_and_all_groups_reachable(self):
        chats = self.chats_fixture()
        expected = [c.id for c in sorted(chats, key=lambda c: (c.is_archived, not c.is_pinned, -c.id))]
        client = self.client(); ids = []; cursor = None
        while True:
            response = client.get('/chats', params={'limit': 4, **({'cursor': cursor} if cursor else {})})
            self.assertEqual(response.status_code, 200)
            ids.extend(c['id'] for c in response.json())
            cursor = response.headers.get('X-Next-Cursor')
            if not cursor: break
        self.assertEqual(ids, expected)
        self.assertEqual(len(ids), len(set(ids)))

    def test_message_pages_newest_window_chronological_display_and_older_access(self):
        chat = self.chats_fixture(1)[0]
        for i in range(8):
            self.db.add(self.models.Message(chat_id=chat.id, role='assistant', content=str(i), created_at=self.now))
        self.db.commit(); chat_id = chat.id
        client = self.client(); ids = []; cursor = None
        while True:
            response = client.get(f'/chats/{chat_id}/messages', params={'limit': 3, **({'cursor': cursor} if cursor else {})})
            page_ids = [r['id'] for r in response.json()]
            self.assertEqual(page_ids, sorted(page_ids))
            ids = page_ids + ids
            cursor = response.headers.get('X-Next-Cursor')
            if not cursor: break
        self.assertEqual(ids, list(range(1, 10)))

    def test_message_cursor_cannot_cross_chats_or_users(self):
        chats = self.chats_fixture(2)
        client = self.client()
        token = self.pagination.encode_cursor(self.owner.id, f'messages:{chats[0].id}', [self.now, 999])
        self.assertEqual(client.get(f'/chats/{chats[1].id}/messages', params={'cursor': token}).status_code, 400)
        chats[1].user_id = self.other_id; self.db.commit()
        self.assertEqual(client.get(f'/chats/{chats[1].id}/messages').status_code, 404)

    def test_existing_document_and_chat_ownership_tests_on_memory_database(self):
        import inspect
        client = self.client()
        del client.app.dependency_overrides[self.chats.get_current_user]
        for module_name in ('test_documents', 'test_chats'):
            module = importlib.import_module(module_name)
            original_create_user = module.create_user
            cases = [fn for name, fn in vars(module).items() if name.startswith('test_')]
            for case_number, case in enumerate(cases):
                with self.subTest(case=case.__name__), patch.object(module, 'create_user',
                    side_effect=lambda db, number: original_create_user(db, number + case_number * 10)):
                    kwargs = {'client': client}
                    if 'db' in inspect.signature(case).parameters: kwargs['db'] = self.db
                    case(**kwargs)

    def test_chat_and_message_serialization_have_bounded_relationship_queries(self):
        chats = self.chats_fixture()
        chat_id = chats[0].id
        self.db.expunge_all()
        sql = []
        def record(conn, cursor, statement, parameters, context, many):
            if statement.lstrip().upper().startswith('SELECT'): sql.append(statement)
        event.listen(self.engine, 'before_cursor_execute', record)
        self.addCleanup(event.remove, self.engine, 'before_cursor_execute', record)
        client = self.client()
        self.assertEqual(client.get('/chats').status_code, 200)
        self.assertEqual(len(sql), 2)  # page + all attachment relationships
        sql.clear(); self.db.expunge_all()
        self.assertEqual(client.get(f'/chats/{chat_id}/messages').status_code, 200)
        self.assertEqual(len(sql), 3)  # ownership + page + attachment relationships

    def test_chat_and_message_default_pages_are_bounded(self):
        chats = self.chats_fixture(55)
        chat_id = chats[0].id
        self.db.add_all([self.models.Message(chat_id=chat_id, role='assistant', content='local',
                         created_at=self.now) for _ in range(54)])
        self.db.commit()
        client = self.client()
        for path in ('/chats', f'/chats/{chat_id}/messages'):
            first = client.get(path)
            self.assertEqual(len(first.json()), 50)
            second = client.get(path, params={'cursor': first.headers['X-Next-Cursor']})
            self.assertEqual(len(second.json()), 5)
            self.assertNotIn('X-Next-Cursor', second.headers)

    def test_summary_and_asset_pages_keep_filtered_older_records_reachable(self):
        Summary = importlib.import_module('app.database.summary_models').DocumentSummary
        Asset = importlib.import_module('app.database.document_asset_models').DocumentAsset
        assets = importlib.import_module('app.routes.document_assets')
        chat = self.chats_fixture(1)[0]
        chat_id, document_id = chat.id, chat.documents[0].id
        self.db.add_all([Summary(chat_id=chat_id, document_id=document_id, mode='summary',
            version=i + 1, status='completed', created_at=self.now, is_selected=i == 0) for i in range(53)])
        self.db.add(Summary(chat_id=chat_id, document_id=document_id, mode='transcription',
            version=1, status='completed', created_at=self.now))
        self.db.add_all([Asset(document_id=document_id, asset_type='image') for _ in range(53)])
        self.db.add(Asset(document_id=document_id, asset_type='table'))
        self.db.commit()
        client = self.client()
        client.app.include_router(self.summaries.router)
        client.app.include_router(assets.router)
        for path, filters in [(f'/documents/{document_id}/summaries', {'chat_id': chat_id, 'mode': 'summary'}),
                              (f'/documents/{document_id}/assets', {'asset_type': 'image'})]:
            first = client.get(path, params=filters)
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(len(first.json()), 50)
            token = first.headers['X-Next-Cursor']
            second = client.get(path, params={**filters, 'cursor': token})
            self.assertEqual(len(second.json()), 3)
            self.assertEqual(len({r['id'] for r in first.json() + second.json()}), 53)
            self.assertNotIn('X-Next-Cursor', second.headers)
            changed_filter = {'mode': 'transcription'} if 'mode' in filters else {'asset_type': 'table'}
            self.assertEqual(client.get(path, params={**filters, **changed_filter, 'cursor': token}).status_code, 400)

    def test_queue_readiness_is_batched_and_not_lazily_reloaded_after_commits(self):
        chats = self.chats_fixture(4)
        document_id = chats[0].documents[0].id
        for document in chats[0].documents:
            document.processing_status = 'ready'
        for chat in chats:
            self.db.query(self.models.Message).filter_by(chat_id=chat.id).update({'status': 'waiting'})
        failed = self.models.Document(user_id=self.owner.id, filename='failed', processing_status='failed')
        pending = self.models.Document(user_id=self.owner.id, filename='pending', processing_status='processing')
        chats[1].documents.append(failed)
        chats[2].documents.append(pending)
        outsider = self.models.Chat(user_id=self.other_id, title='unrelated')
        self.db.add(outsider); self.db.flush()
        outside_message = self.models.Message(chat_id=outsider.id, role='user', status='waiting', content='private')
        self.db.add(outside_message)
        self.db.add(self.models.Message(chat_id=chats[0].id, role='assistant', status='waiting', content='exclude'))
        self.db.commit()
        expected = [chats[0].id, chats[3].id]
        self.db.expunge_all()
        selects = []
        def record(conn, cursor, statement, parameters, context, many):
            if statement.lstrip().upper().startswith('SELECT'): selects.append(statement)
        event.listen(self.engine, 'before_cursor_execute', record)
        self.addCleanup(event.remove, self.engine, 'before_cursor_execute', record)
        processed = []
        def process(db, message):
            processed.append(message.chat_id)
            message.status = 'completed'
            db.commit()  # expire identity map exactly as real processing does
        with patch.object(self.queue, 'SessionLocal', return_value=self.db), \
             patch.object(self.queue, 'process_waiting_message', side_effect=process):
            self.queue.process_waiting_messages_for_document(document_id)
        self.assertEqual(processed, expected)
        # Two discovery/status queries + three necessary per-message reads for
        # two ready messages and one failure. No attachment queries per message.
        self.assertEqual(len(selects), 5, selects)
        self.assertEqual(sum('processing_status' in sql for sql in selects), 1)

    def test_queue_keyset_continues_after_first_batch_changes_status(self):
        chat = self.chats_fixture(1)[0]
        chat_id, document_id = chat.id, chat.documents[0].id
        for document in chat.documents: document.processing_status = 'ready'
        self.db.add_all([self.models.Message(chat_id=chat_id, role='user', status='waiting',
            content='local', created_at=self.now) for _ in range(103)])
        self.db.commit()
        processed = []
        def process(db, message):
            processed.append(message.id)
            message.status = 'completed'
            db.commit()
        with patch.object(self.queue, 'SessionLocal', return_value=self.db), \
             patch.object(self.queue, 'process_waiting_message', side_effect=process):
            self.queue.process_waiting_messages_for_document(document_id)
        self.assertEqual(len(processed), 103)
        self.assertEqual(processed, sorted(set(processed)))

    def test_metadata_chunk_queries_do_not_select_vector_values(self):
        C = self.models.DocumentChunk
        sql = str(select(C).compile(dialect=postgresql.dialect()))
        self.assertNotIn('document_chunks.embedding', sql)
        self.assertIn('document_chunks.content', sql)
        distance = C.embedding.cosine_distance([1.] + [0.] * 511)
        sql = str(select(C, distance.label('distance')).order_by(distance).compile(dialect=postgresql.dialect()))
        self.assertIn('<=>', sql)
        self.assertNotIn('document_chunks.embedding,', sql)

    def test_bounded_scan_filters_before_limit_and_visits_late_matching_rows(self):
        self.docs(11)
        D = self.models.Document
        rows = list(self.queries.iter_query(self.db.query(D).filter(D.user_id == self.owner.id), [D.id], batch_size=3))
        self.assertEqual([d.id for d in rows], list(range(1, 12)))
        with self.assertRaises(ValueError): list(self.queries.iter_query(self.db.query(D), [D.id], 0))

    def test_exact_page_beyond_first_scan_preserves_batch12_filtering(self):
        document = self.docs(1)[0]
        for i in range(103):
            self.db.add(self.models.DocumentChunk(document_id=document.id, content=f'chunk {i}',
                content_type='formula' if i == 102 else 'text', location='Page 1',
                chunk_metadata={'page': 3 if i == 102 else 1}))
        self.db.commit()
        result = self.search.search_chunks_by_page(self.db, [document.id], 3, limit=1)
        self.assertEqual([r['chunk'].content for r in result], ['chunk 102'])
        self.assertEqual(result[0]['chunk'].content_type, 'formula')

    def test_summary_budget_does_not_read_remaining_batches(self):
        document = self.docs(1)[0]
        for i in range(103):
            self.db.add(self.models.DocumentChunk(document_id=document.id, content='x' * 500,
                content_type='text', location='Page 1', chunk_metadata={'page': 1}))
        self.db.commit(); doc_id = document.id
        sql = []
        def record(conn, cursor, statement, parameters, context, many): sql.append(statement)
        event.listen(self.engine, 'before_cursor_execute', record)
        self.addCleanup(event.remove, self.engine, 'before_cursor_execute', record)
        result = self.context.build_text_context(self.db, doc_id, max_chars=600)
        self.assertLessEqual(len(result), 600)
        self.assertEqual(len(sql), 1)
        self.assertIn('LIMIT', sql[0])
        self.assertNotIn('embedding', sql[0])

    def test_read_boundary_releases_transaction_and_preserves_pending_changes(self):
        self.db.execute(select(self.models.User.id)).all()
        self.assertTrue(self.db.in_transaction())
        self.assertTrue(self.queries.release_read_transaction(self.db))
        self.assertFalse(self.db.in_transaction())
        self.db.add(self.models.Chat(user_id=self.owner.id, title='unsaved'))
        self.assertFalse(self.queries.release_read_transaction(self.db))
        self.assertEqual(len(self.db.new), 1)

    def test_query_embedding_wait_has_no_ordinary_transaction(self):
        self.db.execute(select(self.models.User.id)).all()
        def embed(query):
            self.assertFalse(self.db.in_transaction()); return [1.] * 512
        with patch.object(self.search, 'inspect_embeddings', return_value=[NS(document_id=1, valid_chunks=1, missing_chunks=0)]), \
             patch.object(self.search, 'vector_candidates', return_value=[]), \
             patch.object(self.search, 'create_query_embedding', side_effect=embed):
            self.search.search_similar_chunks(self.db, 'question', [1])

    def test_answer_wait_has_no_ordinary_transaction(self):
        self.db.execute(select(self.models.User.id)).all()
        def generate(**kwargs):
            self.assertFalse(self.db.in_transaction()); return 'answer'
        with patch.object(self.rag, 'prepare_answer_context', return_value={
            'immediate_answer': None, 'context': 'source', 'conversation_history': [],
            'candidate_sources': [], 'mode': 'files_only'}), \
             patch.object(self.rag, 'generate_answer', side_effect=generate):
            self.assertEqual(self.rag.answer_question(self.db, 1, 'question')['answer'], 'answer')

    def test_title_wait_has_no_ordinary_transaction(self):
        docs = self.docs(1)
        client = MagicMock()
        def create(**kwargs):
            self.assertFalse(self.db.in_transaction())
            return NS(choices=[NS(message=NS(content='Synthetic title'))])
        client.chat.completions.create.side_effect = create
        with patch.object(self.title, 'get_deepseek_client', return_value=client):
            self.assertEqual(self.title.generate_ai_chat_title(self.db, 1, 'Explain this', docs), 'Synthetic title')

    def test_intent_wait_and_outer_stream_release_ordinary_transaction(self):
        chat = self.chats_fixture(1)[0]
        chat_id = chat.id
        for document in chat.documents: document.processing_status = 'ready'
        self.db.commit()
        observed = []
        def intent(**kwargs):
            observed.append((self.db.in_transaction(), all(isinstance(d, NS) for d in kwargs['documents'])))
            return {'action': 'chat', 'document_ids': [], 'needs_document_selection': False}
        with patch.object(self.chats, 'detect_chat_intent', side_effect=intent) as detect, \
             patch.object(self.chats, 'maybe_generate_chat_title', return_value=None), \
             patch.object(self.chats, 'answer_question', return_value={'answer': 'local', 'sources': [], 'mode': 'files_only'}):
            for route in (self.chats.ask_chat, self.chats.ask_chat_stream):
                route(chat_id=chat_id, data=self.chats.AskRequest(question='Explain this'),
                      db=self.db, current_user=self.owner, admission=MagicMock())
            self.assertEqual(detect.call_count, 2)
            self.assertEqual(observed, [(False, True), (False, True)])
            self.assertFalse(self.db.in_transaction())  # before consuming any stream

    def test_pool_defaults_and_reduced_capacity(self):
        options = self.pool.pool_options()
        self.assertEqual((options['pool_size'], options['max_overflow']), (5, 10))
        self.assertTrue(options['pool_pre_ping'])
        with patch.dict(os.environ, {'DB_POOL_SIZE': '2', 'DB_MAX_OVERFLOW': '0'}):
            self.assertEqual(self.pool.pool_options()['pool_size'], 2)

    def test_pool_rejects_unbounded_increased_and_nonsensical_settings(self):
        for name, value in [('DB_POOL_SIZE','0'), ('DB_POOL_SIZE','6'), ('DB_MAX_OVERFLOW','-1'),
                            ('DB_MAX_OVERFLOW','11'), ('DB_POOL_TIMEOUT','0'), ('DB_POOL_RECYCLE','0'),
                            ('DB_POOL_PRE_PING','maybe'), ('DB_POOL_SIZE','nan')]:
            with patch.dict(os.environ, {name: value}), self.assertRaises(ValueError):
                self.pool.pool_options()

    def test_five_index_migration_uses_autocommit_and_concurrent_ddl_only(self):
        path = Path(__file__).resolve().parents[1] / 'migrations/versions/b11d62a4c901_database_query_indexes.py'
        spec = importlib.util.spec_from_file_location('batch11_migration', path)
        migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
        op = MagicMock(); active = []
        op.get_context.return_value.autocommit_block.return_value.__enter__.side_effect = lambda: active.append(True)
        op.get_context.return_value.autocommit_block.return_value.__exit__.side_effect = lambda *args: active.pop() and False
        def create(*args, **kwargs): self.assertTrue(active); self.assertTrue(kwargs['postgresql_concurrently'])
        op.create_index.side_effect = create
        op.drop_index.side_effect = create
        with patch.object(migration, 'op', op):
            migration.upgrade(); migration.downgrade()  # mocks only, never SQL execution
        self.assertEqual(op.create_index.call_count, 5)
        self.assertEqual(op.drop_index.call_count, 5)
        self.assertEqual(migration.down_revision, '957d795d2816')
        self.assertNotIn('message_documents', [row[1] for row in migration.INDEXES])
        columns = [str(c) for c in migration.INDEXES[1][2]]
        self.assertEqual(columns, ['user_id', 'is_archived ASC', 'is_pinned DESC', 'created_at DESC', 'id DESC'])

    def test_concurrent_migration_sql_renders_outside_transactions_without_connection(self):
        from io import StringIO
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        path = Path(__file__).resolve().parents[1] / 'migrations/versions/b11d62a4c901_database_query_indexes.py'
        spec = importlib.util.spec_from_file_location('batch11_sql_render', path)
        migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
        for direction in ('upgrade', 'downgrade'):
            output = StringIO()
            # as_sql renders text to memory. No engine, URL, connection or
            # Alembic environment is loaded and no SQL is executed.
            context = MigrationContext.configure(dialect_name='postgresql', opts={
                'as_sql': True, 'output_buffer': output, 'transaction_per_migration': True,
            })
            with patch.object(migration, 'op', Operations(context)):
                with context.begin_transaction(_per_migration=True):
                    getattr(migration, direction)()
            sql = output.getvalue()
            command = 'CREATE INDEX CONCURRENTLY' if direction == 'upgrade' else 'DROP INDEX CONCURRENTLY IF EXISTS'
            self.assertEqual(sql.count(command), 5)
            ddl_start = sql.index(command)
            ddl_end = sql.rindex(command)
            self.assertLess(sql.index('COMMIT;'), ddl_start)
            self.assertGreater(sql.rindex('BEGIN;'), ddl_end)
            self.assertNotIn('BEGIN;', sql[ddl_start:ddl_end])
            for name, table, columns in migration.INDEXES:
                self.assertIn(name, sql)
                if direction == 'upgrade':
                    self.assertIn(f'ON {table} ({", ".join(str(c) for c in columns)})', sql)


if __name__ == '__main__':
    unittest.main(verbosity=2)
