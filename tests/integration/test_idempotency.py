"""Idempotency-Key on POST /expenses and POST /settlements (decision 10 in DESIGN.md).

A client that doesn't get a response can't tell whether its request was recorded, so it
retries. With the same key, a retry must return the original row and record nothing new.
The key is required. Expenses and settlements share one implementation
(services/idempotency.py), so the detailed cases run against expenses, plus one replay
test for settlements.
"""

import uuid

import pytest

from tests.integration.helpers import values

KEY = "Idempotency-Key"
REPLAYED = "Idempotent-Replayed"


def post(api, path, body, key=None):
    return api.client.post(path, json=body, headers={KEY: key} if key else {})


@pytest.fixture
def path(trip):
    return f"/groups/{trip.group}/expenses"


@pytest.fixture
def hotel(trip):
    return {"name": "Hotel", "amount": 500000, "paid_by": trip.ansh, "split_strategy": "equal"}


@pytest.mark.parametrize("kind", ["expenses", "settlements"])
def test_missing_key_is_400_and_saves_nothing(api, trip, kind):
    body = ({"name": "Hotel", "amount": 500000, "paid_by": trip.ansh, "split_strategy": "equal"}
            if kind == "expenses" else {"payer_id": trip.kush, "payee_id": trip.ansh, "amount": 100})
    response = post(api, f"/groups/{trip.group}/{kind}", body)
    assert response.status_code == 400
    assert response.json() == {"detail": "Idempotency-Key header is required"}
    assert getattr(api, kind)(trip.group) == []


def test_new_keys_mean_new_actions(api, trip, path, hotel):
    # Two genuine identical expenses (two ₹200 coffees) are two actions with two keys.
    post(api, path, hotel, str(uuid.uuid4()))
    post(api, path, hotel, str(uuid.uuid4()))
    assert len(api.expenses(trip.group)) == 2


def test_retry_with_the_same_key_returns_the_original(api, trip, path, hotel):
    key = str(uuid.uuid4())
    first = post(api, path, hotel, key)
    retry = post(api, path, hotel, key)

    assert first.status_code == retry.status_code == 201
    assert REPLAYED not in first.headers
    assert retry.headers[REPLAYED] == "true"
    assert retry.json() == first.json()
    assert len(api.expenses(trip.group)) == 1
    # Counted once: Ansh's own share is 166668 (166666 + the 2 leftover paise), so he is owed
    # 500000 - 166668 = 333332. Recorded twice, it would be 666664.
    assert api.nets(trip.group)[trip.ansh] == 333332


@pytest.mark.parametrize("first_splits, retry_splits", [
    (lambda t: values({t.kush: "50", t.ansh: "50"}), lambda t: values({t.kush: "50.00", t.ansh: 50})),
    (lambda t: values({t.kush: "25", t.ansh: "75"}), lambda t: values({t.ansh: "75", t.kush: "25"})),
], ids=["equal values written differently", "reordered splits"])
def test_equivalent_retry_bodies_are_recognised(api, trip, path, first_splits, retry_splits):
    # A client may rebuild the body slightly differently; equivalent requests must still match.
    key = str(uuid.uuid4())
    body = {"name": "Dinner", "amount": 10000, "paid_by": trip.kush, "split_strategy": "percentage"}
    first = post(api, path, {**body, "splits": first_splits(trip)}, key)
    retry = post(api, path, {**body, "name": " Dinner ", "splits": retry_splits(trip)}, key)
    assert retry.status_code == 201, retry.text
    assert retry.headers[REPLAYED] == "true"
    assert retry.json()["id"] == first.json()["id"]


def test_same_key_for_a_different_request_is_422(api, trip, path, hotel):
    key = str(uuid.uuid4())
    original = post(api, path, hotel, key).json()
    response = post(api, path, {**hotel, "amount": 400000}, key)
    assert response.status_code == 422
    assert response.json() == {"detail": "Idempotency-Key was already used for a different request"}
    assert api.expenses(trip.group) == [original]


def test_retry_after_the_expense_was_deleted_is_409_and_does_not_recreate_it(api, trip, path, hotel):
    key = str(uuid.uuid4())
    original = post(api, path, hotel, key).json()
    api.delete_expense(trip.group, original["id"])

    response = post(api, path, hotel, key)
    assert response.status_code == 409
    assert "already processed" in response.json()["detail"]
    assert api.expenses(trip.group) == []
    # The fingerprint is checked first: a different request with that key is still a 422.
    assert post(api, path, {**hotel, "amount": 1}, key).status_code == 422
    # Doing it again is a new action, with a new key.
    assert post(api, path, hotel, str(uuid.uuid4())).status_code == 201


def test_retry_after_an_edit_returns_the_expense_as_it_is_now(api, trip, path, hotel):
    key = str(uuid.uuid4())
    original = post(api, path, hotel, key).json()
    edited = api.edit_expense(trip.group, original["id"], amount=450000, paid_by=trip.ansh, name="Hotel")
    retry = post(api, path, hotel, key)  # the ORIGINAL body: still the same request
    assert retry.headers[REPLAYED] == "true"
    assert retry.json() == edited


def test_a_failed_request_is_not_remembered(api, trip, path, hotel):
    key = str(uuid.uuid4())
    assert post(api, path, {**hotel, "paid_by": trip.riya}, key).status_code == 422
    corrected = post(api, path, hotel, key)
    assert corrected.status_code == 201
    assert REPLAYED not in corrected.headers


def test_keys_are_scoped_to_the_group(api, trip, hotel):
    other = api.group("Other", members=trip.members)
    key = str(uuid.uuid4())
    first = post(api, f"/groups/{trip.group}/expenses", hotel, key)
    second = post(api, f"/groups/{other}/expenses", hotel, key)
    assert REPLAYED not in second.headers
    assert first.json()["id"] != second.json()["id"]


def test_malformed_key_is_422(api, trip, path, hotel):
    response = post(api, path, hotel, "not-a-uuid")
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["header", "Idempotency-Key"]


def test_settlement_retries(api, trip):
    path = f"/groups/{trip.group}/settlements"
    body = {"payer_id": trip.kush, "payee_id": trip.ansh, "amount": 30000}
    key = str(uuid.uuid4())
    first = post(api, path, body, key)
    retry = post(api, path, body, key)
    assert retry.headers[REPLAYED] == "true"
    assert retry.json() == first.json()
    assert api.nets(trip.group)[trip.kush] == 30000  # counted once

    api.delete_settlement(trip.group, first.json()["id"])
    assert post(api, path, body, key).status_code == 409
