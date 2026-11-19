"""The three balance endpoints: group nets, pairwise within a group, a user across groups.

Sign convention everywhere: positive = is owed money, negative = owes money.
The invariants (nets sum to 0, pairwise adds up to the net, antisymmetry, user totals)
are also checked after every step of random scenarios in test_invariants.py.
"""

import pytest

from tests.integration.helpers import equal, random_id, values


@pytest.fixture
def worked_example(api, trip):
    """The example from the README, with hand-checked results."""
    api.add_expense(trip.group, name="Dinner", amount=100000, paid_by=trip.shivanshu)
    api.add_expense(trip.group, name="Cab", amount=60000, paid_by=trip.kush, splits=equal(trip.kush, trip.ansh))
    api.add_expense(trip.group, name="Hotel", amount=500000, paid_by=trip.ansh, strategy="exact",
                    splits=values({trip.kush: 200000, trip.shivanshu: 150000, trip.ansh: 150000}))
    api.add_expense(trip.group, name="Snacks", amount=10000, paid_by=trip.kush, strategy="percentage",
                    splits=values({trip.kush: "33.33", trip.shivanshu: "33.33", trip.ansh: "33.34"}))
    api.settle(trip.group, trip.kush, trip.shivanshu, 30000)
    return trip


# ---------- GET /groups/{id}/balances ----------


def test_worked_example_group_nets(api, worked_example):
    t = worked_example
    # Kush: paid 70000, shares 266666, settled out 30000 -> 70000 - 266666 + 30000 = -166666
    assert api.group_balances(t.group) == [  # ordered by name
        {"user_id": t.ansh, "user_name": "Ansh", "net": 283333},
        {"user_id": t.kush, "user_name": "Kush", "net": -166666},
        {"user_id": t.shivanshu, "user_name": "Shivanshu", "net": -116667},
    ]


def test_members_without_activity_are_listed_with_zero(api, trip):
    assert api.group_balances(api.group("Empty")) == []
    assert api.nets(trip.group) == {member: 0 for member in trip.members}


def test_balances_only_count_this_group(api, trip):
    other_group = api.group("Other", members=trip.members)
    api.add_expense(other_group, amount=90000, paid_by=trip.kush)
    api.settle(other_group, trip.ansh, trip.kush, 100)
    assert api.nets(trip.group) == {member: 0 for member in trip.members}


# ---------- GET /groups/{id}/balances/{user_id} (pairwise) ----------


def test_worked_example_pairwise(api, worked_example):
    t = worked_example
    assert api.user_in_group(t.group, t.kush) == {
        "user_id": t.kush,
        "net": -166666,
        "balances": [  # everyone else, ordered by name; not Kush himself
            {"user_id": t.ansh, "user_name": "Ansh", "amount": -166666},
            {"user_id": t.shivanshu, "user_name": "Shivanshu", "amount": 0},
        ],
    }
    assert api.pairwise(t.group, t.shivanshu) == {t.ansh: -116667, t.kush: 0}
    assert api.pairwise(t.group, t.ansh) == {t.kush: 166666, t.shivanshu: 116667}


def test_your_own_share_of_your_own_expense_is_not_a_debt(api, trip):
    api.add_expense(trip.group, amount=90000, paid_by=trip.kush)  # 30000 each, Kush included
    assert api.pairwise(trip.group, trip.kush) == {trip.shivanshu: 30000, trip.ansh: 30000}
    assert api.pairwise(trip.group, trip.ansh)[trip.kush] == -30000


def test_debts_in_both_directions_net_out(api, trip):
    api.add_expense(trip.group, amount=10000, paid_by=trip.kush, splits=equal(trip.ansh))
    api.add_expense(trip.group, amount=4000, paid_by=trip.ansh, splits=equal(trip.kush))
    assert api.pairwise(trip.group, trip.kush)[trip.ansh] == 6000


def test_pairwise_errors(api, trip):
    response = api.call("GET", f"/groups/{trip.group}/balances/{trip.riya}", expect=422)
    assert response.json() == {"detail": "user is not a member of the group"}
    assert api.call("GET", f"/groups/{trip.group}/balances/{random_id()}", expect=404).json() == {
        "detail": "User not found"}
    assert api.call("GET", f"/groups/{random_id()}/balances/{trip.kush}", expect=404).json() == {
        "detail": "Group not found"}


# ---------- GET /users/{id}/balances ----------


def test_user_balance_in_each_group_and_total(api, worked_example):
    t = worked_example
    flat = api.group("Flat", members=[t.kush, t.ansh])
    api.add_expense(flat, amount=40000, paid_by=t.kush)  # Ansh owes Kush 20000
    strangers = api.group("Strangers", members=[t.ansh, t.riya])  # Kush isn't in this one
    api.add_expense(strangers, amount=1000, paid_by=t.riya)

    assert api.user_balances(t.kush) == {
        "user_id": t.kush,
        "total_net": -166666 + 20000,
        "groups": [  # ordered by group name
            {"group_id": flat, "group_name": "Flat", "net": 20000},
            {"group_id": t.group, "group_name": "Goa Trip", "net": -166666},
        ],
    }


def test_user_balances_edge_cases(api, trip):
    assert api.user_balances(trip.kush)["groups"] == [{"group_id": trip.group, "group_name": "Goa Trip", "net": 0}]
    loner = api.user("Loner")
    assert api.user_balances(loner) == {"user_id": loner, "total_net": 0, "groups": []}
    assert api.call("GET", f"/users/{random_id()}/balances", expect=404).json() == {"detail": "User not found"}
