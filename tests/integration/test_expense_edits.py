"""PUT and DELETE /groups/{id}/expenses/{expense_id} (decision 9 in DESIGN.md)."""

import uuid

import pytest
from sqlalchemy import select

from app.models import Expense, ExpenseShare
from tests.integration.helpers import equal, random_id, shares_of, values


@pytest.fixture
def dinner(api, trip):
    """₹900 paid by Shivanshu, split equally between all three."""
    return api.add_expense(trip.group, amount=90000, paid_by=trip.shivanshu, name="Dinner")


# ---------- Editing ----------


def test_edit_replaces_the_expense_and_recalculates_shares(api, trip, dinner):
    edited = api.edit_expense(trip.group, dinner["id"], amount=150000, paid_by=trip.shivanshu, name="Dinner (fixed)")
    assert (edited["id"], edited["name"], edited["amount"]) == (dinner["id"], "Dinner (fixed)", 150000)
    assert shares_of(edited) == {member: 50000 for member in trip.members}
    assert edited["created_at"] == dinner["created_at"]
    assert edited["updated_at"] is not None
    assert api.get_expense(trip.group, dinner["id"]) == edited

    again = api.edit_expense(trip.group, dinner["id"], amount=150000, paid_by=trip.shivanshu)
    assert again["updated_at"] > edited["updated_at"]


def test_edit_can_change_who_is_in_the_split(api, trip, dinner):
    edited = api.edit_expense(trip.group, dinner["id"], amount=90000, paid_by=trip.shivanshu,
                              splits=equal(trip.shivanshu, trip.ansh))
    assert shares_of(edited) == {trip.shivanshu: 45000, trip.ansh: 45000}  # Kush's share is gone
    assert api.nets(trip.group) == {trip.kush: 0, trip.shivanshu: 45000, trip.ansh: -45000}


def test_edit_can_change_the_strategy(api, trip, dinner):
    edited = api.edit_expense(trip.group, dinner["id"], amount=90000, paid_by=trip.shivanshu,
                              strategy="percentage", splits=values({trip.kush: 10, trip.ansh: 90}))
    assert shares_of(edited) == {trip.kush: 9000, trip.ansh: 81000}


def test_edit_can_change_the_payer_and_debts_move_with_it(api, trip, dinner):
    api.edit_expense(trip.group, dinner["id"], amount=90000, paid_by=trip.kush)
    assert api.nets(trip.group) == {trip.kush: 60000, trip.shivanshu: -30000, trip.ansh: -30000}


def test_edits_do_not_leave_old_share_rows(api, trip, dinner, db):
    for amount in (1000, 2000, 3000):
        api.edit_expense(trip.group, dinner["id"], amount=amount, paid_by=trip.kush)
    rows = db.scalars(select(ExpenseShare).where(ExpenseShare.expense_id == uuid.UUID(dinner["id"]))).all()
    assert sorted(row.amount for row in rows) == [1000, 1000, 1000]


@pytest.mark.parametrize("change, detail", [
    ({"strategy": "percentage", "splits": "pct90"}, "percentages add up to 90"),
    ({"paid_by": "riya"}, "paid_by: not a member"),
    ({"amount": 2**63}, None),
], ids=["split rule", "membership rule", "schema"])
def test_invalid_edit_is_422_and_leaves_the_expense_unchanged(api, trip, dinner, change, detail):
    fields = {"amount": 90000, "paid_by": trip.shivanshu, "strategy": "equal", "name": "Changed", **change}
    if fields["paid_by"] == "riya":
        fields["paid_by"] = trip.riya
    if fields.get("splits") == "pct90":
        fields["splits"] = values({trip.kush: 50, trip.ansh: 40})

    response = api.call("PUT", f"/groups/{trip.group}/expenses/{dinner['id']}",
                        api.expense_body(**fields), expect=422)
    if detail:
        assert detail in response.json()["detail"]
    assert api.get_expense(trip.group, dinner["id"]) == dinner  # untouched, updated_at still null


def test_edit_not_found_cases(api, trip, dinner):
    other_group = api.group("Other", members=trip.members)
    body = api.expense_body(amount=100, paid_by=trip.kush)
    for group, expense_id, detail in [
        (trip.group, random_id(), "Expense not found"),      # PUT never creates
        (other_group, dinner["id"], "Expense not found"),    # wrong group
        (random_id(), dinner["id"], "Group not found"),
    ]:
        response = api.call("PUT", f"/groups/{group}/expenses/{expense_id}", body, expect=404)
        assert response.json() == {"detail": detail}
    assert api.expenses(trip.group) == [dinner]


def test_editing_down_after_a_settlement_turns_it_into_an_overpayment(api, trip, dinner):
    settlement = api.settle(trip.group, trip.kush, trip.shivanshu, 30000)
    api.edit_expense(trip.group, dinner["id"], amount=30000, paid_by=trip.shivanshu)
    assert api.pairwise(trip.group, trip.kush)[trip.shivanshu] == 20000  # Shivanshu owes Kush back
    assert api.settlements(trip.group) == [settlement]  # settlements are never touched


# ---------- Deleting ----------


def test_deleted_expense_disappears_everywhere(api, trip, dinner):
    kept = api.add_expense(trip.group, amount=30000, paid_by=trip.kush, splits=equal(trip.ansh))
    assert api.delete_expense(trip.group, dinner["id"]).content == b""

    assert api.expenses(trip.group) == [kept]
    api.get_expense(trip.group, dinner["id"], expect=404)
    assert api.nets(trip.group) == {trip.kush: 30000, trip.shivanshu: 0, trip.ansh: -30000}
    assert api.pairwise(trip.group, trip.shivanshu) == {trip.kush: 0, trip.ansh: 0}
    assert api.user_balances(trip.shivanshu)["total_net"] == 0
    assert api.payments(trip.group) == [(trip.ansh, trip.kush, 30000)]


def test_delete_is_soft_the_rows_stay_for_the_audit_trail(api, trip, dinner, db):
    api.delete_expense(trip.group, dinner["id"])
    row = db.get(Expense, uuid.UUID(dinner["id"]))
    assert row.deleted_at is not None
    assert len(row.shares) == 3


def test_deleting_a_settled_expense_flips_the_balance(api, trip, dinner):
    settlement = api.settle(trip.group, trip.kush, trip.shivanshu, 30000)
    api.delete_expense(trip.group, dinner["id"])
    assert api.settlements(trip.group) == [settlement]
    assert api.nets(trip.group) == {trip.kush: 30000, trip.shivanshu: -30000, trip.ansh: 0}


def test_delete_not_found_cases(api, trip, dinner):
    other_group = api.group("Other", members=trip.members)
    assert api.delete_expense(trip.group, random_id(), expect=404).json() == {"detail": "Expense not found"}
    assert api.delete_expense(other_group, dinner["id"], expect=404).json() == {"detail": "Expense not found"}
    assert api.delete_expense(random_id(), dinner["id"], expect=404).json() == {"detail": "Group not found"}
    assert api.expenses(trip.group) == [dinner]

    # Once deleted, it can't be deleted again or edited.
    api.delete_expense(trip.group, dinner["id"])
    api.delete_expense(trip.group, dinner["id"], expect=404)
    api.edit_expense(trip.group, dinner["id"], amount=100, paid_by=trip.kush, expect=404)
