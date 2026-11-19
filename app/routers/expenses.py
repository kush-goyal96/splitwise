import uuid

from fastapi import APIRouter, Response, status

from app.database import DbSession
from app.idempotency import REPLAYED_HEADER, IdempotencyKey, fingerprint
from app.models import Expense
from app.schemas import ExpenseCreate, ExpenseList, ExpenseRead
from app.services import expenses as service

router = APIRouter(prefix="/groups/{group_id}/expenses", tags=["expenses"])


@router.post("", response_model=ExpenseRead, status_code=status.HTTP_201_CREATED)
def create_expense(
    group_id: uuid.UUID,
    payload: ExpenseCreate,
    db: DbSession,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> Expense:
    expense, replayed = service.create_expense(
        db,
        group_id,
        name=payload.name,
        amount=payload.amount,
        paid_by=payload.paid_by,
        strategy=payload.split_strategy,
        splits=[(entry.user_id, entry.value) for entry in payload.splits],
        idempotency_key=idempotency_key,
        fingerprint=fingerprint(payload.fingerprint_data()) if idempotency_key else None,
    )
    if replayed:
        response.headers[REPLAYED_HEADER] = "true"
    return expense


@router.get("/{expense_id}", response_model=ExpenseRead)
def get_expense(group_id: uuid.UUID, expense_id: uuid.UUID, db: DbSession) -> Expense:
    return service.get_expense(db, group_id, expense_id)


@router.put("/{expense_id}", response_model=ExpenseRead)
def update_expense(
    group_id: uuid.UUID, expense_id: uuid.UUID, payload: ExpenseCreate, db: DbSession
) -> Expense:
    # Full replacement: same body as POST, shares are recalculated from scratch.
    return service.update_expense(
        db,
        group_id,
        expense_id,
        name=payload.name,
        amount=payload.amount,
        paid_by=payload.paid_by,
        strategy=payload.split_strategy,
        splits=[(entry.user_id, entry.value) for entry in payload.splits],
    )


@router.delete("/{expense_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_expense(group_id: uuid.UUID, expense_id: uuid.UUID, db: DbSession) -> None:
    service.delete_expense(db, group_id, expense_id)


@router.get("", response_model=ExpenseList)
def list_expenses(group_id: uuid.UUID, db: DbSession) -> ExpenseList:
    expenses = service.list_expenses(db, group_id)
    return ExpenseList.model_validate({"expenses": expenses}, from_attributes=True)
