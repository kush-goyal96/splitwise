"""Helpers for the integration tests.

Api: a thin client over the HTTP API. Every call states the status code it expects, so a
test fails loudly, with the response body, the moment anything unexpected comes back.

oracle_*: an independent calculation of balances in plain Python, from the expenses and
settlements themselves. The API calculates balances with SQL SUM/GROUP BY queries; these
calculate them a completely different way, so the two agreeing is real evidence.
"""

import itertools
import uuid
from typing import Any

from fastapi.testclient import TestClient
from httpx2 import Response

_phone_numbers = itertools.count(1_000_000_000)


def unique_phone() -> str:
    return f"+91{next(_phone_numbers)}"


def equal(*user_ids: str) -> list[dict]:
    """Splits for an equal split between these users."""
    return [{"user_id": user_id} for user_id in user_ids]


def values(mapping: dict[str, Any]) -> list[dict]:
    """Splits for an exact or percentage split: {user_id: value}."""
    return [{"user_id": user_id, "value": value} for user_id, value in mapping.items()]


def shares_of(expense: dict) -> dict[str, int]:
    return {share["user_id"]: share["amount"] for share in expense["shares"]}


def random_id() -> str:
    return str(uuid.uuid4())


def keyed() -> dict:
    """Idempotency columns for an Expense or Settlement inserted directly (both are NOT NULL)."""
    return {"idempotency_key": uuid.uuid4(), "request_fingerprint": "0" * 64}


