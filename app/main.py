import logging
from typing import Annotated

from fastapi import Depends, FastAPI, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_db

logger = logging.getLogger(__name__)

app = FastAPI()
DbSession = Annotated[Session, Depends(get_db)]


@app.get("/health")
def health_check(db: DbSession):
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.exception("Health check failed: database unreachable")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "unhealthy",
                "app": "ok",
                "database": "disconnected",
            },
        )
    return {
        "status": "healthy",
        "app": "ok",
        "database": "connected",
    }
