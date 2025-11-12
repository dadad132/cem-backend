import os
import sys
import uuid
import importlib
import pytest
from httpx import AsyncClient, ASGITransport


@pytest.mark.asyncio
async def test_api_signup_and_login():
    email = f"user_{uuid.uuid4().hex[:8]}@example.com"
    password = "Passw0rd!"
    # Use an isolated SQLite file per test run to ensure schema matches models
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///./test_{uuid.uuid4().hex[:8]}.db"
    # Import or reload the app after setting DATABASE_URL
    # Ensure fresh modules so settings and engine pick up new env
    for mod in ("app.core.database", "app.core.config", "app.main"):
        sys.modules.pop(mod, None)
    from app.main import app  # type: ignore
    from app.core.config import get_settings  # type: ignore
    settings = get_settings()
    assert "test_" in settings.database_url
    # Ensure metadata has workspace_id in user table
    from sqlmodel import SQLModel  # type: ignore
    user_table = SQLModel.metadata.tables.get("user")
    assert user_table is not None
    assert "workspace_id" in user_table.c

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Signup
        r = await client.post("/api/auth/signup", json={
            "email": email,
            "password": password,
            "full_name": "Test User",
        })
        assert r.status_code == 201, r.text
        data = r.json()
        assert data["email"] == email
        assert "id" in data

        # Login
        r2 = await client.post("/api/auth/login", json={
            "email": email,
            "password": password,
        })
        assert r2.status_code == 200, r2.text
        tokens = r2.json()
        assert "access_token" in tokens and "refresh_token" in tokens
