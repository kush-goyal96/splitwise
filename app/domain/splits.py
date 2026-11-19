"""Split calculation: turns an expense request into exact per-person shares.

Pure domain logic: no database, no FastAPI. Numbers in, numbers out, so it can
be unit-tested directly. Group rules (who is a member) are checked by the
expenses service before this is called; this module only does the maths and
the rules about the split itself.

All amounts are integer paise. Percentages are Decimal; floats are never used.
"""

import uuid
from collections.abc import Sequence
from decimal import ROUND_FLOOR, Decimal
from enum import Enum

from app.errors import RuleViolationError

HUNDRED = Decimal(100)


class SplitStrategy(str, Enum):
    EQUAL = "equal"
    EXACT = "exact"
    PERCENTAGE = "percentage"


class SplitError(RuleViolationError):
    """The request breaks a splitting rule."""


def calculate_shares(
    amount: int,
    strategy: SplitStrategy,
    splits: Sequence[tuple[uuid.UUID, Decimal | None]],
    payer_id: uuid.UUID,
    all_members: set[uuid.UUID],
) -> dict[uuid.UUID, int]:
    """Return {user_id: paise owed}. The shares always add up to `amount`.

    splits: (user_id, value) pairs from the request. `value` means:
      equal      -> None
      exact      -> paise owed (a whole number)
      percentage -> percent owed (e.g. Decimal("33.33"))
    An empty `splits` is only allowed for an equal split, and means "split
    between all_members". The caller has already checked that the payer and
    every listed user are members.
    """
    participants = _participants(strategy, splits, all_members)
    values = dict(splits)

    if strategy is SplitStrategy.EQUAL:
        shares = _equal(amount, participants, payer_id)
    elif strategy is SplitStrategy.EXACT:
        shares = _exact(amount, participants, values)
    else:
        shares = _percentage(amount, participants, values, payer_id)

    if any(share <= 0 for share in shares.values()):
        raise SplitError("amount is too small: every participant's share must be at least 1 paisa")

    # Invariant from DESIGN.md: shares add up to the expense amount. If this fails, the bug
    # is in this module, not in the request, so it's a 500, never a 422. An explicit check,
    # not assert: `python -O` strips asserts, and this one guards money.
    total = sum(shares.values())
    if total != amount:
        raise RuntimeError(f"shares add up to {total}, not the expense amount {amount}")
    return shares


def _participants(
    strategy: SplitStrategy,
    splits: Sequence[tuple[uuid.UUID, Decimal | None]],
    all_members: set[uuid.UUID],
) -> list[uuid.UUID]:
    """Work out who is splitting, and validate the list and its values."""
    if not splits:
        if strategy is not SplitStrategy.EQUAL:
            raise SplitError(f"splits are required for {strategy.value} splits")
        # Decision 5: no participants listed = all members. Sorted, so the order is deterministic.
        return sorted(all_members)

    participants = [user_id for user_id, _ in splits]

    if len(set(participants)) != len(participants):
        raise SplitError("a user appears more than once in splits")

    has_value = [value is not None for _, value in splits]
    if strategy is SplitStrategy.EQUAL and any(has_value):
        raise SplitError("an equal split must not include values")
    if strategy is not SplitStrategy.EQUAL and not all(has_value):
        raise SplitError(f"every entry in {strategy.value} splits needs a value")

    return participants


def _leftover_recipient(participants: list[uuid.UUID], payer_id: uuid.UUID) -> uuid.UUID:
    """Decision 4: the leftover goes to the payer if they are splitting,
    otherwise to the first participant ordered by user id."""
    return payer_id if payer_id in participants else min(participants)


def _equal(amount: int, participants: list[uuid.UUID], payer_id: uuid.UUID) -> dict[uuid.UUID, int]:
    # e.g. 100000 between 3 -> base 33333, leftover 1
    base, leftover = divmod(amount, len(participants))
    shares = {user_id: base for user_id in participants}
    shares[_leftover_recipient(participants, payer_id)] += leftover
    return shares


def _exact(
    amount: int, participants: list[uuid.UUID], values: dict[uuid.UUID, Decimal | None]
) -> dict[uuid.UUID, int]:
    shares: dict[uuid.UUID, int] = {}
    for user_id in participants:
        value = values[user_id]
        assert value is not None  # guaranteed by _participants
        if value != value.to_integral_value():
            raise SplitError("exact split values must be whole numbers of paise")
        shares[user_id] = int(value)

    total = sum(shares.values())
    if total != amount:
        raise SplitError(f"exact amounts add up to {total}, but the expense amount is {amount}")
    return shares


def _percentage(
    amount: int,
    participants: list[uuid.UUID],
    values: dict[uuid.UUID, Decimal | None],
    payer_id: uuid.UUID,
) -> dict[uuid.UUID, int]:
    percents: dict[uuid.UUID, Decimal] = {}
    for user_id in participants:
        value = values[user_id]
        assert value is not None  # guaranteed by _participants
        percents[user_id] = value

    total = sum(percents.values(), Decimal(0))
    if total != HUNDRED:
        raise SplitError(f"percentages add up to {total}, not 100")

    # Round every share down to a whole paisa, then hand the paise lost to
    # rounding to one person (same rule as equal splits). Rounding down means
    # the leftover is never negative, and it is always less than one paisa per person.
    shares = {
        user_id: int((Decimal(amount) * pct / HUNDRED).to_integral_value(rounding=ROUND_FLOOR))
        for user_id, pct in percents.items()
    }
    leftover = amount - sum(shares.values())
    shares[_leftover_recipient(participants, payer_id)] += leftover
    return shares
