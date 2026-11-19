"""SQLAlchemy models. Mirrors the schema in DESIGN.md.

All money columns are BIGINT paise. Balances are never stored; they are
calculated from expenses, expense_shares and settlements.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    MetaData,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from uuid6 import uuid7

# Deterministic constraint names, so constraints are easy to find and refer to
# (e.g. in error messages, or to drop or alter one later). Without this, Postgres invents the names.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    # All columns, so a multi-column constraint gets a meaningful name
    # (e.g. uq_expenses_group_id_idempotency_key).
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def idempotency_constraints() -> tuple:
    """Constraints for tables written by POSTs that accept an Idempotency-Key (decision 10).

    The key is unique per group: a retry of the same request finds the row it created.
    NULL keys (requests sent without the header) are distinct in Postgres, so any number
    of those are allowed. A key is always stored together with its request fingerprint.
    """
    return (
        UniqueConstraint("group_id", "idempotency_key"),
        CheckConstraint(
            "(idempotency_key IS NULL) = (request_fingerprint IS NULL)", name="idempotency_complete"
        ),
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    phone_number: Mapped[str] = mapped_column(String(20), unique=True)
    user_name: Mapped[str] = mapped_column(String(100))


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    name: Mapped[str] = mapped_column(String(100))

    # Ordered, so API responses are deterministic (same order as the balance endpoints).
    members: Mapped[list[User]] = relationship(
        secondary="group_members", order_by=(User.user_name, User.id)
    )


class GroupMember(Base):
    __tablename__ = "group_members"

    # Composite PK: a user can be in a group only once.
    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("groups.id"), primary_key=True
    )
    # Indexed separately: the PK index only helps lookups that start with group_id,
    # and "which groups is this user in?" starts with user_id.
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), primary_key=True, index=True
    )


class Expense(Base):
    __tablename__ = "expenses"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        *idempotency_constraints(),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    group_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("groups.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    amount: Mapped[int] = mapped_column(BigInteger)  # paise
    paid_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # NULL until the expense is first edited.
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Soft delete: NULL = active. Deleted expenses (and their shares) stay for the audit trail.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set when the POST carried an Idempotency-Key; never returned by the API.
    idempotency_key: Mapped[uuid.UUID | None]
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))  # SHA-256 hex

    # Saving an Expense saves its shares too; removing a share from the list deletes it
    # (used when an edit replaces the shares). Ordered by user_id: without an ORDER BY,
    # Postgres returns rows in storage order, which changes when an edit replaces them.
    shares: Mapped[list["ExpenseShare"]] = relationship(
        back_populates="expense",
        cascade="all, delete-orphan",
        order_by="ExpenseShare.user_id",
    )


# Every query that reads expenses, or sums their shares, must include this filter.
ACTIVE_EXPENSE = Expense.deleted_at.is_(None)


class ExpenseShare(Base):
    __tablename__ = "expense_shares"
    __table_args__ = (CheckConstraint("amount > 0", name="amount_positive"),)

    # Composite PK: a user has at most one share per expense.
    expense_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("expenses.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), primary_key=True, index=True
    )
    amount: Mapped[int] = mapped_column(BigInteger)  # paise

    expense: Mapped[Expense] = relationship(back_populates="shares")


class Settlement(Base):
    __tablename__ = "settlements"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("payer_id <> payee_id", name="payer_not_payee"),
        *idempotency_constraints(),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    group_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("groups.id"), index=True)
    payer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    payee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    amount: Mapped[int] = mapped_column(BigInteger)  # paise
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # NULL until the settlement is first edited.
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Soft delete: NULL = active.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set when the POST carried an Idempotency-Key; never returned by the API.
    idempotency_key: Mapped[uuid.UUID | None]
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))  # SHA-256 hex


# Every query that reads settlements must include this filter.
ACTIVE_SETTLEMENT = Settlement.deleted_at.is_(None)
