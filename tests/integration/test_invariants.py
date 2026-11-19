"""Randomised scenarios checked against every balance invariant in DESIGN.md.

Each seed plays a long random sequence of operations across two overlapping groups:
create expenses (all three strategies, random participants, random amounts), edit them,
delete them, record settlements, edit and delete those, plus invalid requests that must
change nothing. The test keeps its own model of what should exist, and after every step
compares the API against it and against an independent oracle (tests/integration/helpers.py).

Invariants checked:
  1. The list endpoints show exactly the model's active expenses and settlements
     (soft-deleted rows never leak, rejected requests never save anything).
  2. Each expense's shares are positive and add up to its amount.
  3. Group nets sum to 0.
  4. Group nets match the oracle.
  5. Each member's pairwise amounts match the oracle and add up to their net.
  6. Pairwise is antisymmetric: A's view of B is minus B's view of A.
  7. A user's per-group nets match each group's balances, and total_net is their sum.
  8. Simplified debts settle every net, in at most n-1 payments, debtors to creditors.
  9. Retrying an earlier create with its Idempotency-Key never records anything new: it
     replays the row as it is now, or is a 409 if that row has since been deleted.

Seeds are fixed, so any failure reproduces exactly.
"""

import random
import uuid
from collections import Counter
from dataclasses import dataclass, field

import pytest

from tests.integration.helpers import Api, equal, oracle_nets, oracle_pairwise, shares_of, values

SEEDS = range(4)
STEPS = 80
FULL_CHECK_EVERY = 10


@dataclass
class GroupModel:
    id: str
    members: list[str]
    expenses: dict[str, dict] = field(default_factory=dict)
    settlements: dict[str, dict] = field(default_factory=dict)
    # Every successful create: (kind, path, body, idempotency key, id of the row it created).
    sent: list[tuple[str, str, dict, str, str]] = field(default_factory=list)


def new_key(rng: random.Random) -> str:
    # Drawn from the seeded generator, so a failing seed reproduces exactly.
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def random_split(rng: random.Random, members: list[str], amount: int) -> tuple[str, list | None]:
    strategy = rng.choice(["equal", "equal", "exact", "percentage"])
    people = rng.sample(members, rng.randint(1, len(members)))
    if strategy == "equal":
        return strategy, (None if rng.random() < 0.3 else equal(*people))
    if strategy == "exact":
        cuts = sorted(rng.sample(range(1, amount), len(people) - 1)) if amount >= len(people) else []
        bounds = [0, *cuts, amount]
        parts = [hi - lo for lo, hi in zip(bounds, bounds[1:])]
        if len(parts) != len(people):  # amount too small to give everyone 1 paisa: send it anyway
            parts = [amount] + [0] * (len(people) - 1)
        return strategy, values(dict(zip(people, parts)))
    cuts = sorted(rng.sample(range(1, 10000), len(people) - 1))
    bounds = [0, *cuts, 10000]
    return strategy, values({p: f"{(hi - lo) / 100:.2f}" for p, (lo, hi) in zip(people, zip(bounds, bounds[1:]))})


def random_amount(rng: random.Random) -> int:
    return rng.choice([rng.randint(1, 10), rng.randint(1, 100_000), rng.randint(1, 10**9)])


def try_expense(api: Api, rng: random.Random, group: GroupModel, expense_id: str | None = None) -> bool:
    """Create (or replace) an expense. Random inputs are sometimes invalid on purpose (too
    small to split); a 422 is fine, as long as nothing changed."""
    amount = random_amount(rng)
    strategy, splits = random_split(rng, group.members, amount)
    body = api.expense_body(amount=amount, paid_by=rng.choice(group.members), strategy=strategy,
                            splits=splits, name=f"E{rng.randint(1, 999)}")
    if expense_id is None:
        path, key = f"/groups/{group.id}/expenses", new_key(rng)
        response = api.client.post(path, json=body, headers={"Idempotency-Key": key})
        expected_ok = 201
        if response.status_code == 201:
            group.sent.append(("expenses", path, body, key, response.json()["id"]))
    else:
        response = api.client.put(f"/groups/{group.id}/expenses/{expense_id}", json=body)
        expected_ok = 200
    assert response.status_code in (expected_ok, 422), response.text
    if response.status_code == expected_ok:
        expense = response.json()
        group.expenses[expense["id"]] = expense
        return True
    return False


