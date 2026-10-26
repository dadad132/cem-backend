"""Security headers: HSTS only over HTTPS, and no outdated XSS filter."""
import pytest
from httpx import ASGITransport, AsyncClient

from app import main as app_main
from app.main import app


@pytest.mark.asyncio
@pytest.mark.parametrize('base_url, https', [('http://test', False), ('https://test', True)])
async def test_hsts_only_over_https(base_url, https, monkeypatch):
    monkeypatch.setattr(app_main.settings, 'debug', False)  # as on a production server (APP_DEBUG=false)
    async with AsyncClient(transport=ASGITransport(app=app), base_url=base_url) as client:
        response = await client.get('/health')
    if https:
        # No includeSubDomains: other names under the domain are not ours to force onto HTTPS
        assert response.headers['strict-transport-security'] == 'max-age=31536000'
    else:
        assert 'strict-transport-security' not in response.headers, 'ignored over plain HTTP anyway'
    assert response.headers['x-xss-protection'] == '0'
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert response.headers['x-frame-options'] == 'SAMEORIGIN'
