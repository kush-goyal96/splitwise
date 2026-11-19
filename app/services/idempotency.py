"""Shared idempotency logic for services that create a row from a POST (decision 10).

Pattern (the same as the duplicate phone check): look up the key; if it's new, insert the
row with the key, and let the UNIQUE (group_id, idempotency_key) constraint decide any race.
A concurrent duplicate waits on the unique index until the first request commits, then
fails with a unique violation and replays the winner's row. There is never a half-finished
state to report.
"""

import uuid
from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import ConflictError, RuleViolationError
from app.models import Expense, Settlement

Row = TypeVar("Row", Expense, Settlement)


def find_by_key(db: Session, model: type[Row], group_id: uuid.UUID, key: uuid.UUID) -> Row | None:
    # Deliberately NOT filtered by ACTIVE_EXPENSE / ACTIVE_SETTLEMENT: the one exception to
    # that rule (decision 9). A deleted row still owns its key, so a late retry must find it.
    return db.scalars(
        select(model).where(model.group_id == group_id, model.idempotency_key == key)
    ).one_or_none()


def replay(row: Row, fingerprint: str | None) -> Row:
    """Return the row an earlier request with the same key created, if this is a true retry."""
    # Checked first: a key reused for a different request is a client bug, whatever the row's state.
    if row.request_fingerprint != fingerprint:
        raise RuleViolationError("Idempotency-Key was already used for a different request")
    if row.deleted_at is not None:
        raise ConflictError(
            "The request with this Idempotency-Key was already processed, "
            "and its result has since been deleted"
        )
    return row


def is_key_conflict(error: IntegrityError, model: type[Row]) -> bool:
    """Did this insert lose a race to a concurrent request with the same key?"""
    diag = getattr(error.orig, "diag", None)
    return getattr(diag, "constraint_name", None) == f"uq_{model.__tablename__}_group_id_idempotency_key"
