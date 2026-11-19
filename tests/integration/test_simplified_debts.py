"""GET /groups/{id}/simplified-debts (decision 8). The algorithm itself is unit-tested in
tests/unit/test_simplify.py; these check the endpoint and its wiring to the balances."""

from tests.integration.helpers import equal, random_id, values


def test_worked_example(api, trip):
    api.add_expense(trip.group, amount=100000, paid_by=trip.shivanshu)
    api.add_expense(trip.group, amount=60000, paid_by=trip.kush, splits=equal(trip.kush, trip.ansh))
    api.add_expense(trip.group, amount=500000, paid_by=trip.ansh, strategy="exact",
                    splits=values({trip.kush: 200000, trip.shivanshu: 150000, trip.ansh: 150000}))
    api.add_expense(trip.group, amount=10000, paid_by=trip.kush, strategy="percentage",
                    splits=values({trip.kush: "33.33", trip.shivanshu: "33.33", trip.ansh: "33.34"}))
    api.settle(trip.group, trip.kush, trip.shivanshu, 30000)

    response = api.call("GET", f"/groups/{trip.group}/simplified-debts", expect=200).json()
    assert response == {"payments": [
        {"from_user_id": trip.kush, "to_user_id": trip.ansh, "amount": 166666},
        {"from_user_id": trip.shivanshu, "to_user_id": trip.ansh, "amount": 116667},
    ]}


def test_recording_the_payments_settles_the_group(api, trip):
    assert api.payments(trip.group) == []
    api.add_expense(trip.group, amount=100000, paid_by=trip.shivanshu)
    api.add_expense(trip.group, amount=7000, paid_by=trip.kush, splits=equal(trip.ansh))

    payments = api.payments(trip.group)
    assert api.settlements(trip.group) == []  # viewing the plan records nothing
    for payer, payee, amount in payments:
        api.settle(trip.group, payer, payee, amount)
    assert all(net == 0 for net in api.nets(trip.group).values())
    assert api.payments(trip.group) == []


def test_unknown_group_is_404(api):
    assert api.call("GET", f"/groups/{random_id()}/simplified-debts", expect=404).json() == {
        "detail": "Group not found"}
