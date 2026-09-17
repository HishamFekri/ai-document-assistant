"""Explicit maintenance entry point. See docs/embedding-recovery.md."""

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys


def parser():
    result = argparse.ArgumentParser(description="Inspect or recover existing chunk embeddings; defaults to dry-run.")
    mode = result.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Read-only inspection (default); no provider calls")
    mode.add_argument("--execute", action="store_true", help="Opt in to paid Voyage calls and batch database writes")
    result.add_argument("--expected-model", help="Required with --execute; must match the current VOYAGE_MODEL")
    result.add_argument("--document-id", type=int, action="append", dest="document_ids")
    result.add_argument("--after-document-id", type=int, default=0)
    result.add_argument("--max-documents", type=int, default=10)
    result.add_argument("--max-chunks", type=int, default=256)
    result.add_argument("--batch-size", type=int, default=32)
    return result


@contextmanager
def open_store(database_url, execute, expected_model):
    # Never fall back to the application's DATABASE_URL or discover a target in
    # .env. Set the explicit maintenance target BEFORE importing the DB module.
    os.environ["DATABASE_URL"] = database_url
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session
    from app.services.embedding_recovery_service import PostgresRecoveryStore
    from app.services.embedding_contract import VOYAGE_MODEL

    if execute and expected_model != VOYAGE_MODEL:
        raise ValueError("Expected model does not match configured model")
    # Own this engine explicitly; never reuse an already-imported app engine.
    engine = create_engine(database_url, hide_parameters=True)
    try:
        with engine.connect().execution_options(postgresql_readonly=not execute) as connection:
            with Session(bind=connection) as db:
                dimension = db.execute(text(
                    "SELECT a.atttypmod FROM pg_attribute a "
                    "JOIN pg_type t ON t.oid = a.atttypid "
                    "WHERE a.attrelid = 'document_chunks'::regclass "
                    "AND a.attname = 'embedding' AND NOT a.attisdropped "
                    "AND t.typname = 'vector'"
                )).scalar_one()
                if dimension != 512:
                    raise ValueError("Recovery requires the existing vector(512) schema")
                yield PostgresRecoveryStore(db)
    finally:
        engine.dispose()


def main(argv=None):
    arguments = parser()
    args = arguments.parse_args(argv)
    target = os.environ.get("EMBEDDING_RECOVERY_DATABASE_URL")
    if not target:
        arguments.error("Set EMBEDDING_RECOVERY_DATABASE_URL explicitly; DATABASE_URL is never a fallback")
    if args.execute and not args.expected_model:
        arguments.error("--execute requires --expected-model to confirm the configured provider model")
    if not 1 <= args.max_documents <= 100 or not 1 <= args.max_chunks <= 10000 or not 1 <= args.batch_size <= 128:
        arguments.error("Limits: max-documents 1..100, max-chunks 1..10000, batch-size 1..128")
    if args.after_document_id < 0 or (args.document_ids and any(i <= 0 for i in args.document_ids)):
        arguments.error("Document IDs must be positive and cursor nonnegative")

    def emit(event):
        print(json.dumps(event, sort_keys=True), flush=True)

    try:
        with open_store(target, args.execute, args.expected_model) as store:
            from app.services.embedding_recovery_service import RecoveryLimits, recover_embeddings

            report = recover_embeddings(
                store,
                limits=RecoveryLimits(args.max_documents, args.max_chunks, args.batch_size),
                document_ids=args.document_ids,
                after_document_id=args.after_document_id,
                execute=args.execute,
                emit=emit,
            )
        if report["failed"]:
            return 1
        if (not report["documents"] or report["uninspected_requested_document_ids"]
                or any(not item["semantic_ready"] for item in report["documents"])):
            return 2
        return 0
    except Exception:
        emit({"event": "recovery_aborted", "message": "Check the explicit database target, vector(512) schema, model, credentials and database availability; rerun dry-run before retrying."})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
