import uuid

from fastapi import APIRouter, status

from app.database import DbSession
from app.models import Group
from app.schemas import AddMembersRequest, GroupCreate, GroupDetail, GroupRead, MembersRead
from app.services import groups as service

router = APIRouter(prefix="/groups", tags=["groups"])


@router.post("", response_model=GroupRead, status_code=status.HTTP_201_CREATED)
def create_group(payload: GroupCreate, db: DbSession) -> Group:
    return service.create_group(db, payload.name)


@router.get("/{group_id}", response_model=GroupDetail)
def get_group(group_id: uuid.UUID, db: DbSession) -> Group:
    return service.get_group(db, group_id, with_members=True)


@router.post("/{group_id}/members", response_model=MembersRead)
def add_members(group_id: uuid.UUID, payload: AddMembersRequest, db: DbSession) -> MembersRead:
    members = service.add_members(db, group_id, payload.user_ids)
    return MembersRead.model_validate({"members": members}, from_attributes=True)
