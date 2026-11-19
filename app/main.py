import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.database import DbSession, engine
from app.errors import ConflictError, DomainError, NotFoundError, RuleViolationError
from app.models import Base
from app.routers import balances, expenses, groups, settlements, users

logger = logging.getLogger(__name__)

# The one place where domain errors become HTTP status codes.
DOMAIN_ERROR_STATUS = {
    NotFoundError: status.HTTP_404_NOT_FOUND,
    RuleViolationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    ConflictError: status.HTTP_409_CONFLICT,
}


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
app.include_router(balances.router)


@app.exception_handler(DomainError)
def handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
    # Subclasses (e.g. SplitError) map through their base class.
    status_code = next(
        code for error_type, code in DOMAIN_ERROR_STATUS.items() if isinstance(exc, error_type)
    )
    return JSONResponse(status_code=status_code, content={"detail": str(exc)})


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

