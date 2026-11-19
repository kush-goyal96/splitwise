import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, selectinload

from app.errors import NotFoundError, RuleViolationError
from app.models import Group, GroupMember, User


def get_group(db: Session, group_id: uuid.UUID, *, with_members: bool = False) -> Group:
    options = [selectinload(Group.members)] if with_members else []
    group = db.get(Group, group_id, options=options)
    if group is None:
        raise NotFoundError("Group not found")
    return group


def create_group(db: Session, name: str) -> Group:
    group = Group(name=name)
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


def add_members(db: Session, group_id: uuid.UUID, user_ids: Iterable[uuid.UUID]) -> list[User]:
    """Add users to a group and return the full member list afterwards.

    Idempotent: users already in the group are skipped, and an id listed twice is added once.
    """
    group = get_group(db, group_id)

    requested = set(user_ids)

    # One query for all ids, not one per id.
    found = set(db.scalars(select(User.id).where(User.id.in_(requested))))
    missing = requested - found
    if missing:
        raise NotFoundError(f"Users not found: {_format_ids(missing)}")

    # ON CONFLICT DO NOTHING skips existing members inside the database, so two
    # concurrent requests adding the same user can't race into a duplicate-key error.
    db.execute(
        insert(GroupMember)
        .values([{"group_id": group.id, "user_id": user_id} for user_id in requested])
        .on_conflict_do_nothing(index_elements=["group_id", "user_id"])
    )
    db.commit()

    # The commit expired `group`, so this reloads the full, current member list.
    return group.members


# ---------- The membership rule, used by every use case that involves group members ----------


def get_member_ids(db: Session, group_id: uuid.UUID) -> set[uuid.UUID]:
    return set(db.scalars(select(GroupMember.user_id).where(GroupMember.group_id == group_id)))


def require_members(member_ids: set[uuid.UUID], user_ids: Iterable[uuid.UUID], field: str) -> None:
    """Rule: everyone involved in a group transaction must be a member of that group."""
    outsiders = set(user_ids) - member_ids
    if outsiders:
        raise RuleViolationError(f"{field}: not a member of the group: {_format_ids(outsiders)}")


def _format_ids(ids: Iterable[uuid.UUID]) -> str:
    return ", ".join(sorted(str(i) for i in ids))
