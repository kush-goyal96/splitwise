import uuid

from fastapi import APIRouter, status

from app.database import DbSession
from app.models import Settlement
from app.schemas import SettlementCreate, SettlementRead
from app.services import settlements as service

router = APIRouter(prefix="/groups/{group_id}/settlements", tags=["settlements"])


@router.post("", response_model=SettlementRead, status_code=status.HTTP_201_CREATED)
def create_settlement(group_id: uuid.UUID, payload: SettlementCreate, db: DbSession) -> Settlement:
    return service.create_settlement(
        db,
        group_id,
        payer_id=payload.payer_id,
        payee_id=payload.payee_id,
        amount=payload.amount,
    )
