import uuid
from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.domain.splits import SplitStrategy, calculate_shares
from app.models import Expense, ExpenseShare
from app.services.groups import get_group, get_member_ids, require_members


def create_expense(
    db: Session,
    group_id: uuid.UUID,
    *,
    name: str,
    amount: int,
    paid_by: uuid.UUID,
    strategy: SplitStrategy,
    splits: Sequence[tuple[uuid.UUID, Decimal | None]],
) -> Expense:
    get_group(db, group_id)

    member_ids = get_member_ids(db, group_id)
    require_members(member_ids, [paid_by], "paid_by")
    require_members(member_ids, [user_id for user_id, _ in splits], "splits")

    # Pure calculation: raises SplitError (a RuleViolationError) if the split is invalid.
    shares = calculate_shares(amount, strategy, splits, paid_by, member_ids)

    expense = Expense(
        group_id=group_id,
        name=name,
        amount=amount,
        paid_by=paid_by,
        shares=[ExpenseShare(user_id=user_id, amount=paise) for user_id, paise in shares.items()],
    )
    db.add(expense)
    # One commit: the expense and all its shares are saved together, or not at all.
    db.commit()
    db.refresh(expense)
    return expense


def list_expenses(db: Session, group_id: uuid.UUID) -> Sequence[Expense]:
    get_group(db, group_id)

    return db.scalars(
        select(Expense)
        .where(Expense.group_id == group_id)
        # Newest first. id breaks ties: UUIDv7 ids are time-ordered, so this stays stable
        # even when two expenses share the same created_at.
        .order_by(Expense.created_at.desc(), Expense.id.desc())
        # Load every expense's shares in one extra query, not one query per expense.
        .options(selectinload(Expense.shares))
    ).all()
