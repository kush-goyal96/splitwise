"""Unit tests for app/domain/splits.py: the split maths, with no database.

Examples document each rule once (decisions 3, 4 and 5, and each split error); the
property tests at the bottom then check the maths against an independent integer-only
calculation for thousands of random inputs, which also covers listing order.
"""

import uuid
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.splits import SplitError, SplitStrategy, calculate_shares
from app.errors import RuleViolationError

EQUAL, EXACT, PERCENTAGE = SplitStrategy.EQUAL, SplitStrategy.EXACT, SplitStrategy.PERCENTAGE

# Fixed ids with a known order: A < B < C < D.
A, B, C, D = (uuid.UUID(int=i) for i in range(1, 5))
MEMBERS = {A, B, C, D}


def split(amount, strategy, entries=(), *, payer=A, members=MEMBERS):
    return calculate_shares(amount, strategy, list(entries), payer, set(members))


def dec(value) -> Decimal:
    return Decimal(str(value))


def test_split_error_is_a_rule_violation():
    # The exception handler maps RuleViolationError to 422; SplitError must inherit that.
    assert issubclass(SplitError, RuleViolationError)


# ---------- Equal ----------


def test_equal_no_participants_listed_means_all_members():
    # Decision 5.
    assert split(90000, EQUAL, members={A, B, C}) == {A: 30000, B: 30000, C: 30000}


def test_equal_leftover_goes_to_the_payer_in_one_piece():
    # Decision 4: 7 / 4 = 1 r 3. The whole leftover goes to the payer, not spread out.
    assert split(7, EQUAL, payer=C) == {A: 1, B: 1, C: 4, D: 1}


def test_equal_leftover_goes_to_lowest_user_id_when_payer_is_not_splitting():
    # Listed in reverse on purpose: the rule is "lowest id", not "first listed".
    assert split(101, EQUAL, [(D, None), (C, None), (B, None)], payer=A) == {B: 35, C: 33, D: 33}


def test_equal_amount_too_small_for_everyone():
    assert split(4, EQUAL) == {A: 1, B: 1, C: 1, D: 1}  # 1 paisa each is the minimum
    with pytest.raises(SplitError, match="at least 1 paisa"):
        split(3, EQUAL)


def test_equal_values_are_not_allowed():
    with pytest.raises(SplitError, match="must not include values"):
        split(100, EQUAL, [(A, None), (B, dec(50))])


def test_duplicate_participant_is_rejected():
    with pytest.raises(SplitError, match="more than once"):
        split(100, EQUAL, [(A, None), (A, None)])


# ---------- Exact ----------


def test_exact_shares_are_the_given_amounts():
    assert split(5000, EXACT, [(A, dec(2000)), (B, dec("3000.00"))]) == {A: 2000, B: 3000}


@pytest.mark.parametrize("values, total", [((2000, 1000), 3000), ((4000, 2000), 6000)], ids=["under", "over"])
def test_exact_amounts_must_add_up(values, total):
    with pytest.raises(SplitError, match=f"add up to {total}, but the expense amount is 5000"):
        split(5000, EXACT, [(A, dec(values[0])), (B, dec(values[1]))])


def test_exact_fractional_paise_are_rejected_even_when_the_total_matches():
    with pytest.raises(SplitError, match="whole numbers"):
        split(5000, EXACT, [(A, dec("2500.5")), (B, dec("2499.5"))])


def test_exact_zero_share_is_rejected():
    # The schema already rejects value=0; the domain rejects it too (defence in depth).
    with pytest.raises(SplitError, match="at least 1 paisa"):
        split(5000, EXACT, [(A, dec(5000)), (B, dec(0))])


@pytest.mark.parametrize("strategy", [EXACT, PERCENTAGE])
def test_values_are_required_for_exact_and_percentage(strategy):
    with pytest.raises(SplitError, match=f"splits are required for {strategy.value} splits"):
        split(5000, strategy, [])
    with pytest.raises(SplitError, match=f"every entry in {strategy.value} splits needs a value"):
        split(5000, strategy, [(A, dec(100)), (B, None)])


# ---------- Percentage ----------


def test_percentage_two_decimal_places():
    entries = [(A, dec("33.33")), (B, dec("33.33")), (C, dec("33.34"))]
    assert split(10000, PERCENTAGE, entries) == {A: 3333, B: 3333, C: 3334}


