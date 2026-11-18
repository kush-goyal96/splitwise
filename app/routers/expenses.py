import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import DbSession
from app.deps import get_group_or_404
from app.models import Expense, ExpenseShare, GroupMember
from app.schemas import ExpenseCreate, ExpenseList, ExpenseRead
from app.services.splits import SplitError, calculate_shares

router = APIRouter(prefix="/groups/{group_id}/expenses", tags=["expenses"])


@router.post("", response_model=ExpenseRead, status_code=status.HTTP_201_CREATED)
def create_expense(group_id: uuid.UUID, payload: ExpenseCreate, db: DbSession) -> Expense:
    get_group_or_404(db, group_id)

    member_ids = set(
        db.scalars(select(GroupMember.user_id).where(GroupMember.group_id == group_id))
    )

    try:
        shares = calculate_shares(
            amount=payload.amount,
            strategy=payload.split_strategy,
            splits=[(entry.user_id, entry.value) for entry in payload.splits],
            payer_id=payload.paid_by,
            member_ids=member_ids,
        )
    except SplitError as exc:
        # The service knows nothing about HTTP; the router decides a broken rule is a 422.
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    expense = Expense(
        group_id=group_id,
        name=payload.name,
        amount=payload.amount,
        paid_by=payload.paid_by,
        shares=[ExpenseShare(user_id=user_id, amount=paise) for user_id, paise in shares.items()],
    )
    db.add(expense)
    # One commit: the expense and all its shares are saved together, or not at all.
    db.commit()
    db.refresh(expense)
    return expense


@router.get("", response_model=ExpenseList)
def list_expenses(group_id: uuid.UUID, db: DbSession) -> ExpenseList:
    get_group_or_404(db, group_id)

    expenses = db.scalars(
        select(Expense)
        .where(Expense.group_id == group_id)
        # Newest first. id breaks ties: UUIDv7 ids are time-ordered, so this stays stable
        # even when two expenses share the same created_at.
        .order_by(Expense.created_at.desc(), Expense.id.desc())
        # Load every expense's shares in one extra query, not one query per expense.
        .options(selectinload(Expense.shares))
    ).all()

    return ExpenseList.model_validate({"expenses": expenses}, from_attributes=True)
