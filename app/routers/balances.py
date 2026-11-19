import uuid

from fastapi import APIRouter

from app.database import DbSession
from app.schemas import (
    GroupBalances,
    GroupNet,
    MemberBalance,
    PairwiseBalance,
    UserBalances,
    UserGroupBalance,
)
from app.services import balances as service

# No prefix: these endpoints live under both /groups and /users.
router = APIRouter(tags=["balances"])


@router.get("/groups/{group_id}/balances", response_model=GroupBalances)
def get_group_balances(group_id: uuid.UUID, db: DbSession) -> GroupBalances:
    rows = service.group_balances(db, group_id)
    return GroupBalances(balances=[MemberBalance(**row._asdict()) for row in rows])


@router.get("/groups/{group_id}/balances/{user_id}", response_model=UserGroupBalance)
def get_user_balance_in_group(
    group_id: uuid.UUID, user_id: uuid.UUID, db: DbSession
) -> UserGroupBalance:
    rows = service.user_balance_in_group(db, group_id, user_id)
    return UserGroupBalance(
        user_id=user_id,
        # The pairwise amounts always add up to the user's group net.
        net=sum(row.amount for row in rows),
        balances=[PairwiseBalance(**row._asdict()) for row in rows],
    )


@router.get("/users/{user_id}/balances", response_model=UserBalances)
def get_user_balances(user_id: uuid.UUID, db: DbSession) -> UserBalances:
    rows = service.user_balances(db, user_id)
    return UserBalances(
        user_id=user_id,
        total_net=sum(row.net for row in rows),
        groups=[GroupNet(**row._asdict()) for row in rows],
    )
