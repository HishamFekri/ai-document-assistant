"""Bounded internal scans and ordinary read-transaction boundaries."""

from sqlalchemy import and_, or_

SCAN_BATCH_SIZE = 100


def iter_query_batches(query, columns, batch_size=SCAN_BATCH_SIZE):
    """Ascending keyset scan; callers retain their Python correctness predicates.

    No OFFSET growth and no open server-side cursor across caller work.
    All columns must form a unique, immutable ordering (ending in the row ID).
    """
    if not 1 <= batch_size <= 100:
        raise ValueError("Invalid scan batch size")
    cursor = None
    while True:
        page = query
        if cursor is not None:
            prefix, clauses = [], []
            for column, value in zip(columns, cursor):
                clauses.append(and_(*prefix, column > value))
                prefix.append(column == value)
            page = page.filter(or_(*clauses))
        rows = page.order_by(None).order_by(*columns).limit(batch_size).all()
        if not rows:
            return
        cursor = tuple(getattr(rows[-1], column.key) for column in columns)
        yield rows
        if len(rows) < batch_size:
            return


def iter_query(query, columns, batch_size=SCAN_BATCH_SIZE):
    for rows in iter_query_batches(query, columns, batch_size):
        yield from rows


def release_read_transaction(db):
    """Do not commit/discard caller writes or close intentional claim connections."""
    if db.new or db.dirty or db.deleted:
        return False
    db.rollback()
    return True
