"""Settlements: create, list, get one, edit and delete.

Logic shared with expenses (ordering, tie-breaks, row locks) is tested on the expense side.
"""

import uuid

import pytest

from app.models import Settlement
from tests.integration.helpers import random_id


@pytest.fixture
def lunch(api, trip):
    """₹900 paid by Shivanshu, split equally: Kush and Ansh each owe Shivanshu 30000."""
    return api.add_expense(trip.group, amount=90000, paid_by=trip.shivanshu, name="Lunch")


# ---------- Creating ----------


def test_settlement_pays_off_the_debt(api, trip, lunch):
    settlement = api.settle(trip.group, trip.kush, trip.shivanshu, 30000)
    assert set(settlement) == {"id", "payer_id", "payee_id", "amount", "created_at", "updated_at"}
    assert settlement["updated_at"] is None
    assert api.nets(trip.group) == {trip.kush: 0, trip.shivanshu: 30000, trip.ansh: -30000}
    assert api.pairwise(trip.group, trip.kush) == {trip.shivanshu: 0, trip.ansh: 0}
    assert api.expenses(trip.group) == [lunch]  # expenses are never touched


def test_settlement_larger_than_the_debt_flips_the_sign(api, trip, lunch):
    api.settle(trip.group, trip.kush, trip.shivanshu, 50000)
    assert api.pairwise(trip.group, trip.kush)[trip.shivanshu] == 20000  # now Shivanshu owes Kush


@pytest.mark.parametrize("overrides, detail", [
    (lambda t: {"payee_id": t.kush}, "payer_id and payee_id must be different users"),
    (lambda t: {"payee_id": t.riya}, "payee_id: not a member of the group"),
    (lambda t: {"amount": 0}, None),
    (lambda t: {"amount": 10**12 + 1}, None),
], ids=["paying yourself", "payee not a member", "zero", "over the cap"])
def test_rejected_settlement_is_422_and_saves_nothing(api, trip, overrides, detail):
    body = {"payer_id": trip.kush, "payee_id": trip.shivanshu, "amount": 1000, **overrides(trip)}
    response = api.call("POST", f"/groups/{trip.group}/settlements", body, expect=422)
    if detail:
        assert detail in str(response.json()["detail"])
    assert api.settlements(trip.group) == []


# ---------- Listing and getting ----------


def test_list_and_get(api, trip):
    other_group = api.group("Other", members=trip.members)
    first = api.settle(trip.group, trip.kush, trip.shivanshu, 100)
    api.settle(other_group, trip.kush, trip.ansh, 100)
    second = api.settle(trip.group, trip.ansh, trip.kush, 200)

    assert api.settlements(trip.group) == [second, first]  # newest first, this group only
    assert api.get_settlement(trip.group, first["id"]) == first
    assert api.get_settlement(trip.group, random_id(), expect=404) == {"detail": "Settlement not found"}
    assert api.get_settlement(other_group, first["id"], expect=404) == {"detail": "Settlement not found"}
    assert api.get_settlement(random_id(), first["id"], expect=404) == {"detail": "Group not found"}


# ---------- Editing ----------


def test_edit_the_amount_and_the_payee(api, trip, lunch):
    settlement = api.settle(trip.group, trip.kush, trip.shivanshu, 30000)
    edited = api.edit_settlement(trip.group, settlement["id"], trip.kush, trip.shivanshu, 20000)
    assert (edited["id"], edited["amount"], edited["created_at"]) == (settlement["id"], 20000, settlement["created_at"])
    assert edited["updated_at"] is not None
    assert api.nets(trip.group)[trip.kush] == -10000

    api.edit_settlement(trip.group, settlement["id"], trip.kush, trip.ansh, 20000)
    assert api.pairwise(trip.group, trip.kush) == {trip.shivanshu: -30000, trip.ansh: 20000}


def test_invalid_edit_is_422_and_leaves_the_settlement_unchanged(api, trip):
    settlement = api.settle(trip.group, trip.kush, trip.shivanshu, 30000)
    for payee in (trip.kush, trip.riya):  # paying yourself; a non-member
        api.edit_settlement(trip.group, settlement["id"], trip.kush, payee, 500, expect=422)
    assert api.get_settlement(trip.group, settlement["id"]) == settlement


def test_edit_not_found_cases(api, trip):
    settlement = api.settle(trip.group, trip.kush, trip.ansh, 100)
    other_group = api.group("Other", members=trip.members)
    assert api.edit_settlement(trip.group, random_id(), trip.kush, trip.ansh, 1, expect=404) == {
        "detail": "Settlement not found"}
    assert api.edit_settlement(other_group, settlement["id"], trip.kush, trip.ansh, 1, expect=404) == {
        "detail": "Settlement not found"}
    assert api.settlements(trip.group) == [settlement]


# ---------- Deleting ----------


def test_delete_brings_the_debt_back(api, trip, lunch, db):
    settlement = api.settle(trip.group, trip.kush, trip.shivanshu, 30000)
    assert api.delete_settlement(trip.group, settlement["id"]).content == b""

    assert api.settlements(trip.group) == []
    api.get_settlement(trip.group, settlement["id"], expect=404)
    assert api.nets(trip.group) == {trip.kush: -30000, trip.shivanshu: 60000, trip.ansh: -30000}
    assert db.get(Settlement, uuid.UUID(settlement["id"])).deleted_at is not None  # soft delete


def test_delete_not_found_cases(api, trip):
    settlement = api.settle(trip.group, trip.kush, trip.ansh, 100)
    other_group = api.group("Other", members=trip.members)
    assert api.delete_settlement(other_group, settlement["id"], expect=404).json() == {"detail": "Settlement not found"}
    assert api.delete_settlement(random_id(), settlement["id"], expect=404).json() == {"detail": "Group not found"}

    api.delete_settlement(trip.group, settlement["id"])
    api.delete_settlement(trip.group, settlement["id"], expect=404)
    api.edit_settlement(trip.group, settlement["id"], trip.kush, trip.ansh, 200, expect=404)
