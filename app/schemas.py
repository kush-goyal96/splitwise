"""Pydantic request/response schemas. Mirrors the API section of DESIGN.md.

These validate the *shape* of each request (types, blanks, positive numbers).
Rules that need the database or the whole request (members of the group,
shares adding up, percentages totalling 100) live in app/services.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, StringConstraints, model_validator

# ---------- Reusable field types ----------

# Trims surrounding whitespace first, so "   " counts as blank and is rejected.
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
ExpenseName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]

# Optional leading +, then 7-15 digits (the E.164 limit). Spaces and dashes are not allowed.
PhoneNumber = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\+?[0-9]{7,15}$")]

# All money is an integer number of paise.
Paise = PositiveInt


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


class SplitStrategy(str, Enum):
    EQUAL = "equal"
    EXACT = "exact"
    PERCENTAGE = "percentage"


class SplitEntry(BaseModel):
    user_id: uuid.UUID
    # equal      -> must be omitted
    # exact      -> paise this user owes (must be a whole number)
    # percentage -> percent this user owes, up to 2 decimal places (e.g. 33.33)
    value: Decimal | None = Field(default=None, gt=0, decimal_places=2)


class ExpenseCreate(BaseModel):
    name: ExpenseName
    amount: Paise
    paid_by: uuid.UUID
    split_strategy: SplitStrategy
    # Empty is only valid for "equal" (= split between all members); checked in the service.
    splits: list[SplitEntry] = Field(default_factory=list)


class ShareRead(ORMModel):
    user_id: uuid.UUID
    amount: int


class ExpenseRead(ORMModel):
    id: uuid.UUID
    name: str
    amount: int
    paid_by: uuid.UUID
    created_at: datetime
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


class SettlementRead(ORMModel):
    id: uuid.UUID
    payer_id: uuid.UUID
    payee_id: uuid.UUID
    amount: int
    created_at: datetime


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
