"""Tests for MeshCentral Remote Client Access dashboard, SSO tokens, sync, and tenant isolation."""
from __future__ import annotations

import json
import secrets
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel, select

from app.core import meshcentral as mc
from app.models.meshcentral import (
    MeshCentralDevice,
    MeshCentralSessionLog,
    MeshCentralSettings,
)
from app.models.user import User
from app.models.workspace import Workspace
from app.web.meshcentral_routes import (
    web_remote_dashboard,
    web_remote_device_create,
    web_remote_device_delete,
    web_remote_device_notes,
    web_remote_device_pin,
    web_remote_session,
    web_remote_session_embed_url,
    web_remote_settings,
    web_remote_settings_save,
    web_remote_sync,
)


@pytest.fixture(autouse=True)
def _legacy_template_call(monkeypatch):
    """Bridge legacy TemplateResponse shape used by app.web.routes in this venv."""
    import app.web.routes as routes
    from fastapi.templating import Jinja2Templates

    def render(name, context, *args, **kwargs):
        return Jinja2Templates.TemplateResponse(
            routes.templates, context.get("request"), name, context, *args, **kwargs
        )

    monkeypatch.setattr(routes, "_original_template_response", render)


def _request(user_id=None, path="/web/remote"):
    return SimpleNamespace(
        session={"user_id": user_id} if user_id is not None else {},
        state=SimpleNamespace(),
        headers={},
        url=SimpleNamespace(path=path, scheme="https", netloc="crm.test"),
        query_params={},
    )


async def _seed(tmp_path):
    import app.models  # noqa: F401

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'mesh.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    db = AsyncSession(engine, expire_on_commit=False)

    ws1 = Workspace(name="Acme IT", timezone="UTC")
    ws2 = Workspace(name="Rival MSP", timezone="UTC")
    db.add_all([ws1, ws2])
    await db.flush()

    admin = User(
        username="admin", full_name="Alice Admin", hashed_password="x",
        email="admin@acme.test", workspace_id=ws1.id, is_admin=True, is_active=True,
    )
    tech = User(
        username="tech", full_name="Tom Tech", hashed_password="x",
        email="tech@acme.test", workspace_id=ws1.id, is_admin=False, is_active=True,
    )
    other_admin = User(
        username="other", full_name="Olivia Other", hashed_password="x",
        email="other@rival.test", workspace_id=ws2.id, is_admin=True, is_active=True,
    )
    db.add_all([admin, tech, other_admin])
    await db.flush()
    await db.commit()
    return db, engine, ws1, ws2, admin, tech, other_admin


@pytest_asyncio.fixture
async def seeded(tmp_path):
    db, engine, *rest = await _seed(tmp_path)
    try:
        yield (db, *rest)
    finally:
        await db.close()
        await engine.dispose()


# ---------------------------------------------------------------------------
# Protocol & cryptographic helpers
# ---------------------------------------------------------------------------

def test_login_token_aes_gcm_roundtrip_and_urlsafe_alphabet():
    key_hex = secrets.token_hex(80)  # MeshCentral --loginTokenKey is 80 bytes hex
    token = mc.generate_login_token(key_hex, "user//admin", now=1700000000)

    assert "+" not in token
    assert "/" not in token
    assert "=" not in token

    decoded = mc.decode_login_token(token, key_hex)
    assert decoded == {"u": "user//admin", "a": 3, "time": 1700000000}


def test_secret_encryption_roundtrip():
    cipher = mc.encrypt_secret("super-secret-password")
    assert cipher and "super-secret-password" not in cipher
    assert mc.decrypt_secret(cipher) == "super-secret-password"


def test_url_and_node_normalization_rejects_unsafe_inputs():
    assert mc.normalize_server_url("https://mesh.example.com/") == "https://mesh.example.com"
    assert mc.normalize_server_url("http://10.0.0.5:4433/mc/") == "http://10.0.0.5:4433/mc"
    assert mc.normalize_node_id("node//UkSNlz7tABC123") == "UkSNlz7tABC123"
    assert mc.normalize_node_id("node/tenant1/UkSNlz7tABC123") == "UkSNlz7tABC123"
    assert mc.normalize_node_id("UkSNlz7tABC123") == "UkSNlz7tABC123"

    for bad in ("javascript:alert(1)", "ftp://mesh.example.com", "https://user:pass@mesh.example.com", "not a url"):
        with pytest.raises(ValueError):
            mc.normalize_server_url(bad)


