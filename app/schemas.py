"""Pydantic request/response schemas. Mirrors the API section of DESIGN.md.

These validate the *shape* of each request (types, blanks, positive numbers).
Rules that need the database or the whole request (members of the group,
shares adding up, percentages totalling 100) live in app/services and app/domain.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.domain.splits import SplitStrategy

# ---------- Reusable field types ----------

# Trims surrounding whitespace first, so "   " counts as blank and is rejected.
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
ExpenseName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]

# Optional leading +, then 7-15 digits (the E.164 limit). Spaces and dashes are not allowed.
PhoneNumber = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\+?[0-9]{7,15}$")]

# Largest amount accepted: 10^12 paise = ₹1000 crore per expense or settlement.
# A business limit far inside BIGINT (about 9.2 x 10^18), so oversized input is a 422,
# never an overflow (500) in Postgres.
MAX_PAISE = 10**12

# All money is an integer number of paise, from 1 to MAX_PAISE.
Paise = Annotated[int, Field(gt=0, le=MAX_PAISE)]


class ORMModel(BaseModel):
    """Base for responses built from SQLAlchemy objects."""

    model_config = ConfigDict(from_attributes=True)


# ---------- Users ----------


class UserCreate(BaseModel):
    phone_number: PhoneNumber
    user_name: Name


class UserRead(ORMModel):
    id: uuid.UUID
    phone_number: str
    user_name: str


# ---------- Groups ----------


class GroupCreate(BaseModel):
    name: Name


class GroupRead(ORMModel):
    id: uuid.UUID
    name: str


class MemberRead(ORMModel):
    # Built from a User object, whose primary key is called `id`.
    user_id: uuid.UUID = Field(validation_alias="id")
    user_name: str


class GroupDetail(GroupRead):
    members: list[MemberRead]


class AddMembersRequest(BaseModel):
    user_ids: list[uuid.UUID] = Field(min_length=1)


class MembersRead(BaseModel):
    members: list[MemberRead]


# ---------- Expenses ----------


class SplitEntry(BaseModel):
    user_id: uuid.UUID
    # equal      -> must be omitted
    # exact      -> paise this user owes (must be a whole number)
    # percentage -> percent this user owes, up to 2 decimal places (e.g. 33.33)
    value: Decimal | None = Field(default=None, gt=0, le=MAX_PAISE, decimal_places=2)


class ExpenseCreate(BaseModel):
    name: ExpenseName
    amount: Paise
    paid_by: uuid.UUID
    split_strategy: SplitStrategy
    # Empty is only valid for "equal" (= split between all members); checked in the service.
    splits: list[SplitEntry] = Field(default_factory=list)

    def fingerprint_data(self) -> dict:
        """Canonical form for the Idempotency-Key fingerprint: equivalent requests are equal.

        A client's retry may rebuild the body slightly differently, so splits are sorted by
        user (the order never changes the result) and values are normalised ("50" == "50.00").
        Names were already stripped by validation.
        """
        splits = [
            [str(entry.user_id), None if entry.value is None else str(entry.value.normalize())]
            for entry in self.splits
        ]
        return {
            "name": self.name,
            "amount": self.amount,
            "paid_by": str(self.paid_by),
            "split_strategy": self.split_strategy.value,
            "splits": sorted(splits, key=lambda pair: pair[0]),
        }


class ShareRead(ORMModel):
    user_id: uuid.UUID
    amount: int


class SplitRead(ORMModel):
    """One split entry as it was entered: the same shape as SplitEntry in the request."""

    user_id: uuid.UUID
    value: Decimal | None = Field(validation_alias="input_value")


class ExpenseRead(ORMModel):
    id: uuid.UUID
    name: str
    amount: int
    paid_by: uuid.UUID
    created_at: datetime
    updated_at: datetime | None
    # The split as entered, in the request's shape, so a client can pre-fill an edit form...
    split_strategy: SplitStrategy
    splits: list[SplitRead] = Field(validation_alias="shares")
    # ...and the calculated paise per person, which is what balances use.
    shares: list[ShareRead]


class ExpenseList(BaseModel):
    expenses: list[ExpenseRead]


# ---------- Settlements ----------


class SettlementCreate(BaseModel):
    payer_id: uuid.UUID
    payee_id: uuid.UUID
    amount: Paise

    @model_validator(mode="after")
    def payer_is_not_payee(self) -> "SettlementCreate":
        if self.payer_id == self.payee_id:
            raise ValueError("payer_id and payee_id must be different users")
        return self

    def fingerprint_data(self) -> dict:
        """Canonical form for the Idempotency-Key fingerprint."""
        return {"payer_id": str(self.payer_id), "payee_id": str(self.payee_id), "amount": self.amount}


class SettlementRead(ORMModel):
    id: uuid.UUID
    payer_id: uuid.UUID
    payee_id: uuid.UUID
    amount: int
    created_at: datetime
    updated_at: datetime | None


class SettlementList(BaseModel):
    settlements: list[SettlementRead]


# ---------- Balances ----------
# Sign convention everywhere: positive = is owed money, negative = owes money.


class MemberBalance(BaseModel):
    user_id: uuid.UUID
    user_name: str
    net: int


class GroupBalances(BaseModel):
    balances: list[MemberBalance]


class PairwiseBalance(BaseModel):
    user_id: uuid.UUID
    user_name: str
    amount: int  # positive = this user owes you


class UserGroupBalance(BaseModel):
    user_id: uuid.UUID
    net: int
    balances: list[PairwiseBalance]


class GroupNet(BaseModel):
    group_id: uuid.UUID
    group_name: str
    net: int


class UserBalances(BaseModel):
    user_id: uuid.UUID
    total_net: int
    groups: list[GroupNet]


# ---------- Debt simplification (stretch) ----------


class Payment(BaseModel):
    from_user_id: uuid.UUID
    to_user_id: uuid.UUID
    amount: int


class SimplifiedDebts(BaseModel):
    payments: list[Payment]
