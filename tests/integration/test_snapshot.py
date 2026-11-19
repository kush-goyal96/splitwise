"""Decision 2: each balance use case reads from one REPEATABLE READ snapshot.

A balance is several SUM queries. Under READ COMMITTED (Postgres' default) each query sees
its own snapshot, so a write committed between two of them is half-counted: e.g. an
expense's amount missed by the "paid" query but its shares counted by the "shares" query,
and the nets no longer sum to 0.

These tests force exactly that interleaving: _totals is wrapped so that right after the
first SUM query, another connection commits a new expense. The result must be exactly the
balances from before the write. A control test proves the interleaving really happens.
"""

import uuid

import pytest
from sqlalchemy import text

from app.database import SessionLocal, engine, use_snapshot
from app.domain.splits import SplitStrategy
from app.services import balances as balances_service
from app.services import expenses as expenses_service


@pytest.fixture
def write_during_read(monkeypatch, trip):
    """Returns arm(): after it is called, the next balance read gets a concurrent write
    (₹900 paid by Kush, split between all three) committed after its first SUM query."""
    original_totals = balances_service._totals
    state = {"armed": False, "written": False}

    def totals_then_write(db, stmt):
        result = original_totals(db, stmt)
        if state["armed"] and not state["written"]:
            state["written"] = True
            with SessionLocal() as other_connection:
                expenses_service.create_expense(
                    other_connection, uuid.UUID(trip.group), name="Concurrent", amount=90000,
                    paid_by=uuid.UUID(trip.kush), strategy=SplitStrategy.EQUAL, splits=[],
                    idempotency_key=uuid.uuid4(), fingerprint="0" * 64,
                )
        return result

    monkeypatch.setattr(balances_service, "_totals", totals_then_write)

    def arm():
        state["armed"] = True
        return state

    return arm


@pytest.fixture
def some_activity(api, trip):
    api.add_expense(trip.group, amount=30000, paid_by=trip.shivanshu)
    api.settle(trip.group, trip.ansh, trip.shivanshu, 5000)


def test_group_balances_read_one_snapshot(api, trip, db, some_activity, write_during_read):
    before = api.nets(trip.group)
    state = write_during_read()

    rows = balances_service.group_balances(db, uuid.UUID(trip.group))

    assert state["written"]
    assert {str(row.user_id): row.net for row in rows} == before
    assert api.nets(trip.group) != before  # the write did commit; a new read sees it


def test_user_balances_read_one_snapshot(api, trip, db, some_activity, write_during_read):
    before = api.user_balances(trip.kush)["groups"]
    state = write_during_read()

    rows = balances_service.user_balances(db, uuid.UUID(trip.kush))

    assert state["written"]
    assert [{"group_id": str(r.group_id), "group_name": r.group_name, "net": r.net} for r in rows] == before
    assert api.user_balances(trip.kush)["groups"] != before


def test_control_without_the_snapshot_the_write_is_half_counted(
    api, trip, db, some_activity, write_during_read, monkeypatch
):
    """Proves the tests above are not vacuous: with use_snapshot disabled, the same
    interleaving really does produce nets that don't sum to 0."""
    monkeypatch.setattr(balances_service, "use_snapshot", lambda db: None)
    write_during_read()

    rows = balances_service.group_balances(db, uuid.UUID(trip.group))

    assert sum(row.net for row in rows) != 0


def test_use_snapshot_sets_repeatable_read(db):
    use_snapshot(db)
    assert db.execute(text("SHOW transaction_isolation")).scalar() == "repeatable read"


def test_writes_use_read_committed(db):
    # The row-lock re-check in _get_expense / _get_settlement depends on this.
    assert db.execute(text("SHOW transaction_isolation")).scalar() == "read committed"


def test_snapshot_isolation_does_not_leak_into_the_next_request():
    # Pooled connections are reused: the isolation level must be reset when one is returned.
    engine.dispose()  # empty pool, so the second session gets the same connection back
    with SessionLocal() as first:
        use_snapshot(first)
        first_pid = first.execute(text("SELECT pg_backend_pid()")).scalar()
    with SessionLocal() as second:
        assert second.execute(text("SELECT pg_backend_pid()")).scalar() == first_pid
        assert second.execute(text("SHOW transaction_isolation")).scalar() == "read committed"