def step(api: Api, rng: random.Random, groups: list[GroupModel], outsider: str) -> str:
    """Do one random operation; return what was actually done (for the coverage check)."""
    group = rng.choice(groups)
    action = rng.choices(
        ["add", "edit", "delete", "settle", "edit_settlement", "delete_settlement", "invalid", "retry"],
        weights=[30, 12, 8, 20, 8, 6, 6, 8],
    )[0]

    if action == "edit" and group.expenses:
        return "edit" if try_expense(api, rng, group, rng.choice(list(group.expenses))) else "rejected"
    elif action == "delete" and group.expenses:
        expense_id = rng.choice(list(group.expenses))
        api.delete_expense(group.id, expense_id)
        del group.expenses[expense_id]
        return "delete"
    elif action == "settle":
        payer, payee = rng.sample(group.members, 2)
        path, key = f"/groups/{group.id}/settlements", new_key(rng)
        body = {"payer_id": payer, "payee_id": payee, "amount": random_amount(rng)}
        response = api.client.post(path, json=body, headers={"Idempotency-Key": key})
        assert response.status_code == 201, response.text
        settlement = response.json()
        group.settlements[settlement["id"]] = settlement
        group.sent.append(("settlements", path, body, key, settlement["id"]))
        return "settle"
    elif action == "retry" and group.sent:
        # A late retry of an earlier create. The model is NOT updated: nothing may change.
        kind, path, body, key, row_id = rng.choice(group.sent)
        response = api.client.post(path, json=body, headers={"Idempotency-Key": key})
        current = getattr(group, kind).get(row_id)
        if current is None:  # deleted since
            assert response.status_code == 409, response.text
        else:
            assert response.status_code == 201, response.text
            assert response.headers["Idempotent-Replayed"] == "true"
            assert response.json() == current  # the row as it is now, edits included
        return "retry"
    elif action == "edit_settlement" and group.settlements:
        settlement_id = rng.choice(list(group.settlements))
        payer, payee = rng.sample(group.members, 2)
        group.settlements[settlement_id] = api.edit_settlement(group.id, settlement_id, payer, payee,
                                                               random_amount(rng))
        return "edit_settlement"
    elif action == "delete_settlement" and group.settlements:
        settlement_id = rng.choice(list(group.settlements))
        api.delete_settlement(group.id, settlement_id)
        del group.settlements[settlement_id]
        return "delete_settlement"
    elif action == "invalid":
        # Must be rejected and change nothing; the next check proves it.
        rng.choice([
            lambda: api.add_expense(group.id, amount=100, paid_by=outsider, expect=422),
            lambda: api.add_expense(group.id, amount=0, paid_by=group.members[0], expect=422),
            lambda: api.settle(group.id, group.members[0], group.members[0], 100, expect=422),
            lambda: api.settle(group.id, group.members[0], outsider, 100, expect=422),
        ])()
        return "rejected"
    return "add" if try_expense(api, rng, group) else "rejected"


def check_group(api: Api, group: GroupModel, *, full: bool) -> dict[str, int]:
    # 1. Lists show exactly the active rows.
    listed_expenses = api.expenses(group.id)
    listed_settlements = api.settlements(group.id)
    assert {e["id"]: e for e in listed_expenses} == group.expenses
    assert {s["id"]: s for s in listed_settlements} == group.settlements

    # 2. Shares are positive and add up.
    for expense in listed_expenses:
        assert all(amount > 0 for amount in shares_of(expense).values())
        assert sum(shares_of(expense).values()) == expense["amount"]

    # 3 and 4. Nets sum to 0 and match the oracle.
    nets = api.nets(group.id)
    assert sum(nets.values()) == 0
    assert nets == oracle_nets(listed_expenses, listed_settlements, group.members)

    if full:
        # 5 and 6. Pairwise matches the oracle, adds up to the net, and is antisymmetric.
        expected = oracle_pairwise(listed_expenses, listed_settlements, group.members)
        views = {}
        for member in group.members:
            view = api.user_in_group(group.id, member)
            views[member] = {row["user_id"]: row["amount"] for row in view["balances"]}
            assert views[member] == expected[member]
            assert view["net"] == sum(views[member].values()) == nets[member]
        for a in group.members:
            for b in group.members:
                if a != b:
                    assert views[a][b] == -views[b][a]

        # 8. Simplified debts settle everyone, in at most n-1 payments, debtors to creditors.
        payments = api.payments(group.id)
        after = dict(nets)
        for payer, payee, amount in payments:
            assert amount > 0 and nets[payer] < 0 < nets[payee]
            after[payer] += amount
            after[payee] -= amount
        assert all(v == 0 for v in after.values())
        assert len(payments) <= max(sum(1 for v in nets.values() if v) - 1, 0)
    return nets


def check_user_totals(api: Api, groups: list[GroupModel], nets_by_group: dict[str, dict[str, int]]) -> None:
    # 7. Per-group nets match the group balances, and the total is their sum.
    everyone = {member for group in groups for member in group.members}
    for user in everyone:
        totals = api.user_balances(user)
        expected = {g.id: nets_by_group[g.id][user] for g in groups if user in g.members}
        assert {row["group_id"]: row["net"] for row in totals["groups"]} == expected
        assert totals["total_net"] == sum(expected.values())


@pytest.mark.parametrize("seed", SEEDS)
def test_random_operations_keep_every_invariant(api, seed):
    rng = random.Random(seed)
    people = [api.user(f"P{i}") for i in range(5)]
    outsider = api.user("Outsider")
    groups = [
        GroupModel(api.group("Trip", members=people[:4]), people[:4]),
        GroupModel(api.group("Flat", members=people[2:]), people[2:]),  # overlaps on P2, P3
    ]

    done = Counter()
    for step_number in range(1, STEPS + 1):
        done[step(api, rng, groups, outsider)] += 1
        full = step_number % FULL_CHECK_EVERY == 0 or step_number == STEPS
        nets_by_group = {group.id: check_group(api, group, full=full) for group in groups}
        if full:
            check_user_totals(api, groups, nets_by_group)

    # The scenario must actually have exercised every kind of operation, or it proves little.
    assert set(done) == {
        "add", "edit", "delete", "settle", "edit_settlement", "delete_settlement", "rejected", "retry",
    }, done
