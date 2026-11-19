"""The health check, and the API-wide error conventions from DESIGN.md:
the status code carries success or failure, and errors return {"detail": ...}."""

import logging

from sqlalchemy.exc import OperationalError

from app.database import get_db
from app.main import app
from tests.integration.helpers import random_id


def test_health_check_reports_a_connected_database(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "app": "ok", "database": "connected"}


def test_health_check_reports_503_and_logs_when_the_database_is_unreachable(client, caplog):
    class UnreachableDatabase:
        def execute(self, *args, **kwargs):
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    def unreachable_db():
        yield UnreachableDatabase()

    app.dependency_overrides[get_db] = unreachable_db
    try:
        with caplog.at_level(logging.ERROR):
            response = client.get("/health")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {"status": "unhealthy", "app": "ok", "database": "disconnected"}
    assert "Health check failed" in caplog.text


def test_error_body_shapes(client):
    # Domain errors (one exception handler in main.py): detail is a readable string.
    response = client.get(f"/groups/{random_id()}")
    assert (response.status_code, response.json()) == (404, {"detail": "Group not found"})

    # Request-shape errors (Pydantic): detail is a list of problems with their location.
    response = client.post("/groups", json={"name": ""})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "name"]

    # A malformed id in the path is a 422, not a 404 or a 500.
    assert client.get("/groups/not-a-uuid").status_code == 422
