import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import NotFoundError
from app.models import ACTIVE_SETTLEMENT, Settlement
from app.services import idempotency
from app.services.groups import get_group, get_member_ids, require_members

# A settlement is a payment between two members. It is independent of expenses:
# creating, editing or deleting one never changes an expense or a share. Balances
# pick the change up the next time they are calculated.


# ---------- Use cases ----------


def create_settlement(
    db: Session,
    group_id: uuid.UUID,
    *,
    payer_id: uuid.UUID,
    payee_id: uuid.UUID,
    amount: int,
    idempotency_key: uuid.UUID | None = None,
    fingerprint: str | None = None,
) -> tuple[Settlement, bool]:
    """Record a settlement. Returns (settlement, replayed); see create_expense for the key."""
    if (idempotency_key is None) != (fingerprint is None):
        raise ValueError("idempotency_key and fingerprint go together")
    if idempotency_key is not None:
        existing = idempotency.find_by_key(db, Settlement, group_id, idempotency_key)
        if existing is not None:
            return idempotency.replay(existing, fingerprint), True

    get_group(db, group_id)
    _require_members(db, group_id, payer_id, payee_id)

    settlement = Settlement(
        group_id=group_id,
        payer_id=payer_id,
        payee_id=payee_id,
        amount=amount,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
    )
    db.add(settlement)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        if idempotency_key is None or not idempotency.is_key_conflict(error, Settlement):
            raise
        # A concurrent request with the same key committed first: this is a retry of it.
        winner = idempotency.find_by_key(db, Settlement, group_id, idempotency_key)
        return idempotency.replay(winner, fingerprint), True
    db.refresh(settlement)
    return settlement, False


def update_settlement(
    db: Session,
    group_id: uuid.UUID,
    settlement_id: uuid.UUID,
    *,
    payer_id: uuid.UUID,
    payee_id: uuid.UUID,
    amount: int,
) -> Settlement:
    """Replace a settlement (full replacement, like PUT). Last write wins."""
    get_group(db, group_id)
    settlement = _get_settlement(db, group_id, settlement_id, for_update=True)
    # Validate before changing anything: a 422 leaves the settlement untouched.
    _require_members(db, group_id, payer_id, payee_id)

    settlement.payer_id = payer_id
    settlement.payee_id = payee_id
    settlement.amount = amount
    settlement.updated_at = func.now()
    db.commit()
    db.refresh(settlement)
    return settlement


def delete_settlement(db: Session, group_id: uuid.UUID, settlement_id: uuid.UUID) -> None:
    """Soft delete: the row stays, but balances skip it, so the debt it paid comes back."""
    get_group(db, group_id)
    settlement = _get_settlement(db, group_id, settlement_id, for_update=True)
    settlement.deleted_at = func.now()
    db.commit()


def get_settlement(db: Session, group_id: uuid.UUID, settlement_id: uuid.UUID) -> Settlement:
    get_group(db, group_id)
    return _get_settlement(db, group_id, settlement_id)


def list_settlements(db: Session, group_id: uuid.UUID) -> Sequence[Settlement]:
    get_group(db, group_id)

    return db.scalars(
        select(Settlement)
        .where(Settlement.group_id == group_id, ACTIVE_SETTLEMENT)
        # Newest first; id (UUIDv7, time-ordered) breaks ties.
        .order_by(Settlement.created_at.desc(), Settlement.id.desc())
    ).all()


# ---------- Helpers ----------


def _get_settlement(
    db: Session, group_id: uuid.UUID, settlement_id: uuid.UUID, *, for_update: bool = False
) -> Settlement:
    """An active settlement in this group; deleted ones, and ones in another group, are 404.

    for_update=True locks the row until commit, so concurrent edits/deletes of one
    settlement run one after another; an edit that loses the race to a delete gets a 404.
    (Same reasoning as _get_expense in services/expenses.py; writers stay on READ COMMITTED.)
    """
    stmt = select(Settlement).where(
        Settlement.id == settlement_id, Settlement.group_id == group_id, ACTIVE_SETTLEMENT
    )
    if for_update:
        stmt = stmt.with_for_update()
    settlement = db.scalars(stmt).one_or_none()
    if settlement is None:
        raise NotFoundError("Settlement not found")
    return settlement


def _require_members(
    db: Session, group_id: uuid.UUID, payer_id: uuid.UUID, payee_id: uuid.UUID
) -> None:
    member_ids = get_member_ids(db, group_id)
    require_members(member_ids, [payer_id], "payer_id")
    require_members(member_ids, [payee_id], "payee_id")
