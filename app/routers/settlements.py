import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.database import DbSession
from app.deps import get_group_or_404
from app.models import GroupMember, Settlement
from app.schemas import SettlementCreate, SettlementRead

router = APIRouter(prefix="/groups/{group_id}/settlements", tags=["settlements"])


@router.post("", response_model=SettlementRead, status_code=status.HTTP_201_CREATED)
def create_settlement(group_id: uuid.UUID, payload: SettlementCreate, db: DbSession) -> Settlement:
    get_group_or_404(db, group_id)

    member_ids = set(
        db.scalars(select(GroupMember.user_id).where(GroupMember.group_id == group_id))
    )

    if payload.payer_id not in member_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="payer_id must be a member of the group",
        )
    if payload.payee_id not in member_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="payee_id must be a member of the group",
        )

    # Only a new row: expenses and shares are never changed.
    # Balances pick the settlement up when they are next calculated.
    settlement = Settlement(
        group_id=group_id,
        payer_id=payload.payer_id,
        payee_id=payload.payee_id,
        amount=payload.amount,
    )
    db.add(settlement)
    db.commit()
    db.refresh(settlement)
    return settlement
