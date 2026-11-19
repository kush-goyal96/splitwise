"""Unit tests for app/domain/simplify.py: debt simplification (decision 8), no database."""

import uuid

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.simplify import Transfer, simplify_debts

A, B, C, D, E = (uuid.UUID(int=i) for i in range(1, 6))


def apply(nets, transfers):
    """The nets after every transfer is paid: the debtor's net goes up, the creditor's down."""
    after = dict(nets)
    for t in transfers:
        after[t.from_user_id] += t.amount
        after[t.to_user_id] -= t.amount
    return after


# ---------- Examples ----------


def test_nobody_owes_anything():
    assert simplify_debts({}) == []
    assert simplify_debts({A: 0, B: 0}) == []


def test_one_debtor_one_creditor():
    assert simplify_debts({A: -500, B: 500}) == [Transfer(A, B, 500)]


def test_chain_collapses_to_one_payment():
    # A owes B 100 and B owes C 100: B is net 0 and drops out, so A pays C directly.
    assert simplify_debts({A: -100, B: 0, C: 100}) == [Transfer(A, C, 100)]


def test_biggest_debtor_pays_biggest_creditor_first():
    nets = {A: -70, B: -30, C: 40, D: 40, E: 20}
    assert simplify_debts(nets) == [
        Transfer(A, C, 40),  # A (70) and C/D (40) are biggest; C wins the tie by id
        Transfer(A, D, 30),  # A has 30 left; A and B tie at 30, A wins by id
        Transfer(B, E, 20),
        Transfer(B, D, 10),
    ]


def test_ties_are_broken_by_user_id():
    assert simplify_debts({D: -50, C: -50, B: 50, A: 50}) == [Transfer(C, A, 50), Transfer(D, B, 50)]


def test_nets_that_do_not_sum_to_zero_are_a_bug():
    with pytest.raises(ValueError, match="sum to 0"):
        simplify_debts({A: 10, B: -5})


def test_transfer_fields_match_the_api_payment_shape():
    # The router builds Payment(**transfer._asdict()); the names must line up.
    assert Transfer._fields == ("from_user_id", "to_user_id", "amount")


# ---------- Properties ----------


@st.composite
def balanced_nets(draw):
    values = draw(st.lists(st.integers(-10**9, 10**9), min_size=1, max_size=12))
    values.append(-sum(values))
    ids = draw(st.permutations(range(1, len(values) + 1)))
    return {uuid.UUID(int=i): v for i, v in zip(ids, values)}


@given(balanced_nets())
def test_payments_settle_everyone(nets):
    assert all(v == 0 for v in apply(nets, simplify_debts(nets)).values())


@given(balanced_nets())
def test_at_most_n_minus_1_payments(nets):
    non_zero = sum(1 for v in nets.values() if v != 0)
    assert len(simplify_debts(nets)) <= max(non_zero - 1, 0)


@given(balanced_nets())
def test_debtors_only_pay_and_creditors_only_receive(nets):
    for t in simplify_debts(nets):
        assert t.amount > 0
        assert t.from_user_id != t.to_user_id
        assert nets[t.from_user_id] < 0 < nets[t.to_user_id]


@given(balanced_nets())
def test_nobody_pays_or_receives_more_than_their_net(nets):
    paid, received = {}, {}
    for t in simplify_debts(nets):
        paid[t.from_user_id] = paid.get(t.from_user_id, 0) + t.amount
        received[t.to_user_id] = received.get(t.to_user_id, 0) + t.amount
    assert all(total == -nets[user] for user, total in paid.items())
    assert all(total == nets[user] for user, total in received.items())


@given(balanced_nets())
def test_payments_come_biggest_first(nets):
    amounts = [t.amount for t in simplify_debts(nets)]
    assert amounts == sorted(amounts, reverse=True)


@given(balanced_nets())
def test_result_is_deterministic_and_independent_of_input_order(nets):
    reversed_nets = dict(reversed(list(nets.items())))
    assert simplify_debts(nets) == simplify_debts(reversed_nets) == simplify_debts(dict(nets))
