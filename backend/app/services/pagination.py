"""Bounded lists with signed, owner/resource-scoped keyset cursors."""

import base64
from datetime import datetime
import hashlib
import hmac
import json
import os

from fastapi import HTTPException
from sqlalchemy import Integer, and_, cast, or_

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100


def _signature(payload):
    key = os.environ.get("JWT_SECRET_KEY")
    if not key:
        raise RuntimeError("JWT_SECRET_KEY is required for pagination")
    return hmac.new(key.encode(), b"pagination-v1:" + payload, hashlib.sha256).digest()


def encode_cursor(owner, scope, values):
    payload = json.dumps([owner, scope, values], default=lambda v: v.isoformat(),
                         separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(_signature(payload) + payload).decode().rstrip("=")


def decode_cursor(token, owner, scope, fields):
    try:
        if len(token) > 2048:
            raise ValueError()
        raw = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
        signature, payload = raw[:32], raw[32:]
        if not hmac.compare_digest(signature, _signature(payload)):
            raise ValueError()
        cursor_owner, cursor_scope, values = json.loads(payload)
        if cursor_owner != owner or cursor_scope != scope or len(values) != len(fields):
            raise ValueError()
        result = []
        for value, (column, _) in zip(values, fields):
            kind = column.type.python_type
            if kind is datetime:
                value = datetime.fromisoformat(value)
                if value.tzinfo is not None:
                    raise ValueError()
            elif type(value) is not kind:
                raise ValueError()
            result.append(value)
        return result
    except (ValueError, TypeError, KeyError, UnicodeError, OverflowError):
        raise HTTPException(400, "Invalid pagination cursor") from None


def paginated(query, fields, *, owner, scope, response, limit=DEFAULT_PAGE_SIZE,
              cursor=None, offset=0, reverse=False):
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE or type(offset) is not int or offset < 0:
        raise HTTPException(422, "Invalid pagination values")
    if cursor is not None and offset:
        raise HTTPException(400, "Use either cursor or offset")
    if cursor is not None:
        values = decode_cursor(cursor, owner, scope, fields)
        clauses = []
        prefix = []
        for (column, descending), value in zip(fields, values):
            comparison_column = cast(column, Integer) if type(value) is bool else column
            comparison_value = int(value) if type(value) is bool else value
            comparison = comparison_column < comparison_value if descending else comparison_column > comparison_value
            clauses.append(and_(*prefix, comparison))
            prefix.append(column == value)
        query = query.filter(or_(*clauses))
    rows = query.order_by(*(column.desc() if desc else column.asc() for column, desc in fields)).offset(offset).limit(limit + 1).all()
    items = rows[:limit]
    if len(rows) > limit:
        last = items[-1]
        response.headers["X-Next-Cursor"] = encode_cursor(owner, scope, [getattr(last, col.key) for col, _ in fields])
    if reverse:
        items.reverse()
    return items
