"""Lookups shared by several routers."""

import uuid

from fastapi import HTTPException, status
from sqlalchemy.orm import Session, selectinload

from app.models import Group


def get_group_or_404(db: Session, group_id: uuid.UUID, *, with_members: bool = False) -> Group:
    options = [selectinload(Group.members)] if with_members else []
    group = db.get(Group, group_id, options=options)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    return group
