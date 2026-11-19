"""POST /groups/{id}/expenses, GET the list, GET one.

The split maths is tested exhaustively in tests/unit/test_splits.py. Here, one case per
kind of error proves the wiring: the right status, a readable message, and nothing saved.
"""

import uuid
from datetime import datetime
from decimal import Decimal

import pytest
from uuid6 import uuid7

from app.models import Expense, ExpenseShare
from tests.integration.helpers import equal, keyed, random_id, shares_of, values

# ---------- Creating ----------


def test_equal_split_with_no_splits_is_between_all_members(api, trip):
    # Decisions 4 and 5: 100000 / 3 = 33333 r 1, and the payer gets the leftover paisa.
    expense = api.add_expense(trip.group, amount=100000, paid_by=trip.shivanshu)
    assert shares_of(expense) == {trip.kush: 33333, trip.shivanshu: 33334, trip.ansh: 33333}
    # An empty list means the same.
    expense = api.add_expense(trip.group, amount=90000, paid_by=trip.kush, splits=[])
    assert shares_of(expense) == {member: 30000 for member in trip.members}


def test_payer_does_not_have_to_be_in_the_split(api, trip):
    api.add_expense(trip.group, amount=50000, paid_by=trip.kush, splits=equal(trip.ansh))
    assert api.nets(trip.group) == {trip.kush: 50000, trip.shivanshu: 0, trip.ansh: -50000}


def test_exact_split(api, trip):
    expense = api.add_expense(
        trip.group, amount=500000, paid_by=trip.ansh, strategy="exact",
        splits=values({trip.kush: 200000, trip.shivanshu: 150000, trip.ansh: 150000}),
    )
    assert shares_of(expense) == {trip.kush: 200000, trip.shivanshu: 150000, trip.ansh: 150000}


def test_percentage_split(api, trip):
    # JSON numbers and strings both work for percentages.
    expense = api.add_expense(
        trip.group, amount=10000, paid_by=trip.kush, strategy="percentage",
        splits=values({trip.kush: "33.33", trip.shivanshu: 33.33, trip.ansh: "33.34"}),
    )
    assert shares_of(expense) == {trip.kush: 3333, trip.shivanshu: 3333, trip.ansh: 3334}


def test_largest_allowed_amount(api, trip):
    # MAX_PAISE = 10^12 (₹1000 crore), well above INT's limit: the columns must be BIGINT.
    expense = api.add_expense(trip.group, amount=10**12, paid_by=trip.kush)
    assert sum(shares_of(expense).values()) == 10**12
    assert sum(api.nets(trip.group).values()) == 0


def test_response_shape(api, trip):
    expense = api.add_expense(trip.group, amount=90000, paid_by=trip.kush, name="  Dinner  ")
    assert set(expense) == {"id", "name", "amount", "paid_by", "created_at", "updated_at",
                            "split_strategy", "splits", "shares"}
    assert expense["name"] == "Dinner"
    assert expense["updated_at"] is None
    assert uuid.UUID(expense["id"]).version == 7
    assert datetime.fromisoformat(expense["created_at"]).tzinfo is not None


# ---------- The split as entered (for pre-filling an edit form) ----------


@pytest.mark.parametrize("strategy, entered", [
    ("equal", lambda t: {t.kush: None, t.ansh: None}),
    ("exact", lambda t: {t.kush: 60000, t.ansh: 30000}),
    ("percentage", lambda t: {t.kush: "33.33", t.ansh: "66.67"}),
])
def test_the_split_comes_back_as_entered_and_can_be_sent_back_unchanged(api, trip, strategy, entered):
    entered = entered(trip)
    splits = [{"user_id": u} if v is None else {"user_id": u, "value": v} for u, v in entered.items()]
    expense = api.add_expense(trip.group, amount=90000, paid_by=trip.kush, strategy=strategy, splits=splits)

    fetched = api.get_expense(trip.group, expense["id"])
    assert fetched["split_strategy"] == strategy
    assert {s["user_id"]: s["value"] for s in fetched["splits"]} == {
        u: None if v is None else str(Decimal(str(v)).quantize(Decimal("0.01"))) for u, v in entered.items()
    }

    # An edit form pre-filled from the GET, saved with only the name changed, keeps the split.
    resaved = api.edit_expense(trip.group, expense["id"], amount=90000, paid_by=trip.kush, name="Renamed",
                               strategy=fetched["split_strategy"], splits=fetched["splits"])
    assert resaved["shares"] == expense["shares"]
    assert resaved["splits"] == fetched["splits"]


def test_equal_with_no_list_stores_the_members_at_that_moment(api, trip):
    expense = api.add_expense(trip.group, amount=90000, paid_by=trip.kush)
    api.add_members(trip.group, [trip.riya])  # joins later: not part of the old expense
    fetched = api.get_expense(trip.group, expense["id"])
    assert sorted(s["user_id"] for s in fetched["splits"]) == sorted(trip.members)
    assert all(s["value"] is None for s in fetched["splits"])


