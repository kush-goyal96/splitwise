"""Concurrent requests: row locks on edit/delete (decision 9), idempotent member adds, and
the insert-then-handle duplicate phone check.

Each operation runs in its own thread with its own database session (= its own Postgres
connection), released at the same moment by a barrier, so the operations really overlap.
Services are called directly so the test controls the timing; the HTTP layer adds nothing
to what is being tested here.
"""

import threading
import uuid
from collections.abc import Callable

import pytest
from sqlalchemy import func, select

from app.database import SessionLocal
from app.domain.splits import SplitStrategy
from app.errors import ConflictError, NotFoundError
from app.models import Expense, ExpenseShare, GroupMember, Settlement, User
from app.services import expenses, groups, settlements, users
from tests.integration.helpers import equal, unique_phone

pytestmark = pytest.mark.concurrency

ROUNDS = 15
EQUAL = SplitStrategy.EQUAL


def run_together(*operations: Callable) -> list:
    """Run each operation(db) in its own thread and session, all starting at once.
    Returns each one's result, or the exception it raised."""
    barrier = threading.Barrier(len(operations))
    outcomes: list = [None] * len(operations)

    def worker(index: int, operation: Callable) -> None:
        with SessionLocal() as db:
            barrier.wait()
            try:
                outcomes[index] = operation(db)
            except Exception as error:  # noqa: BLE001 - the outcome is what's being tested
                outcomes[index] = error

    threads = [threading.Thread(target=worker, args=(i, op)) for i, op in enumerate(operations)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
        assert not thread.is_alive(), "deadlock: a thread did not finish"
    return outcomes


def ids(trip):
    return uuid.UUID(trip.group), [uuid.UUID(member) for member in trip.members]


# ---------- Expenses ----------


def test_concurrent_edits_that_change_the_participants_all_succeed(api, trip, db):
    """The bug the row lock fixes: without it, edits that add a participant collide on
    the (expense_id, user_id) key and fail with a 500."""
    group, members = ids(trip)
    for _ in range(ROUNDS):
        expense = api.add_expense(trip.group, amount=90000, paid_by=trip.kush, splits=equal(trip.kush, trip.ansh))
        expense_id = uuid.UUID(expense["id"])
        amounts = [90000 + 300 * i for i in range(8)]

        outcomes = run_together(*[
            (lambda db, amount=amount: expenses.update_expense(
                db, group, expense_id, name="Edited", amount=amount, paid_by=members[0],
                strategy=EQUAL, splits=[]))
            for amount in amounts
        ])

        assert not [o for o in outcomes if isinstance(o, Exception)]
        db.expire_all()
        final = db.get(Expense, expense_id)
        shares = db.scalars(select(ExpenseShare).where(ExpenseShare.expense_id == expense_id)).all()
        assert final.amount in amounts  # last write wins: one of the requested edits, intact
        assert sorted(s.user_id for s in shares) == sorted(members)  # one row per member
        assert sum(s.amount for s in shares) == final.amount


def test_edit_racing_a_delete_never_errors(api, trip, db):
    group, members = ids(trip)
    seen = set()
    for _ in range(ROUNDS):
        expense_id = uuid.UUID(api.add_expense(trip.group, amount=90000, paid_by=trip.kush, name="Original")["id"])

        edit, delete = run_together(
            lambda db: expenses.update_expense(db, group, expense_id, name="Edited", amount=60000,
                                               paid_by=members[1], strategy=EQUAL, splits=[]),
            lambda db: expenses.delete_expense(db, group, expense_id),
        )

        assert delete is None  # the delete always succeeds
        assert edit is not None and (isinstance(edit, NotFoundError) or isinstance(edit, Expense)), edit
        seen.add(type(edit).__name__)
        db.expire_all()
        row = db.get(Expense, expense_id)
        assert row.deleted_at is not None
        if isinstance(edit, NotFoundError):
            assert row.name == "Original"  # the edit lost the race and changed nothing
    print(f"edit outcomes seen: {seen}")


def test_concurrent_deletes_exactly_one_succeeds(api, trip):
    group, _ = ids(trip)
    for _ in range(ROUNDS):
        expense_id = uuid.UUID(api.add_expense(trip.group, amount=300, paid_by=trip.kush)["id"])
        outcomes = run_together(*[lambda db: expenses.delete_expense(db, group, expense_id)] * 4)
        assert outcomes.count(None) == 1
        assert all(isinstance(o, NotFoundError) for o in outcomes if o is not None)


# ---------- Settlements ----------


def test_settlement_edit_racing_a_delete_never_errors(api, trip, db):
    group, members = ids(trip)
    for _ in range(ROUNDS):
        settlement_id = uuid.UUID(api.settle(trip.group, trip.kush, trip.ansh, 100)["id"])
        edit, delete = run_together(
            lambda db: settlements.update_settlement(db, group, settlement_id, payer_id=members[0],
                                                     payee_id=members[2], amount=999),
            lambda db: settlements.delete_settlement(db, group, settlement_id),
        )
        assert delete is None
        assert isinstance(edit, (Settlement, NotFoundError)), edit
        db.expire_all()
        row = db.get(Settlement, settlement_id)
        assert row.deleted_at is not None
        if isinstance(edit, NotFoundError):
            assert row.amount == 100


# ---------- Groups and users ----------


def test_concurrent_adds_of_the_same_members_are_idempotent(api, db):
    people = [uuid.UUID(api.user(f"U{i}")) for i in range(3)]
    for _ in range(ROUNDS):
        group = uuid.UUID(api.group("Race"))
        outcomes = run_together(*[lambda db: groups.add_members(db, group, people)] * 6)
        assert not [o for o in outcomes if isinstance(o, Exception)]
        count = db.scalar(select(func.count()).select_from(GroupMember).where(GroupMember.group_id == group))
        assert count == 3


def test_concurrent_signups_with_the_same_phone_exactly_one_wins(db):
    for _ in range(ROUNDS):
        phone = unique_phone()
        outcomes = run_together(*[lambda db: users.create_user(db, phone, "Racer")] * 6)
        winners = [o for o in outcomes if isinstance(o, User)]
        assert len(winners) == 1
        assert all(isinstance(o, ConflictError) for o in outcomes if o not in winners)
        assert db.scalar(select(func.count()).select_from(User).where(User.phone_number == phone)) == 1


# ---------- Idempotency keys ----------


def test_simultaneous_retries_with_one_key_record_it_once(api, trip, db):
    """Six copies of one request with the same key, all at once: one row, everyone gets it."""
    from app.idempotency import fingerprint
    from app.schemas import ExpenseCreate

    group, members = ids(trip)
    for _ in range(ROUNDS):
        key = uuid.uuid4()
        body = ExpenseCreate(name="Hotel", amount=90000, paid_by=members[0], split_strategy="equal")
        print_ = fingerprint(body.fingerprint_data())
        outcomes = run_together(*[
            (lambda db: expenses.create_expense(
                db, group, name="Hotel", amount=90000, paid_by=members[0], strategy=EQUAL, splits=[],
                idempotency_key=key, fingerprint=print_))
        ] * 6)

        assert not [o for o in outcomes if isinstance(o, Exception)], outcomes
        assert len({expense.id for expense, _ in outcomes}) == 1  # everyone got the same expense
        assert sorted(replayed for _, replayed in outcomes) == [False] + [True] * 5
        count = db.scalar(select(func.count()).select_from(Expense).where(Expense.idempotency_key == key))
        assert count == 1


def test_simultaneous_different_requests_with_one_key(api, trip, db):
    """A buggy client reusing one key for different requests at once: one row is created;
    every other request is either a replay of the same body or rejected with 422."""
    from app.errors import RuleViolationError
    from app.idempotency import fingerprint
    from app.schemas import SettlementCreate

    group, members = ids(trip)
    for _ in range(ROUNDS):
        key = uuid.uuid4()
        amounts = [100, 200, 100, 300]

        def settle(db, amount):
            body = SettlementCreate(payer_id=members[0], payee_id=members[1], amount=amount)
            return settlements.create_settlement(
                db, group, payer_id=members[0], payee_id=members[1], amount=amount,
                idempotency_key=key, fingerprint=fingerprint(body.fingerprint_data()))

        outcomes = run_together(*[(lambda db, a=a: settle(db, a)) for a in amounts])

        rows = db.scalars(select(Settlement).where(Settlement.idempotency_key == key)).all()
        assert len(rows) == 1
        winner = rows[0].amount
        for amount, outcome in zip(amounts, outcomes):
            if amount == winner:
                assert isinstance(outcome, tuple) and outcome[0].id == rows[0].id
            else:
                assert isinstance(outcome, RuleViolationError)
