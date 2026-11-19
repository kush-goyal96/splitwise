import uuid

from fastapi import APIRouter

from app.database import DbSession
from app.schemas import (
    GroupBalances,
    GroupNet,
    MemberBalance,
    PairwiseBalance,
    Payment,
    SimplifiedDebts,
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
    result = service.user_balance_in_group(db, group_id, user_id)
    return UserGroupBalance(
        user_id=user_id,
        net=result.net,
        balances=[PairwiseBalance(**row._asdict()) for row in result.balances],
    )


@router.get("/groups/{group_id}/simplified-debts", response_model=SimplifiedDebts)
def get_simplified_debts(group_id: uuid.UUID, db: DbSession) -> SimplifiedDebts:
    transfers = service.simplified_debts(db, group_id)
    return SimplifiedDebts(payments=[Payment(**t._asdict()) for t in transfers])


@router.get("/users/{user_id}/balances", response_model=UserBalances)
def get_user_balances(user_id: uuid.UUID, db: DbSession) -> UserBalances:
    result = service.user_balances(db, user_id)
    return UserBalances(
        user_id=user_id,
        total_net=result.total_net,
        groups=[GroupNet(**row._asdict()) for row in result.groups],
    )
