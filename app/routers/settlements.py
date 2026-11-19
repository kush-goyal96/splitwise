import uuid

from fastapi import APIRouter, Response, status

from app.database import DbSession
from app.idempotency import REPLAYED_HEADER, IdempotencyKey, fingerprint
from app.models import Settlement
from app.schemas import SettlementCreate, SettlementList, SettlementRead
from app.services import settlements as service

router = APIRouter(prefix="/groups/{group_id}/settlements", tags=["settlements"])


@router.post("", response_model=SettlementRead, status_code=status.HTTP_201_CREATED)
def create_settlement(
    group_id: uuid.UUID,
    payload: SettlementCreate,
    db: DbSession,
    response: Response,
    idempotency_key: IdempotencyKey,
) -> Settlement:
    settlement, replayed = service.create_settlement(
        db,
        group_id,
        payer_id=payload.payer_id,
        payee_id=payload.payee_id,
        amount=payload.amount,
        idempotency_key=idempotency_key,
        fingerprint=fingerprint(payload.fingerprint_data()),
    )
    if replayed:
        response.headers[REPLAYED_HEADER] = "true"
    return settlement


@router.get("", response_model=SettlementList)
def list_settlements(group_id: uuid.UUID, db: DbSession) -> SettlementList:
    settlements = service.list_settlements(db, group_id)
    return SettlementList.model_validate({"settlements": settlements}, from_attributes=True)


@router.get("/{settlement_id}", response_model=SettlementRead)
def get_settlement(group_id: uuid.UUID, settlement_id: uuid.UUID, db: DbSession) -> Settlement:
    return service.get_settlement(db, group_id, settlement_id)


@router.put("/{settlement_id}", response_model=SettlementRead)
def update_settlement(
    group_id: uuid.UUID, settlement_id: uuid.UUID, payload: SettlementCreate, db: DbSession
) -> Settlement:
    # Full replacement: same body as POST.
    return service.update_settlement(
        db,
        group_id,
        settlement_id,
        payer_id=payload.payer_id,
        payee_id=payload.payee_id,
        amount=payload.amount,
    )


@router.delete("/{settlement_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_settlement(group_id: uuid.UUID, settlement_id: uuid.UUID, db: DbSession) -> None:
    service.delete_settlement(db, group_id, settlement_id)
