from fastapi import APIRouter, status

from app.database import DbSession
from app.models import User
from app.schemas import UserCreate, UserRead
from app.services import users as service

router = APIRouter(prefix="/users", tags=["users"])


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate, db: DbSession) -> User:
    return service.create_user(db, payload.phone_number, payload.user_name)