class Api:
    def __init__(self, client: TestClient):
        self.client = client

    def call(
        self, method: str, path: str, json: Any = None, *, expect: int, headers: dict | None = None
    ) -> Response:
        # POSTs that record money require an Idempotency-Key. Tests get a fresh one (a new
        # action) unless they pass their own; test_idempotency.py tests the key itself.
        if method == "POST" and path.endswith(("/expenses", "/settlements")):
            headers = {"Idempotency-Key": str(uuid.uuid4()), **(headers or {})}
        response = self.client.request(method, path, json=json, headers=headers)
        assert response.status_code == expect, (
            f"{method} {path}: expected {expect}, got {response.status_code}: {response.text}"
        )
        return response

    # ---------- Users and groups ----------

    def user(self, name: str = "User", phone: str | None = None) -> str:
        body = {"phone_number": phone or unique_phone(), "user_name": name}
        return self.call("POST", "/users", body, expect=201).json()["id"]

    def group(self, name: str = "Group", members: list[str] = ()) -> str:
        group_id = self.call("POST", "/groups", {"name": name}, expect=201).json()["id"]
        if members:
            self.add_members(group_id, list(members))
        return group_id

    def add_members(self, group_id: str, user_ids: list[str], expect: int = 200) -> Response:
        return self.call("POST", f"/groups/{group_id}/members", {"user_ids": user_ids}, expect=expect)

    def get_group(self, group_id: str, expect: int = 200) -> Response:
        return self.call("GET", f"/groups/{group_id}", expect=expect)

    # ---------- Expenses ----------

    @staticmethod
    def expense_body(*, amount, paid_by, strategy="equal", splits=None, name="Expense") -> dict:
        body = {"name": name, "amount": amount, "paid_by": paid_by, "split_strategy": strategy}
        if splits is not None:
            body["splits"] = splits
        return body

    def add_expense(self, group_id: str, *, expect: int = 201, **fields) -> dict:
        return self.call(
            "POST", f"/groups/{group_id}/expenses", self.expense_body(**fields), expect=expect
        ).json()

    def edit_expense(self, group_id: str, expense_id: str, *, expect: int = 200, **fields) -> dict:
        return self.call(
            "PUT", f"/groups/{group_id}/expenses/{expense_id}", self.expense_body(**fields), expect=expect
        ).json()

    def delete_expense(self, group_id: str, expense_id: str, expect: int = 204) -> Response:
        return self.call("DELETE", f"/groups/{group_id}/expenses/{expense_id}", expect=expect)

    def get_expense(self, group_id: str, expense_id: str, expect: int = 200) -> dict:
        return self.call("GET", f"/groups/{group_id}/expenses/{expense_id}", expect=expect).json()

    def expenses(self, group_id: str) -> list[dict]:
        return self.call("GET", f"/groups/{group_id}/expenses", expect=200).json()["expenses"]

    # ---------- Settlements ----------

    def settle(self, group_id: str, payer: str, payee: str, amount: int, expect: int = 201) -> dict:
        body = {"payer_id": payer, "payee_id": payee, "amount": amount}
        return self.call("POST", f"/groups/{group_id}/settlements", body, expect=expect).json()

    def edit_settlement(
        self, group_id: str, settlement_id: str, payer: str, payee: str, amount: int, expect: int = 200
    ) -> dict:
        body = {"payer_id": payer, "payee_id": payee, "amount": amount}
        return self.call(
            "PUT", f"/groups/{group_id}/settlements/{settlement_id}", body, expect=expect
        ).json()

    def delete_settlement(self, group_id: str, settlement_id: str, expect: int = 204) -> Response:
        return self.call("DELETE", f"/groups/{group_id}/settlements/{settlement_id}", expect=expect)

    def get_settlement(self, group_id: str, settlement_id: str, expect: int = 200) -> dict:
        return self.call("GET", f"/groups/{group_id}/settlements/{settlement_id}", expect=expect).json()

    def settlements(self, group_id: str) -> list[dict]:
        return self.call("GET", f"/groups/{group_id}/settlements", expect=200).json()["settlements"]

    # ---------- Balances ----------

    def group_balances(self, group_id: str) -> list[dict]:
        return self.call("GET", f"/groups/{group_id}/balances", expect=200).json()["balances"]

    def nets(self, group_id: str) -> dict[str, int]:
        return {row["user_id"]: row["net"] for row in self.group_balances(group_id)}

    def user_in_group(self, group_id: str, user_id: str) -> dict:
        return self.call("GET", f"/groups/{group_id}/balances/{user_id}", expect=200).json()

    def pairwise(self, group_id: str, user_id: str) -> dict[str, int]:
        return {row["user_id"]: row["amount"] for row in self.user_in_group(group_id, user_id)["balances"]}

    def user_balances(self, user_id: str) -> dict:
        return self.call("GET", f"/users/{user_id}/balances", expect=200).json()

    def payments(self, group_id: str) -> list[tuple[str, str, int]]:
        rows = self.call("GET", f"/groups/{group_id}/simplified-debts", expect=200).json()["payments"]
        return [(p["from_user_id"], p["to_user_id"], p["amount"]) for p in rows]


# ---------- Independent oracle ----------


def oracle_nets(expenses: list[dict], settlements: list[dict], member_ids: list[str]) -> dict[str, int]:
    """net = paid - own shares + settlements paid - settlements received."""
    nets = {member: 0 for member in member_ids}
    for expense in expenses:
        nets[expense["paid_by"]] += expense["amount"]
        for share in expense["shares"]:
            nets[share["user_id"]] -= share["amount"]
    for settlement in settlements:
        nets[settlement["payer_id"]] += settlement["amount"]
        nets[settlement["payee_id"]] -= settlement["amount"]
    return nets


def oracle_pairwise(
    expenses: list[dict], settlements: list[dict], member_ids: list[str]
) -> dict[str, dict[str, int]]:
    """view[a][b] = what b owes a (positive) or a owes b (negative), from a's point of view."""
    view = {a: {b: 0 for b in member_ids if b != a} for a in member_ids}
    for expense in expenses:
        payer = expense["paid_by"]
        for share in expense["shares"]:
            debtor = share["user_id"]
            if debtor != payer:  # your own share of your own expense is no one's debt
                view[payer][debtor] += share["amount"]
                view[debtor][payer] -= share["amount"]
    for settlement in settlements:
        payer, payee = settlement["payer_id"], settlement["payee_id"]
        view[payer][payee] += settlement["amount"]
        view[payee][payer] -= settlement["amount"]
    return view
