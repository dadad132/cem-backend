"""Domain and reverse-proxy handling for tickets.hubconnect.co.za."""
import pytest
from httpx import ASGITransport, AsyncClient

from app import main as app_main
from app.core.config import Settings
from app.main import app


def test_default_domain_settings_include_tickets_hubconnect():
    cfg = Settings(_env_file=None)
    assert cfg.app_domain == "tickets.hubconnect.co.za"
    assert "tickets.hubconnect.co.za" in cfg.allowed_hosts
    assert cfg.public_base_url == "http://tickets.hubconnect.co.za"

    https_cfg = Settings(_env_file=None, APP_HTTPS_ONLY=True)
    assert https_cfg.public_base_url == "https://tickets.hubconnect.co.za"

    # Extra .env keys (like MESHCENTRAL_DIR written by setup_meshcentral.sh or SMTP_*) must never crash startup
    extra_cfg = Settings(
        _env_file=None,
        MESHCENTRAL_DIR="/opt/meshcentral",
        SMTP_HOST="smtp.example.com",
        UNKNOWN_CUSTOM_VAR="ok",
    )
    assert extra_cfg.meshcentral_dir == "/opt/meshcentral"


@pytest.mark.asyncio
@pytest.mark.parametrize("scheme", ["http", "https"])
async def test_app_accepts_tickets_hubconnect_host(scheme):
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url=f"{scheme}://tickets.hubconnect.co.za",
    ) as client:
        health = await client.get("/health")
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}

        landing = await client.get("/", follow_redirects=False)
        assert landing.status_code in (200, 303)

        login = await client.get("/web/login", follow_redirects=False)
        assert login.status_code == 200


@pytest.mark.asyncio
async def test_proxy_headers_from_local_nginx_enable_https_and_hsts(monkeypatch):
    monkeypatch.setattr(app_main.settings, "debug", False)
    transport = ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with AsyncClient(transport=transport, base_url="http://tickets.hubconnect.co.za") as client:
        response = await client.get(
            "/health",
            headers={
                "Host": "tickets.hubconnect.co.za",
                "X-Forwarded-Proto": "https",
                "X-Forwarded-For": "196.25.1.10",
            },
        )
    assert response.status_code == 200
    assert response.headers["strict-transport-security"] == "max-age=31536000"


@pytest.mark.asyncio
async def test_untrusted_forwarded_proto_is_ignored(monkeypatch):
    monkeypatch.setattr(app_main.settings, "debug", False)
    transport = ASGITransport(app=app, client=("203.0.113.55", 50000))
    async with AsyncClient(transport=transport, base_url="http://tickets.hubconnect.co.za") as client:
        response = await client.get(
            "/health",
            headers={
                "Host": "tickets.hubconnect.co.za",
                "X-Forwarded-Proto": "https",
            },
        )
    assert response.status_code == 200
    assert "strict-transport-security" not in response.headers