def test_percentage_rounds_down_and_gives_the_leftover_to_the_payer():
    # 33.33 / 33.33 / 33.34 % of 100 paise floor to 33 each; the 1 left over goes to B, the payer.
    entries = [(A, dec("33.33")), (B, dec("33.33")), (C, dec("33.34"))]
    assert split(100, PERCENTAGE, entries, payer=B) == {A: 33, B: 34, C: 33}
    # 66.67% of 100 floors to 66, never rounds up to 67, so the leftover is never negative.
    assert split(100, PERCENTAGE, [(A, dec("33.33")), (B, dec("66.67"))], payer=A) == {A: 34, B: 66}


@pytest.mark.parametrize("values, total", [(("50", "40"), "90"), (("50", "50.01"), "100.01")])
def test_percentages_must_total_exactly_100(values, total):
    with pytest.raises(SplitError, match=rf"percentages add up to {total}, not 100"):
        split(5000, PERCENTAGE, [(A, dec(values[0])), (B, dec(values[1]))])


def test_percentage_amount_too_small_for_every_share_to_be_positive():
    # 50% of 1 paisa floors to 0 for both; the leftover can only rescue one of them.
    with pytest.raises(SplitError, match="at least 1 paisa"):
        split(1, PERCENTAGE, [(A, dec(50)), (B, dec(50))])


# ---------- Properties: compared against an independent, integer-only calculation ----------

user_ids = st.lists(st.integers(1, 40), min_size=1, max_size=8, unique=True).map(
    lambda ints: [uuid.UUID(int=i) for i in ints]
)
payers = st.integers(1, 45).map(lambda i: uuid.UUID(int=i))  # sometimes not a participant


def leftover_recipient(participants, payer):
    return payer if payer in participants else min(participants)


@given(amount=st.integers(1, 10**12), participants=user_ids, payer=payers, data=st.data())
def test_equal_split_properties(amount, participants, payer, data):
    order = data.draw(st.permutations(participants))
    base, leftover = divmod(amount, len(participants))
    expected = {user: base for user in participants}
    expected[leftover_recipient(participants, payer)] += leftover

    if base == 0:
        with pytest.raises(SplitError):
            split(amount, EQUAL, [(u, None) for u in order], payer=payer)
        return

    result = split(amount, EQUAL, [(u, None) for u in order], payer=payer)
    assert result == expected  # also proves the listing order doesn't matter
    assert sum(result.values()) == amount


@given(shares=st.lists(st.integers(1, 10**10), min_size=1, max_size=8), payer=payers)
def test_exact_split_returns_the_given_amounts(shares, payer):
    participants = [uuid.UUID(int=i + 1) for i in range(len(shares))]
    entries = [(u, Decimal(s)) for u, s in zip(participants, shares)]
    assert split(sum(shares), EXACT, entries, payer=payer) == dict(zip(participants, shares))


@st.composite
def basis_point_partitions(draw):
    """Percentages with up to 2 decimals that total exactly 100, as basis points (1% = 100)."""
    k = draw(st.integers(1, 6))
    cuts = sorted(draw(st.lists(st.integers(1, 9999), min_size=k - 1, max_size=k - 1, unique=True)))
    bounds = [0, *cuts, 10000]
    return [hi - lo for lo, hi in zip(bounds, bounds[1:])]


@given(amount=st.integers(1, 10**12), parts=basis_point_partitions(), payer=payers, data=st.data())
def test_percentage_split_properties(amount, parts, payer, data):
    participants = [uuid.UUID(int=i + 1) for i in range(len(parts))]
    bps = dict(zip(participants, parts))
    order = data.draw(st.permutations(participants))
    entries = [(u, Decimal(bps[u]) / 100) for u in order]

    # Independent oracle in pure integer maths: floor each share, give the rest to one person.
    floors = {u: amount * bp // 10000 for u, bp in bps.items()}
    leftover = amount - sum(floors.values())
    assert 0 <= leftover < len(parts)  # rounding down never overshoots, and loses < 1 paisa each
    expected = dict(floors)
    expected[leftover_recipient(participants, payer)] += leftover

    if any(share <= 0 for share in expected.values()):
        with pytest.raises(SplitError, match="at least 1 paisa"):
            split(amount, PERCENTAGE, entries, payer=payer)
        return

    result = split(amount, PERCENTAGE, entries, payer=payer)
    assert result == expected
    assert sum(result.values()) == amount
