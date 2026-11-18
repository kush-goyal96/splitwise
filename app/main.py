import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.database import DbSession, engine
from app.models import Base
from app.routers import expenses, groups, settlements, users

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Runs when the application starts
    Base.metadata.create_all(bind=engine)

    yield
    engine.dispose()


app = FastAPI(lifespan=lifespan)
app.include_router(users.router)
app.include_router(groups.router)
app.include_router(expenses.router)
app.include_router(settlements.router)


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

