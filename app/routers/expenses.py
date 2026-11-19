import uuid

from fastapi import APIRouter, status

from app.database import DbSession
from app.models import Expense
from app.schemas import ExpenseCreate, ExpenseList, ExpenseRead
from app.services import expenses as service

router = APIRouter(prefix="/groups/{group_id}/expenses", tags=["expenses"])


@router.post("", response_model=ExpenseRead, status_code=status.HTTP_201_CREATED)
def create_expense(group_id: uuid.UUID, payload: ExpenseCreate, db: DbSession) -> Expense:
    return service.create_expense(
        db,
        group_id,
        name=payload.name,
        amount=payload.amount,
        paid_by=payload.paid_by,
        strategy=payload.split_strategy,
        splits=[(entry.user_id, entry.value) for entry in payload.splits],
    )


@router.get("", response_model=ExpenseList)
def list_expenses(group_id: uuid.UUID, db: DbSession) -> ExpenseList:
    expenses = service.list_expenses(db, group_id)
    return ExpenseList.model_validate({"expenses": expenses}, from_attributes=True)
