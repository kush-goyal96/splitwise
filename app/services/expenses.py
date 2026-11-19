import uuid
from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.domain.splits import SplitStrategy, calculate_shares
from app.errors import NotFoundError
from app.models import ACTIVE_EXPENSE, Expense, ExpenseShare
from app.services.groups import get_group, get_member_ids, require_members

Splits = Sequence[tuple[uuid.UUID, Decimal | None]]


# ---------- Use cases ----------


def create_expense(
    db: Session,
    group_id: uuid.UUID,
    *,
    name: str,
    amount: int,
    paid_by: uuid.UUID,
    strategy: SplitStrategy,
    splits: Splits,
) -> Expense:
    get_group(db, group_id)
    shares = _calculate_shares(
        db, group_id, amount=amount, paid_by=paid_by, strategy=strategy, splits=splits
    )

    expense = Expense(
        group_id=group_id,
        name=name,
        amount=amount,
        paid_by=paid_by,
        shares=_share_rows(shares),
    )
    db.add(expense)
    # One commit: the expense and all its shares are saved together, or not at all.
    db.commit()
    db.refresh(expense)
    return expense


def update_expense(
    db: Session,
    group_id: uuid.UUID,
    expense_id: uuid.UUID,
    *,
    name: str,
    amount: int,
    paid_by: uuid.UUID,
    strategy: SplitStrategy,
    splits: Splits,
) -> Expense:
    """Replace an expense (full replacement, like PUT): the shares are recalculated from scratch.

    Concurrent edits are last-write-wins (decision 9 in DESIGN.md).
    """
    get_group(db, group_id)
    expense = _get_expense(db, group_id, expense_id, for_update=True)
    # Validate before changing anything: a 422 leaves the expense untouched.
    shares = _calculate_shares(
        db, group_id, amount=amount, paid_by=paid_by, strategy=strategy, splits=splits
    )

    expense.name = name
    expense.amount = amount
    expense.paid_by = paid_by
    # Set explicitly: an edit that only changes the shares wouldn't otherwise touch this row.
    expense.updated_at = func.now()

    # Delete the old shares before inserting the new ones. Without the flush, the unit of
    # work could INSERT a new (expense_id, user_id) row before DELETEing the old one,
    # and the composite primary key would collide.
    expense.shares.clear()
    db.flush()
    expense.shares.extend(_share_rows(shares))

    # One commit: readers see either the old expense or the new one, never a mix.
    db.commit()
    db.refresh(expense)
    return expense


def delete_expense(db: Session, group_id: uuid.UUID, expense_id: uuid.UUID) -> None:
    """Soft delete: the expense and its shares stay, but every query skips them.
    Settlements are not touched; if one paid off this expense, the balance flips sign."""
    get_group(db, group_id)
    expense = _get_expense(db, group_id, expense_id, for_update=True, with_shares=False)
    expense.deleted_at = func.now()
    db.commit()


def get_expense(db: Session, group_id: uuid.UUID, expense_id: uuid.UUID) -> Expense:
    get_group(db, group_id)
    return _get_expense(db, group_id, expense_id)


def list_expenses(db: Session, group_id: uuid.UUID) -> Sequence[Expense]:
    get_group(db, group_id)

    return db.scalars(
        select(Expense)
        .where(Expense.group_id == group_id, ACTIVE_EXPENSE)
        # Newest first. id breaks ties: UUIDv7 ids are time-ordered, so this stays stable
        # even when two expenses share the same created_at.
        .order_by(Expense.created_at.desc(), Expense.id.desc())
        # Load every expense's shares in one extra query, not one query per expense.
        .options(selectinload(Expense.shares))
    ).all()


# ---------- Helpers ----------


def _get_expense(
    db: Session,
    group_id: uuid.UUID,
    expense_id: uuid.UUID,
    *,
    for_update: bool = False,
    with_shares: bool = True,
) -> Expense:
    """An active expense in this group. Deleted expenses, and expenses in another
    group, are reported as not found: to the API they don't exist here.

    for_update=True locks the expense row (SELECT ... FOR UPDATE) until commit, so
    concurrent edits/deletes of one expense run one after another. Without it, two edits
    that both replace the shares can insert the same (expense_id, user_id) and the second
    fails with a duplicate key. Every writer locks the expense before touching its shares,
    so locks are always taken in the same order (no deadlocks).

    Writers must stay on READ COMMITTED (never call use_snapshot): a writer that waited
    for the lock then re-checks deleted_at against the committed row, so an edit that
    loses the race to a delete gets a clean 404. REPEATABLE READ would raise a
    serialization error instead.
    """
    stmt = select(Expense).where(
        Expense.id == expense_id, Expense.group_id == group_id, ACTIVE_EXPENSE
    )
    if for_update:
        stmt = stmt.with_for_update()
    if with_shares:
        stmt = stmt.options(selectinload(Expense.shares))
    expense = db.scalars(stmt).one_or_none()
    if expense is None:
        raise NotFoundError("Expense not found")
    return expense


def _calculate_shares(
    db: Session,
    group_id: uuid.UUID,
    *,
    amount: int,
    paid_by: uuid.UUID,
    strategy: SplitStrategy,
    splits: Splits,
) -> dict[uuid.UUID, int]:
    """Check the membership rule, then calculate each person's share in paise."""
    member_ids = get_member_ids(db, group_id)
    require_members(member_ids, [paid_by], "paid_by")
    require_members(member_ids, [user_id for user_id, _ in splits], "splits")

    # Pure calculation: raises SplitError (a RuleViolationError) if the split is invalid.
    return calculate_shares(amount, strategy, splits, paid_by, member_ids)


def _share_rows(shares: dict[uuid.UUID, int]) -> list[ExpenseShare]:
    return [ExpenseShare(user_id=user_id, amount=paise) for user_id, paise in shares.items()]