# (overrides, expected detail): a string is a business-rule 422 with that message;
# None is a request-shape 422 from the schema.
REJECTED = [
    (lambda t: {"split_strategy": "weighted"}, None),
    (lambda t: {"amount": 0}, None),
    (lambda t: {"amount": 10**12 + 1}, None),
    (lambda t: {"amount": 2**63}, None),   # used to overflow BIGINT and return a 500
    (lambda t: {"paid_by": t.riya}, "paid_by: not a member of the group"),
    (lambda t: {"splits": equal(t.kush, t.riya)}, "splits: not a member of the group"),
    (lambda t: {"splits": equal(t.kush, t.kush)}, "a user appears more than once in splits"),
    (lambda t: {"split_strategy": "exact", "splits": values({t.kush: 2000, t.ansh: 1000})},
     "exact amounts add up to 3000, but the expense amount is 5000"),
    (lambda t: {"split_strategy": "percentage", "splits": values({t.kush: 50, t.ansh: 40})},
     "percentages add up to 90, not 100"),
    (lambda t: {"split_strategy": "exact"}, "splits are required for exact splits"),
]
REJECTED_IDS = [
    "unknown strategy", "zero amount", "amount over the cap", "amount overflowing BIGINT",
    "payer not a member", "participant not a member", "duplicate participant",
    "exact under total", "percent under 100", "exact without splits",
]


@pytest.mark.parametrize("overrides, detail", REJECTED, ids=REJECTED_IDS)
def test_rejected_expense_is_422_and_saves_nothing(api, trip, overrides, detail):
    body = {"name": "Bad", "amount": 5000, "paid_by": trip.kush, "split_strategy": "equal", **overrides(trip)}
    response = api.call("POST", f"/groups/{trip.group}/expenses", body, expect=422)
    if detail is None:
        assert isinstance(response.json()["detail"], list)
    else:
        assert detail in response.json()["detail"]
    assert api.expenses(trip.group) == []


# ---------- Listing and getting ----------


def test_list_is_newest_first_and_only_this_groups(api, trip):
    other_group = api.group("Other", members=[trip.kush])
    first = api.add_expense(trip.group, amount=300, paid_by=trip.kush, name="First")
    api.add_expense(other_group, amount=500, paid_by=trip.kush)
    second = api.add_expense(trip.group, amount=600, paid_by=trip.ansh, name="Second")
    assert api.expenses(trip.group) == [second, first]


def test_list_breaks_created_at_ties_by_id(api, trip, db):
    # Two expenses saved in one transaction get the same created_at (now() is the
    # transaction's start time); the time-ordered UUIDv7 id keeps the order stable.
    older_id, newer_id = uuid7(), uuid7()
    for expense_id in (older_id, newer_id):
        db.add(Expense(
            **keyed(), split_strategy="equal", id=expense_id, group_id=uuid.UUID(trip.group), name="Same time", amount=100,
            paid_by=uuid.UUID(trip.kush), shares=[ExpenseShare(user_id=uuid.UUID(trip.kush), amount=100)],
        ))
    db.commit()

    listed = api.expenses(trip.group)
    assert [e["id"] for e in listed] == [str(newer_id), str(older_id)]
    assert listed[0]["created_at"] == listed[1]["created_at"]


def test_shares_are_ordered_by_user_id_even_after_edits(api, trip):
    # Regression: without ORDER BY, Postgres returns share rows in storage order, which
    # changes when an edit deletes and re-inserts them.
    by_id = sorted(trip.members, key=uuid.UUID)
    expense = api.add_expense(trip.group, amount=90000, paid_by=trip.kush, splits=equal(*reversed(by_id)))
    assert [s["user_id"] for s in expense["shares"]] == by_id
    for splits in (equal(by_id[2], by_id[0]), None, equal(by_id[1], by_id[2]), None):
        edited = api.edit_expense(trip.group, expense["id"], amount=90000, paid_by=trip.kush, splits=splits)
        ids = [s["user_id"] for s in edited["shares"]]
        assert ids == sorted(ids, key=uuid.UUID)
        assert api.get_expense(trip.group, expense["id"]) == edited
        assert api.expenses(trip.group) == [edited]


def test_not_found_cases(api, trip):
    expense = api.add_expense(trip.group, amount=300, paid_by=trip.kush)
    assert api.get_expense(trip.group, expense["id"]) == expense
    other_group = api.group("Other", members=[trip.kush])

    assert api.get_expense(trip.group, random_id(), expect=404) == {"detail": "Expense not found"}
    assert api.get_expense(other_group, expense["id"], expect=404) == {"detail": "Expense not found"}
    assert api.get_expense(random_id(), expense["id"], expect=404) == {"detail": "Group not found"}
    api.call("GET", f"/groups/{random_id()}/expenses", expect=404)
    api.call("POST", f"/groups/{random_id()}/expenses",
             api.expense_body(amount=100, paid_by=trip.kush), expect=404)
