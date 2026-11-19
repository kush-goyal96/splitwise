import uuid

from sqlalchemy.orm import Session

from app.models import Settlement
from app.services.groups import get_group, get_member_ids, require_members


def create_settlement(
    db: Session, group_id: uuid.UUID, *, payer_id: uuid.UUID, payee_id: uuid.UUID, amount: int
) -> Settlement:
    get_group(db, group_id)

    member_ids = get_member_ids(db, group_id)
    require_members(member_ids, [payer_id], "payer_id")
    require_members(member_ids, [payee_id], "payee_id")

    # Only a new row: expenses and shares are never changed.
    # Balances pick the settlement up when they are next calculated.
    settlement = Settlement(group_id=group_id, payer_id=payer_id, payee_id=payee_id, amount=amount)
    db.add(settlement)
    db.commit()
    db.refresh(settlement)
    return settlement
