"""POST /users, POST /groups, GET /groups/{id}, POST /groups/{id}/members."""

import uuid

import pytest
from sqlalchemy import func, select

from app.models import GroupMember, User
from tests.integration.helpers import random_id, unique_phone

# ---------- Users ----------


def test_create_user(client):
    response = client.post("/users", json={"phone_number": " +919876543210 ", "user_name": "  Kush "})
    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"id", "phone_number", "user_name"}
    assert (body["phone_number"], body["user_name"]) == ("+919876543210", "Kush")  # whitespace stripped
    assert uuid.UUID(body["id"]).version == 7


def test_duplicate_phone_number_is_409_and_creates_nothing(client, db):
    assert client.post("/users", json={"phone_number": "+919800000001", "user_name": "A"}).status_code == 201
    # Also a duplicate once surrounding whitespace is stripped.
    response = client.post("/users", json={"phone_number": " +919800000001", "user_name": "B"})
    assert response.status_code == 409
    assert response.json() == {"detail": "A user with this phone number already exists"}
    assert db.scalar(select(func.count()).select_from(User)) == 1


@pytest.mark.parametrize("phone, stored", [
    ("9876543210", "+919876543210"),          # no country code: read as Indian
    ("+91 98765 43210", "+919876543210"),     # spaces
    ("98765-43210", "+919876543210"),         # dashes
    ("+14155552671", "+14155552671"),         # another country
], ids=["national", "spaces", "dashes", "international"])
def test_phone_numbers_are_stored_in_e164(client, phone, stored):
    response = client.post("/users", json={"phone_number": phone, "user_name": "U"})
    assert response.status_code == 201
    assert response.json()["phone_number"] == stored


def test_one_number_written_two_ways_is_one_user(client):
    assert client.post("/users", json={"phone_number": "9876543210", "user_name": "A"}).status_code == 201
    response = client.post("/users", json={"phone_number": "+91 98765 43210", "user_name": "B"})
    assert response.status_code == 409


@pytest.mark.parametrize("phone", ["123456", "+911234", "abcdefghij", "", "9" * 40],
                         ids=["too short", "too short with code", "letters", "empty", "too long"])
def test_invalid_phone_numbers_are_422(client, phone):
    response = client.post("/users", json={"phone_number": phone, "user_name": "U"})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "phone_number"]


@pytest.mark.parametrize("name, status", [("   ", 422), ("x" * 100, 201), ("x" * 101, 422)],
                         ids=["blank", "100 chars", "101 chars"])
def test_user_name_length(client, name, status):
    assert client.post("/users", json={"phone_number": unique_phone(), "user_name": name}).status_code == status


# ---------- Groups ----------


def test_create_group(client):
    response = client.post("/groups", json={"name": "  Goa Trip "})
    assert response.status_code == 201
    assert response.json()["name"] == "Goa Trip"
    assert client.post("/groups", json={"name": "   "}).status_code == 422
    assert client.post("/groups", json={"name": "x" * 101}).status_code == 422


def test_group_names_do_not_have_to_be_unique(api):
    assert api.group("Flatmates") != api.group("Flatmates")


def test_get_group_shows_members_ordered_by_name(api):
    zed, amy, mia = api.user("Zed"), api.user("Amy"), api.user("Mia")
    group = api.group("Trip")
    assert api.get_group(group).json() == {"id": group, "name": "Trip", "members": []}

    added = api.add_members(group, [zed, amy, mia]).json()["members"]
    assert [m["user_name"] for m in added] == ["Amy", "Mia", "Zed"]
    assert api.get_group(group).json()["members"] == added


def test_add_members_returns_the_whole_group(api):
    kush, ansh = api.user("Kush"), api.user("Ansh")
    group = api.group("Trip", members=[kush])
    members = api.add_members(group, [ansh]).json()["members"]
    assert {m["user_id"] for m in members} == {kush, ansh}


def test_adding_members_is_idempotent(api, db):
    kush, ansh = api.user("Kush"), api.user("Ansh")
    group = api.group("Trip")
    api.add_members(group, [kush, kush, ansh])   # an id listed twice is added once
    api.add_members(group, [kush, ansh])         # adding existing members again is a no-op
    assert db.scalar(select(func.count()).select_from(GroupMember)) == 2


def test_adding_unknown_users_is_404_and_adds_nobody(api):
    # All or nothing: the real user in the same request is not added either.
    kush = api.user("Kush")
    group = api.group("Trip")
    missing_1, missing_2 = sorted([random_id(), random_id()])
    response = api.add_members(group, [kush, missing_2, missing_1], expect=404)
    assert response.json() == {"detail": f"Users not found: {missing_1}, {missing_2}"}
    assert api.get_group(group).json()["members"] == []


def test_member_endpoints_errors(api):
    kush = api.user("Kush")
    assert api.get_group(random_id(), expect=404).json() == {"detail": "Group not found"}
    assert api.add_members(random_id(), [kush], expect=404).json() == {"detail": "Group not found"}
    api.add_members(api.group("Trip"), [], expect=422)
