"""Debt simplification: the payments that would settle everyone in a group.

Pure logic, no database. Works only from net balances (positive = is owed,
negative = owes), so who originally owed whom doesn't matter: the payments
only need to bring every net to 0.

Greedy: repeatedly the biggest debtor pays the biggest creditor as much as
they can. Each payment settles at least one of the two, so a group of n people
needs at most n - 1 payments. This is not always the true minimum (finding that
is NP-hard), but it is the standard practical answer.
"""

import heapq
import uuid
from collections.abc import Mapping
from typing import NamedTuple


class Transfer(NamedTuple):
    from_user_id: uuid.UUID
    to_user_id: uuid.UUID
    amount: int  # paise, always > 0


def simplify_debts(nets: Mapping[uuid.UUID, int]) -> list[Transfer]:
    """The payments that bring every net in `nets` to 0, biggest first.

    Ties between equal amounts are broken by user id, so the same nets always
    give the same payments.
    """
    if sum(nets.values()) != 0:
        raise ValueError("nets must sum to 0")

    # Min-heaps of (-amount, user_id): the top is the biggest amount, and for
    # equal amounts the smallest user id. Members with a net of 0 are left out.
    debtors = [(net, user_id) for user_id, net in nets.items() if net < 0]
    creditors = [(-net, user_id) for user_id, net in nets.items() if net > 0]
    heapq.heapify(debtors)
    heapq.heapify(creditors)

    transfers: list[Transfer] = []
    while debtors and creditors:
        neg_owes, debtor = heapq.heappop(debtors)
        neg_due, creditor = heapq.heappop(creditors)
        owes, due = -neg_owes, -neg_due

        amount = min(owes, due)
        transfers.append(Transfer(debtor, creditor, amount))

        # Whoever isn't fully settled goes back in with what's left.
        if owes > amount:
            heapq.heappush(debtors, (-(owes - amount), debtor))
        if due > amount:
            heapq.heappush(creditors, (-(due - amount), creditor))

    # The nets sum to 0, so debtors and creditors always run out together.
    assert not debtors and not creditors
    return transfers
