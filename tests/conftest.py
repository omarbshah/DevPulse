import os

# These must be set BEFORE the app modules are imported: database.py builds its
# engine at import time. Using an in-memory SQLite DB means the tests can never
# touch the real (.env) database.
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["SECRET_KEY"] = "test-secret-key-used-only-by-pytest-0123456789"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import cache
import models  # noqa: F401  (registers the tables on Base.metadata)
from database import Base, get_db
from main import app


@pytest.fixture()
def client():
    """A TestClient wired to a fresh in-memory database and an empty cache."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # one shared connection, so the in-memory DB survives
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    cache._store.clear()

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()
    cache._store.clear()


@pytest.fixture()
def auth(client):
    """Sign up + log in a user; returns (user_id, headers)."""
    r = client.post(
        "/signup",
        json={"username": "alice", "email": "alice@example.com", "password": "password123"},
    )
    assert r.status_code == 201
    user_id = r.json()["id"]

    r = client.post("/login", json={"email": "alice@example.com", "password": "password123"})
    assert r.status_code == 200
    return user_id, {"Authorization": f"Bearer {r.json()['access_token']}"}