def test_build_embed_url_includes_sso_token_viewmode_and_hide():
    key_hex = secrets.token_hex(32)
    cfg = MeshCentralSettings(
        workspace_id=1,
        enabled=True,
        server_url="https://mesh.acme.test",
        domain="",
        username="admin",
        login_token_key_encrypted=mc.encrypt_secret(key_hex),
        default_view_mode=11,
        hide_mesh_chrome=True,
    )
    embed = mc.build_embed_url(cfg, "node//PCNodeABC123", mode="terminal")
    parsed = urlparse(embed["url"])
    qs = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "mesh.acme.test"
    assert qs["gotonode"] == ["PCNodeABC123"]
    assert qs["viewmode"] == ["12"]
    assert qs["hide"] == [str(mc.HIDE_CHROME_BITMASK)]
    assert embed["sso_active"] is True

    decoded = mc.decode_login_token(qs["login"][0], key_hex)
    assert decoded["u"] == "user//admin"
    assert decoded["a"] == 3


def test_parse_inventory_messages_extracts_groups_and_online_status():
    messages = [
        {"action": "meshes", "meshes": [{"_id": "mesh//G1", "name": "Acme Accounting"}]},
        {
            "action": "nodes",
            "nodes": {
                "mesh//G1": [
                    {
                        "_id": "node//N1AAA",
                        "name": "FINANCE-01",
                        "rname": "DESKTOP-FIN01",
                        "osdesc": "Microsoft Windows 11 Pro",
                        "ip": "192.168.10.12",
                        "conn": 1,
                        "pwr": 1,
                    },
                    {
                        "_id": "node//N2BBB",
                        "name": "BACKUP-SRV",
                        "osdesc": "Ubuntu 24.04 LTS",
                        "ip": "192.168.10.20",
                        "conn": 0,
                        "pwr": 0,
                    },
                ]
            },
        },
    ]
    meshes, devices = mc.parse_inventory_messages(messages)
    assert meshes == {"mesh//G1": "Acme Accounting"}
    assert len(devices) == 2
    assert devices[0]["node_id"] == "N1AAA"
    assert devices[0]["mesh_name"] == "Acme Accounting"
    assert devices[0]["is_online"] is True
    assert devices[0]["os_family"] == "windows"
    assert devices[1]["is_online"] is False
    assert devices[1]["os_family"] == "linux"


# ---------------------------------------------------------------------------
# Routes, settings, custom dashboard rendering, session logs & tenant isolation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_settings_admin_only_and_never_leaks_secrets(seeded):
    db, ws1, _ws2, admin, tech, _other = seeded
    key_hex = secrets.token_hex(32)

    # Non-admin blocked from settings
    with pytest.raises(HTTPException) as exc:
        await web_remote_settings(_request(tech.id, "/web/remote/settings"), db)
    assert exc.value.status_code == 403

    # Admin saves settings
    req = _request(admin.id, "/web/remote/settings/save")
    resp = await web_remote_settings_save(
        req,
        enabled="1",
        server_url="https://mesh.acme.test/",
        domain="",
        username="meshadmin",
        password="PlainTextPassword123",
        clear_password=None,
        login_token_key=key_hex,
        clear_login_token_key=None,
        login_user_id="",
        default_view_mode=11,
        hide_mesh_chrome="1",
        verify_ssl="1",
        db=db,
    )
    assert resp.status_code == 303

    saved = (
        await db.execute(select(MeshCentralSettings).where(MeshCentralSettings.workspace_id == ws1.id))
    ).scalar_one()
    assert saved.enabled is True
    assert saved.server_url == "https://mesh.acme.test"
    assert saved.password_encrypted != "PlainTextPassword123"
    assert saved.login_token_key_encrypted != key_hex

    # Rendered settings page must never expose plaintext secrets
    page = await web_remote_settings(_request(admin.id, "/web/remote/settings"), db)
    html = bytes(page.body).decode()
    assert "MeshCentral Remote Access Setup" in html
    assert "https://mesh.acme.test" in html
    assert "PlainTextPassword123" not in html
    assert key_hex not in html


