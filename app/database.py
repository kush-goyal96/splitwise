import os
from typing import Annotated

from fastapi import Depends
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


DbSession = Annotated[Session, Depends(get_db)]


def use_snapshot(db: Session) -> None:
    """Make every read in the current transaction see one consistent snapshot of the database.

    Postgres' default (READ COMMITTED) gives each query its own snapshot, so a use case that
    runs several queries could see a write that lands between them. REPEATABLE READ takes one
    snapshot at the transaction's first query. Must be called before that first query.
    """
    db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
