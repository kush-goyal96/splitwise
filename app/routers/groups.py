import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.database import DbSession
from app.deps import get_group_or_404
from app.models import Group, GroupMember, User
from app.schemas import AddMembersRequest, GroupCreate, GroupDetail, GroupRead, MembersRead

router = APIRouter(prefix="/groups", tags=["groups"])


@router.post("", response_model=GroupRead, status_code=status.HTTP_201_CREATED)
def create_group(payload: GroupCreate, db: DbSession) -> Group:
    group = Group(name=payload.name)
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


@router.get("/{group_id}", response_model=GroupDetail)
def get_group(group_id: uuid.UUID, db: DbSession) -> Group:
    return get_group_or_404(db, group_id, with_members=True)


@router.post("/{group_id}/members", response_model=MembersRead)
def add_members(group_id: uuid.UUID, payload: AddMembersRequest, db: DbSession) -> MembersRead:
    group = get_group_or_404(db, group_id)

    # An id listed twice in the request is added once.
    requested = set(payload.user_ids)

    # One query for all ids, not one per id.
    found = set(db.scalars(select(User.id).where(User.id.in_(requested))))
    missing = requested - found
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "Users not found", "user_ids": sorted(str(u) for u in missing)},
        )

    # Idempotent: users already in the group are skipped by ON CONFLICT DO NOTHING.
    # The database resolves it, so two concurrent requests adding the same user
    # can't race each other into a duplicate-key error.
    db.execute(
        insert(GroupMember)
        .values([{"group_id": group.id, "user_id": user_id} for user_id in requested])
        .on_conflict_do_nothing(index_elements=["group_id", "user_id"])
    )
    db.commit()

    # The commit expired `group`, so this reloads the full, current member list.
    return MembersRead.model_validate({"members": group.members}, from_attributes=True)
