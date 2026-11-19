"""Balance calculation. Nothing here is stored: every balance is calculated on
request from expenses, expense_shares and settlements (decision 2 in DESIGN.md).

Sign convention everywhere: positive = is owed money, negative = owes money.

    net = paid for expenses - own shares + settlements paid - settlements received

Each total is one SUM ... GROUP BY query, so the number of queries is fixed
no matter how many expenses or members a group has. The database does the
adding up; Python only combines a handful of totals.
"""

import uuid
from collections import defaultdict
from typing import NamedTuple

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.database import use_snapshot
from app.domain.simplify import Transfer, simplify_debts
from app.errors import RuleViolationError
from app.models import ACTIVE_EXPENSE, Expense, ExpenseShare, Group, GroupMember, Settlement, User
from app.services.groups import get_group
from app.services.users import get_user


class MemberNet(NamedTuple):
    user_id: uuid.UUID
    user_name: str
    net: int


class PairwiseAmount(NamedTuple):
    user_id: uuid.UUID
    user_name: str
    amount: int  # positive = this member owes the user


class GroupNet(NamedTuple):
    group_id: uuid.UUID
    group_name: str
    net: int


# ---------- Use cases ----------


def group_balances(db: Session, group_id: uuid.UUID) -> list[MemberNet]:
    """Each member's net balance in the group, ordered by name. The nets sum to 0."""
    use_snapshot(db)  # before the first query: all reads below see one snapshot
    get_group(db, group_id)
    names = _member_names(db, group_id)
    nets = _group_nets(db, group_id, list(names))
    return [MemberNet(user_id, name, nets[user_id]) for user_id, name in names.items()]


def user_balance_in_group(
    db: Session, group_id: uuid.UUID, user_id: uuid.UUID
) -> list[PairwiseAmount]:
    """What each other member owes the user (positive) or is owed by them (negative).
    The amounts add up to the user's net in the group."""
    use_snapshot(db)
    get_group(db, group_id)
    get_user(db, user_id)
    names = _member_names(db, group_id)
    # NotFoundError above = no such user at all; this = the user exists but isn't in the group.
    if user_id not in names:
        raise RuleViolationError("user is not a member of the group")
    owed = _pairwise(db, group_id, user_id, list(names))
    return [PairwiseAmount(other, names[other], amount) for other, amount in owed.items()]


def user_balances(db: Session, user_id: uuid.UUID) -> list[GroupNet]:
    """The user's net in each of their groups, ordered by group name."""
    use_snapshot(db)
    get_user(db, user_id)
    return [GroupNet(*row) for row in _user_group_nets(db, user_id)]


def simplified_debts(db: Session, group_id: uuid.UUID) -> list[Transfer]:
    """The fewest payments (greedy) that would settle everyone in the group.
    Nothing is stored; members record actual payments as settlements."""
    use_snapshot(db)
    get_group(db, group_id)
    member_ids = list(_member_names(db, group_id))
    return simplify_debts(_group_nets(db, group_id, member_ids))


# ---------- Queries ----------


def _totals(db: Session, stmt: Select) -> dict[uuid.UUID, int]:
    """Run a `SELECT key, SUM(...) ... GROUP BY key` query and return {key: total}.

    Postgres returns SUM(bigint) as a Decimal, so totals are converted to int.
    """
    return {key: int(total) for key, total in db.execute(stmt)}


def _member_names(db: Session, group_id: uuid.UUID) -> dict[uuid.UUID, str]:
    """{user_id: user_name} for every member of the group, ordered by name."""
    rows = db.execute(
        select(User.id, User.user_name)
        .join(GroupMember, GroupMember.user_id == User.id)
        .where(GroupMember.group_id == group_id)
        .order_by(User.user_name, User.id)
    )
    return {user_id: name for user_id, name in rows}


