"""The database's own rules: CHECK, UNIQUE, primary and foreign keys, cascades.

These hold even if a bug in the application skips a check (defence in depth), so they
are tested by writing invalid rows directly, bypassing the API. Each test also checks the
constraint's name, which comes from the naming convention in models.py.
"""

import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from uuid6 import uuid7

from app.models import Expense, ExpenseShare, Group, GroupMember, Settlement, User
from tests.integration.helpers import keyed


@pytest.fixture
def people(db):
    kush = User(phone_number="+911111111", user_name="Kush")
    ansh = User(phone_number="+912222222", user_name="Ansh")
    group = Group(name="Trip")
    db.add_all([kush, ansh, group])
    db.flush()
    db.add_all([GroupMember(group_id=group.id, user_id=kush.id), GroupMember(group_id=group.id, user_id=ansh.id)])
    db.commit()
    return kush.id, ansh.id, group.id


def violated_constraint(db, *rows) -> str:
    db.add_all(rows)
    with pytest.raises(IntegrityError) as error:
        db.commit()
    db.rollback()
    return error.value.orig.diag.constraint_name


def test_expense_amount_must_be_positive(db, people):
    kush, _, group = people
    assert violated_constraint(db, Expense(**keyed(), group_id=group, name="x", amount=0, paid_by=kush)) == \
        "ck_expenses_amount_positive"


def test_share_amount_must_be_positive(db, people):
    kush, _, group = people
    expense = Expense(**keyed(), group_id=group, name="x", amount=100, paid_by=kush,
                      shares=[ExpenseShare(user_id=kush, amount=0)])
    assert violated_constraint(db, expense) == "ck_expense_shares_amount_positive"


def test_settlement_amount_must_be_positive(db, people):
    kush, ansh, group = people
    assert violated_constraint(db, Settlement(**keyed(), group_id=group, payer_id=kush, payee_id=ansh, amount=-1)) == \
        "ck_settlements_amount_positive"


def test_settlement_payer_and_payee_must_differ(db, people):
    kush, _, group = people
    assert violated_constraint(db, Settlement(**keyed(), group_id=group, payer_id=kush, payee_id=kush, amount=1)) == \
        "ck_settlements_payer_not_payee"


def test_phone_number_is_unique(db, people):
    assert violated_constraint(db, User(phone_number="+911111111", user_name="Copy")) == "uq_users_phone_number"


def test_a_user_is_in_a_group_at_most_once(db, people):
    kush, _, group = people
    assert violated_constraint(db, GroupMember(group_id=group, user_id=kush)) == "pk_group_members"


def test_a_user_has_at_most_one_share_per_expense(db, people):
    kush, _, group = people
    expense = Expense(**keyed(), group_id=group, name="x", amount=100, paid_by=kush,
                      shares=[ExpenseShare(user_id=kush, amount=100)])
    db.add(expense)
    db.commit()
    assert violated_constraint(db, ExpenseShare(expense_id=expense.id, user_id=kush, amount=1)) == \
        "pk_expense_shares"


@pytest.mark.parametrize("make_row, constraint", [
    (lambda kush, group: Expense(**keyed(), group_id=uuid7(), name="x", amount=1, paid_by=kush), "fk_expenses_group_id_groups"),
    (lambda kush, group: GroupMember(group_id=group, user_id=uuid7()), "fk_group_members_user_id_users"),
], ids=["expense group", "member user"])
def test_references_must_exist(db, people, make_row, constraint):
    kush, _, group = people
    assert violated_constraint(db, make_row(kush, group)) == constraint


def test_hard_deleting_an_expense_cascades_to_its_shares(db, people):
    # The API only soft-deletes, but the foreign key is ON DELETE CASCADE as a safety net.
    kush, ansh, group = people
    expense = Expense(**keyed(), group_id=group, name="x", amount=100, paid_by=kush,
                      shares=[ExpenseShare(user_id=kush, amount=50), ExpenseShare(user_id=ansh, amount=50)])
    db.add(expense)
    db.commit()
    db.execute(text("DELETE FROM expenses WHERE id = :id"), {"id": expense.id})
    db.commit()
    assert db.scalar(select(func.count()).select_from(ExpenseShare)) == 0


def test_ids_are_time_ordered_uuid7(db):
    users = [User(phone_number=f"+9130000000{i:02d}", user_name=f"U{i}") for i in range(50)]
    for user in users:
        db.add(user)
        db.flush()
    ids = [user.id for user in users]
    assert all(isinstance(i, uuid.UUID) and i.version == 7 for i in ids)
    assert ids == sorted(ids)  # what the list endpoints' id tie-break relies on


def test_an_idempotency_key_is_used_once_per_group(db, people):
    kush, ansh, group = people
    key = uuid7()
    db.add(Settlement(group_id=group, payer_id=kush, payee_id=ansh, amount=1,
                      idempotency_key=key, request_fingerprint="a" * 64))
    db.commit()
    duplicate = Settlement(group_id=group, payer_id=kush, payee_id=ansh, amount=1,
                           idempotency_key=key, request_fingerprint="a" * 64)
    assert violated_constraint(db, duplicate) == "uq_settlements_group_id_idempotency_key"

    expense = Expense(group_id=group, name="x", amount=1, paid_by=kush, idempotency_key=key,
                      request_fingerprint="b" * 64, shares=[ExpenseShare(user_id=kush, amount=1)])
    db.add(expense)
    db.commit()  # the same key on another table is fine
    again = Expense(group_id=group, name="x", amount=1, paid_by=kush, idempotency_key=key,
                    request_fingerprint="b" * 64)
    assert violated_constraint(db, again) == "uq_expenses_group_id_idempotency_key"


def test_every_expense_and_settlement_has_an_idempotency_key(db, people):
    kush, ansh, group = people
    for row in (Settlement(group_id=group, payer_id=kush, payee_id=ansh, amount=1),
                Expense(group_id=group, name="x", amount=1, paid_by=kush)):
        db.add(row)
        with pytest.raises(IntegrityError) as error:
            db.commit()
        db.rollback()
        assert error.value.orig.diag.column_name == "idempotency_key"  # NOT NULL