@pytest.mark.asyncio
async def test_dashboard_device_lifecycle_sync_and_session_embedding(seeded, monkeypatch):
    db, ws1, ws2, admin, tech, other_admin = seeded
    key_hex = secrets.token_hex(32)

    db.add(
        MeshCentralSettings(
            workspace_id=ws1.id,
            enabled=True,
            server_url="https://mesh.acme.test",
            username="meshadmin",
            password_encrypted=mc.encrypt_secret("pw"),
            login_token_key_encrypted=mc.encrypt_secret(key_hex),
            default_view_mode=11,
            hide_mesh_chrome=True,
        )
    )
    await db.commit()

    # Tech adds a device manually
    add_resp = await web_remote_device_create(
        _request(tech.id, "/web/remote/devices"),
        name="Reception PC",
        node_id="node//RECEP001",
        client_name="Northwind Client",
        os_desc="Windows 11 Pro",
        ip_address="192.168.1.50",
        notes="Ask Sarah before rebooting",
        db=db,
    )
    assert add_resp.status_code == 303

    dev = (
        await db.execute(select(MeshCentralDevice).where(MeshCentralDevice.workspace_id == ws1.id))
    ).scalar_one()
    assert dev.node_id == "RECEP001"
    assert dev.os_family == "windows"

    # Pin toggle + notes update
    pin_resp = await web_remote_device_pin(_request(tech.id), dev.id, db)
    assert json.loads(pin_resp.body.decode())["is_pinned"] is True

    notes_resp = await web_remote_device_notes(_request(tech.id), dev.id, notes="Updated note", client_name="Northwind HQ", db=db)
    assert json.loads(notes_resp.body.decode())["notes"] == "Updated note"

    # Mock WebSocket sync preserves custom notes/pins and adds new remote device
    async def fake_fetch(settings_row, timeout=12.0):
        return (
            {"mesh//M1": "Northwind Client"},
            [
                {
                    "node_id": "RECEP001",
                    "mesh_id": "mesh//M1",
                    "mesh_name": "Northwind Client",
                    "name": "Reception PC",
                    "hostname": "RECEP-01",
                    "os_desc": "Windows 11 Pro",
                    "os_family": "windows",
                    "ip_address": "192.168.1.50",
                    "power_state": 1,
                    "is_online": True,
                },
                {
                    "node_id": "FILESRV02",
                    "mesh_id": "mesh//M1",
                    "mesh_name": "Northwind Client",
                    "name": "File Server",
                    "hostname": "SRV-FILE-02",
                    "os_desc": "Ubuntu 24.04",
                    "os_family": "linux",
                    "ip_address": "192.168.1.10",
                    "power_state": 1,
                    "is_online": True,
                },
            ],
        )

    monkeypatch.setattr(mc, "fetch_meshcentral_inventory", fake_fetch)
    sync_resp = await web_remote_sync(_request(tech.id, "/web/remote/sync"), db)
    sync_data = json.loads(sync_resp.body.decode())
    assert sync_data["success"] is True
    assert sync_data["synced"] == 2
    assert sync_data["created"] == 1
    assert sync_data["updated"] == 1

    await db.refresh(dev)
    assert dev.is_pinned is True
    assert dev.notes == "Updated note"

    # Custom dashboard renders both devices cleanly
    dash = await web_remote_dashboard(_request(tech.id, "/web/remote"), db)
    dash_html = bytes(dash.body).decode()
    assert "Remote Client Access" in dash_html
    assert "Reception PC" in dash_html
    assert "File Server" in dash_html
    assert "Northwind HQ" in dash_html

    # Launching embedded session renders custom frame & records session audit log
    sess = await web_remote_session(_request(tech.id, f"/web/remote/session/{dev.id}"), dev.id, mode="desktop", db=db)
    sess_html = bytes(sess.body).decode()
    assert 'id="meshcentral-session-frame"' in sess_html
    assert "gotonode=RECEP001" in sess_html
    assert "viewmode=11" in sess_html
    assert "hide=63" in sess_html

    # Mode switcher endpoint mints fresh URL for terminal
    switch_resp = await web_remote_session_embed_url(_request(tech.id), dev.id, mode="terminal", db=db)
    switch_data = json.loads(switch_resp.body.decode())
    assert switch_data["success"] is True
    assert "viewmode=12" in switch_data["embed_url"]

    logs = (
        await db.execute(select(MeshCentralSessionLog).where(MeshCentralSessionLog.workspace_id == ws1.id))
    ).scalars().all()
    assert len(logs) == 2
    assert {l.view_mode for l in logs} == {"desktop", "terminal"}

    # Strict multi-tenant isolation: Rival MSP admin cannot open, pin, edit, or delete Acme's device
    with pytest.raises(HTTPException) as exc404:
        await web_remote_session(_request(other_admin.id), dev.id, mode="desktop", db=db)
    assert exc404.value.status_code == 404

    rival_pin = await web_remote_device_pin(_request(other_admin.id), dev.id, db)
    assert rival_pin.status_code == 404

    with pytest.raises(HTTPException) as exc_del:
        await web_remote_device_delete(_request(other_admin.id), dev.id, db)
    assert exc_del.value.status_code == 404

    # Non-admin in same workspace cannot delete devices
    with pytest.raises(HTTPException) as exc_non_admin:
        await web_remote_device_delete(_request(tech.id), dev.id, db)
    assert exc_non_admin.value.status_code == 403

    # Admin in same workspace can delete
    del_resp = await web_remote_device_delete(_request(admin.id), dev.id, db)
    assert del_resp.status_code == 303