def _group_nets(db: Session, group_id: uuid.UUID, member_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    """Each member's net balance in the group. The nets always sum to 0."""
    paid = _totals(
        db,
        select(Expense.paid_by, func.sum(Expense.amount))
        .where(Expense.group_id == group_id, ACTIVE_EXPENSE)
        .group_by(Expense.paid_by),
    )
    shares = _totals(
        db,
        select(ExpenseShare.user_id, func.sum(ExpenseShare.amount))
        .join(Expense, Expense.id == ExpenseShare.expense_id)
        .where(Expense.group_id == group_id, ACTIVE_EXPENSE)
        .group_by(ExpenseShare.user_id),
    )
    settled_out = _totals(
        db,
        select(Settlement.payer_id, func.sum(Settlement.amount))
        .where(Settlement.group_id == group_id)
        .group_by(Settlement.payer_id),
    )
    settled_in = _totals(
        db,
        select(Settlement.payee_id, func.sum(Settlement.amount))
        .where(Settlement.group_id == group_id)
        .group_by(Settlement.payee_id),
    )

    # Members with no activity are included with a net of 0.
    return {
        user_id: paid.get(user_id, 0)
        - shares.get(user_id, 0)
        + settled_out.get(user_id, 0)
        - settled_in.get(user_id, 0)
        for user_id in member_ids
    }


def _pairwise(
    db: Session, group_id: uuid.UUID, user_id: uuid.UUID, member_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """What each other member owes `user_id` (positive) or is owed by them (negative).

    A share is a debt between two people only: the person whose share it is,
    and the person who paid that expense. Your own share of an expense you paid
    is left out: you don't owe yourself. The amounts add up to the user's group net.
    """
    # Other people's shares on expenses this user paid: they owe the user.
    owed_to_user = _totals(
        db,
        select(ExpenseShare.user_id, func.sum(ExpenseShare.amount))
        .join(Expense, Expense.id == ExpenseShare.expense_id)
        .where(
            Expense.group_id == group_id,
            ACTIVE_EXPENSE,
            Expense.paid_by == user_id,
            ExpenseShare.user_id != user_id,
        )
        .group_by(ExpenseShare.user_id),
    )
    # This user's shares on expenses someone else paid: the user owes the payer.
    owed_by_user = _totals(
        db,
        select(Expense.paid_by, func.sum(ExpenseShare.amount))
        .join(Expense, Expense.id == ExpenseShare.expense_id)
        .where(
            Expense.group_id == group_id,
            ACTIVE_EXPENSE,
            ExpenseShare.user_id == user_id,
            Expense.paid_by != user_id,
        )
        .group_by(Expense.paid_by),
    )
    # Money the user paid to someone reduces what the user owes them (balance goes up)...
    paid_to = _totals(
        db,
        select(Settlement.payee_id, func.sum(Settlement.amount))
        .where(Settlement.group_id == group_id, Settlement.payer_id == user_id)
        .group_by(Settlement.payee_id),
    )
    # ...and money someone paid the user reduces what they owe the user (balance goes down).
    received_from = _totals(
        db,
        select(Settlement.payer_id, func.sum(Settlement.amount))
        .where(Settlement.group_id == group_id, Settlement.payee_id == user_id)
        .group_by(Settlement.payer_id),
    )

    return {
        other: owed_to_user.get(other, 0)
        - owed_by_user.get(other, 0)
        + paid_to.get(other, 0)
        - received_from.get(other, 0)
        for other in member_ids
        if other != user_id
    }


def _user_group_nets(db: Session, user_id: uuid.UUID) -> list[tuple[uuid.UUID, str, int]]:
    """(group_id, group_name, net) for every group the user is in, ordered by name.

    Same formula as group_nets, but for one user across all their groups:
    each total is grouped by group_id, so it is still a fixed number of queries
    however many groups the user is in.
    """
    groups = db.execute(
        select(Group.id, Group.name)
        .join(GroupMember, GroupMember.group_id == Group.id)
        .where(GroupMember.user_id == user_id)
        .order_by(Group.name, Group.id)
    ).all()

    paid = _totals(
        db,
        select(Expense.group_id, func.sum(Expense.amount))
        .where(Expense.paid_by == user_id, ACTIVE_EXPENSE)
        .group_by(Expense.group_id),
    )
    shares = _totals(
        db,
        select(Expense.group_id, func.sum(ExpenseShare.amount))
        .join(Expense, Expense.id == ExpenseShare.expense_id)
        .where(ExpenseShare.user_id == user_id, ACTIVE_EXPENSE)
        .group_by(Expense.group_id),
    )
    settled_out = _totals(
        db,
        select(Settlement.group_id, func.sum(Settlement.amount))
        .where(Settlement.payer_id == user_id)
        .group_by(Settlement.group_id),
    )
    settled_in = _totals(
        db,
        select(Settlement.group_id, func.sum(Settlement.amount))
        .where(Settlement.payee_id == user_id)
        .group_by(Settlement.group_id),
    )

    nets: dict[uuid.UUID, int] = defaultdict(int)
    for totals, sign in ((paid, 1), (shares, -1), (settled_out, 1), (settled_in, -1)):
        for group_id, total in totals.items():
            nets[group_id] += sign * total

    return [(group_id, name, nets[group_id]) for group_id, name in groups]
