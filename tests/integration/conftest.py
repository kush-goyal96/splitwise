"""Fixtures for the integration tests: a real Postgres database, the app, and API helpers.

These tests run against Postgres, not SQLite, because much of what they check is Postgres
behaviour: ON CONFLICT, constraint names reported by psycopg, REPEATABLE READ snapshots
and SELECT ... FOR UPDATE row locks.

Safety: every test empties every table, so DATABASE_URL must name a database ending in
"_test". The database is created on first use and its schema is rebuilt at the start of
every run, so model changes never need a manual reset.

Run with:  docker compose run --rm --build test
"""

import os
import re
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

_database_url = os.environ.get("DATABASE_URL")
if not _database_url:
    pytest.exit("DATABASE_URL is not set. Run the tests with: docker compose run --rm --build test", returncode=2)
_url = make_url(_database_url)
if not re.fullmatch(r"[a-z0-9_]+_test", _url.database or ""):
    pytest.exit(
        f"Refusing to run: the tests empty every table, so DATABASE_URL must point at a "
        f"database whose name ends in _test (got {_url.database!r}).",
        returncode=2,
    )

# Imported after the checks above: app.database builds its engine from DATABASE_URL.
from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402
from tests.integration.helpers import Api  # noqa: E402


def _create_test_database() -> None:
    # CREATE DATABASE can't run inside a transaction, and can't target the database it is
    # connected to, so connect to the server's maintenance database with autocommit.
    admin = create_engine(_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": _url.database}
            )
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{_url.database}"'))  # name checked above
    finally:
        admin.dispose()


@pytest.fixture(scope="session", autouse=True)
def database():
    _create_test_database()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_tables(database):
    """Every test starts with empty tables.

    Truncating (not rolling back a wrapping transaction) means the app runs exactly as in
    production: real commits, real isolation levels, and real locks between connections.
    """
    tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} CASCADE"))


@pytest.fixture(scope="session")
def client(database):
    # As a context manager, TestClient runs the app's lifespan (startup and shutdown).
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def api(client) -> Api:
    return Api(client)


@pytest.fixture
def db():
    """A database session for tests that call services or the database directly."""
    with SessionLocal() as session:
        yield session


@pytest.fixture
def trip(api):
    """The running example from DESIGN.md: a group of three, plus Riya who is not a member."""
    kush = api.user("Kush")
    shivanshu = api.user("Shivanshu")
    ansh = api.user("Ansh")
    riya = api.user("Riya")
    group = api.group("Goa Trip", members=[kush, shivanshu, ansh])
    return SimpleNamespace(
        group=group, kush=kush, shivanshu=shivanshu, ansh=ansh, riya=riya,
        members=[kush, shivanshu, ansh],
    )
