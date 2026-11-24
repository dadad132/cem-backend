"""Tests for MeshCentral Remote Client Access dashboard, SSO tokens, sync, and tenant isolation."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import re
import secrets
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel, select

from app.core import meshcentral as mc
from app.core.database import MESHCENTRAL_SETTINGS_EXTRA_COLUMNS
from app.core.tenant_backup import TENANT_SCOPE
from app.models.meshcentral import (
    MeshCentralDevice,
    MeshCentralGroup,
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
    web_remote_groups,
    web_remote_groups_create,
    web_remote_session,
    web_remote_session_embed_url,
    web_remote_settings,
    web_remote_settings_autofill,
    web_remote_settings_save,
    web_remote_settings_test,
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

    # Non-admin technician sees the remote dashboard & session without any MeshCentral Setup links
    tech_dash = await web_remote_dashboard(_request(tech.id, "/web/remote"), db=db)
    tech_dash_html = bytes(tech_dash.body).decode()
    assert "/web/remote/settings" not in tech_dash_html
    assert "MeshCentral Setup" not in tech_dash_html

    tech_sess = await web_remote_session(_request(tech.id, f"/web/remote/session/{dev.id}"), dev.id, mode="desktop", db=db)
    tech_sess_html = bytes(tech_sess.body).decode()
    assert "/web/remote/settings" not in tech_sess_html
    assert "Open Direct Tab" not in tech_sess_html

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


@pytest.mark.asyncio
async def test_site_settings_includes_meshcentral_form_and_roundtrip(seeded):
    from app.web.routes import web_admin_site_settings

    db, ws1, _ws2, admin, _tech, _other = seeded
    key_hex = secrets.token_hex(32)

    req = _request(admin.id, "/web/remote/settings/save")
    resp = await web_remote_settings_save(
        req,
        enabled="1",
        server_url="https://mesh.site-settings.test",
        domain="",
        username="admin",
        password="SecretPass99",
        clear_password=None,
        login_token_key=key_hex,
        clear_login_token_key=None,
        login_user_id="",
        default_view_mode=11,
        hide_mesh_chrome="1",
        verify_ssl="1",
        return_to="/web/admin/site-settings",
        db=db,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/web/admin/site-settings"
    assert "saved successfully" in req.session["success_message"].lower()

    # Open-redirect protection: unknown return_to always falls back to /web/remote/settings
    bad_req = _request(admin.id, "/web/remote/settings/save")
    bad_resp = await web_remote_settings_save(
        bad_req,
        enabled="1",
        server_url="https://mesh.site-settings.test",
        domain="",
        username="admin",
        password="",
        clear_password=None,
        login_token_key="",
        clear_login_token_key=None,
        login_user_id="",
        default_view_mode=11,
        hide_mesh_chrome="1",
        verify_ssl="1",
        return_to="https://evil.example/phish",
        db=db,
    )
    assert bad_resp.headers["location"] == "/web/remote/settings"

    page = await web_admin_site_settings(_request(admin.id, "/web/admin/site-settings"), db)
    html = bytes(page.body).decode()
    assert "MeshCentral Remote Access" in html
    assert "https://mesh.site-settings.test" in html
    assert "Save MeshCentral Settings" in html
    assert "Auto-Setup & Fill" in html
    assert "SecretPass99" not in html
    assert key_hex not in html


@pytest.mark.asyncio
async def test_local_autodetect_updates_config_and_autofill_settings(seeded, tmp_path, monkeypatch):
    from app.web.meshcentral_routes import web_remote_settings_autofill

    db, ws1, _ws2, admin, tech, _other = seeded

    # Seed a fake local MeshCentral directory with commented config.json
    mc_dir = tmp_path / "meshcentral"
    data_dir = mc_dir / "meshcentral-data"
    data_dir.mkdir(parents=True)
    cfg_file = data_dir / "config.json"
    cfg_file.write_text(
        '// MeshCentral config\n{"settings": {"cert": "mesh.hubconnect.co.za", "port": 443}}\n',
        encoding="utf-8",
    )

    local = await mc.autodetect_local_meshcentral(str(mc_dir))
    assert local["found"] is True
    assert local["config_updated"] is True
    assert local["suggested_url"] == "https://mesh.hubconnect.co.za"
    assert (data_dir / "config.json.bak").exists()
    updated_cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert updated_cfg["settings"].get("AllowFraming") is True or updated_cfg["settings"].get("allowFraming") is True
    assert updated_cfg["settings"].get("AllowLoginToken") is True

    # Non-admin blocked from autofill
    with pytest.raises(HTTPException) as exc403:
        await web_remote_settings_autofill(_request(tech.id), db=db)
    assert exc403.value.status_code == 403

    # Mock the hardened diagnostics report used by autofill (inventory + SSO discovery)
    async def fake_diagnose(settings_row, timeout=10.0):
        return {
            "ok": True,
            "auth_method": "x-meshauth",
            "meshes": {"mesh//GRP1": "Acme Clients"},
            "groups": 1,
            "devices": 1,
            "online": 1,
            "sso_mode": "authcookie",
            "error": None,
            "hint": "Connected to https://mesh.hubconnect.co.za.",
        }

    async def fake_cookie(settings_row, timeout=6.0):
        return "LIVE_SIGNED_COOKIE_ABC"

    monkeypatch.setattr("app.web.meshcentral_routes.diagnose_meshcentral_connection", fake_diagnose)

    resp = await web_remote_settings_autofill(
        _request(admin.id, "/web/remote/settings/autofill"),
        server_url="",
        domain="",
        username="admin",
        password="AdminPassword!",
        verify_ssl="1",
        local_install_path=str(mc_dir),
        db=db,
    )
    payload = json.loads(resp.body)
    assert "login_token_key" not in payload
    assert payload["success"] is True
    assert payload["enabled"] is True
    assert payload["server_url"] == "https://mesh.hubconnect.co.za"
    assert payload["sso_ready"] is True
    assert payload["sso_mode"] == "authcookie"
    assert payload["default_mesh_id"] == "mesh//GRP1"

    cached = (
        await db.execute(
            select(MeshCentralGroup).where(
                MeshCentralGroup.workspace_id == ws1.id,
                MeshCentralGroup.mesh_id == "mesh//GRP1",
            )
        )
    ).scalar_one_or_none()
    assert cached is not None and cached.name == "Acme Clients"

    # Verify build_embed_url_async uses live authcookie automatically when loginTokenKey is blank
    monkeypatch.setattr("app.core.meshcentral.fetch_meshcentral_auth_cookie", fake_cookie)
    saved = (
        await db.execute(select(MeshCentralSettings).where(MeshCentralSettings.workspace_id == ws1.id))
    ).scalar_one()
    embed = await mc.build_embed_url_async(saved, "node//PC123", mode="desktop")
    assert embed["sso_active"] is True
    assert embed.get("sso_mode") == "authcookie"
    assert "auth=LIVE_SIGNED_COOKIE_ABC" in embed["url"]


@pytest.mark.asyncio
async def test_autofill_reports_group_creation_failure_instead_of_succeeding(seeded, monkeypatch):
    from app.web.meshcentral_routes import web_remote_settings_autofill

    db, ws1, _ws2, admin, _tech, _other = seeded
    db.add(_mc_settings(
        workspace_id=ws1.id,
        enabled=False,
        server_url="https://mesh.acme.test",
        username="admin",
        password_encrypted=mc.encrypt_secret("AdminPassword!"),
    ))
    await db.commit()

    async def fake_diagnose(settings_row, timeout=10.0):
        return {"ok": True, "meshes": {}, "devices": 0, "online": 0,
                "sso_mode": "none", "error": None, "hint": None}

    async def broken_group_create(settings_row, name, description="", timeout=10.0):
        raise RuntimeError("group API refused")

    monkeypatch.setattr("app.web.meshcentral_routes.diagnose_meshcentral_connection", fake_diagnose)
    monkeypatch.setattr("app.web.meshcentral_routes.create_meshcentral_group", broken_group_create)

    resp = await web_remote_settings_autofill(
        _request(admin.id, "/web/remote/settings/autofill"),
        server_url="", domain="", username="", password="", verify_ssl=None,
        local_install_path="", db=db,
    )
    payload = json.loads(resp.body)
    assert payload["success"] is False
    assert payload["enabled"] is False
    assert "default_group_error" in payload
    assert "group API refused" in payload["default_group_error"]


@pytest.mark.asyncio
async def test_agent_direct_download_and_expiring_client_invite_lifecycle(seeded, monkeypatch):
    from datetime import datetime, timedelta
    from app.models.meshcentral import MeshCentralAgentInvite
    from app.web.meshcentral_routes import (
        web_remote_agent_download,
        web_remote_invite_create,
        web_remote_invite_revoke,
        web_remote_public_invite_download,
        web_remote_public_invite_page,
    )

    db, ws1, _ws2, admin, tech, other_admin = seeded

    db.add(
        MeshCentralSettings(
            workspace_id=ws1.id,
            enabled=True,
            server_url="https://mesh.hubconnect.co.za",
            username="admin",
            password_encrypted=mc.encrypt_secret("Pass123"),
            default_mesh_id="mesh//HUBGROUP1",
            default_mesh_name="HubConnect Clients",
        )
    )
    await db.commit()

    async def fake_proxy(settings_row, mesh_id, platform="windows64", install_mode="install", brand_name="RemoteSupport", timeout=30.0):
        return b"MZ-FAKE-MESHAGENT-BINARY", f"Acme-IT-Support-{platform}.exe", "application/octet-stream"

    monkeypatch.setattr("app.web.meshcentral_routes.proxy_agent_installer", fake_proxy)

    # 1. Technician direct download streams installer cleanly with no-store
    direct = await web_remote_agent_download(
        _request(tech.id, "/web/remote/agent/download"),
        mesh_id="mesh//HUBGROUP1",
        platform="windows64",
        install_mode="install",
        db=db,
    )
    assert direct.status_code == 200
    assert direct.body == b"MZ-FAKE-MESHAGENT-BINARY"
    assert "attachment; filename=" in direct.headers["content-disposition"]
    assert direct.headers["cache-control"] == "no-store"

    # 2. Technician creates a 15-minute expiring link hosted on tickets.hubconnect.co.za/web/client/remote/get/<token>
    req = _request(tech.id, "/web/remote/invites")
    req.url = SimpleNamespace(path="/web/remote/invites", scheme="https", netloc="tickets.hubconnect.co.za")
    inv_resp = await web_remote_invite_create(
        req,
        mesh_id="mesh//HUBGROUP1",
        mesh_name="HubConnect Clients",
        client_name="Sarah — Reception",
        platform="windows64",
        install_mode="connect",
        expires_minutes=15,
        db=db,
    )
    inv_data = json.loads(inv_resp.body)
    assert inv_data["success"] is True
    invite_info = inv_data["invite"]
    token = invite_info["token"]
    assert invite_info["share_url"] == f"https://tickets.hubconnect.co.za/web/client/remote/get/{token}"
    assert 840 <= invite_info["remaining_seconds"] <= 900

    # 3. Client (unauthenticated!) opens branded download page & downloads installer
    pub_page = await web_remote_public_invite_page(_request(None, f"/web/client/remote/get/{token}"), token, db=db)
    assert pub_page.status_code == 200
    pub_html = bytes(pub_page.body).decode()
    assert "Sarah — Reception" in pub_html
    assert "Download Remote Support Tool" in pub_html
    assert f"/web/client/remote/get/{token}/download" in pub_html
    assert "mesh.hubconnect.co.za" not in pub_html  # Client stays 100% on CEM website!

    pub_dl = await web_remote_public_invite_download(_request(None, f"/web/client/remote/get/{token}/download"), token, db=db)
    assert pub_dl.status_code == 200
    assert pub_dl.body == b"MZ-FAKE-MESHAGENT-BINARY"

    inv_row = (
        await db.execute(select(MeshCentralAgentInvite).where(MeshCentralAgentInvite.token == token))
    ).scalar_one()
    assert inv_row.download_count == 1

    # 4. Cross-tenant isolation: rival workspace cannot revoke Acme's link
    rival_revoke = await web_remote_invite_revoke(_request(other_admin.id), inv_row.id, db=db)
    assert rival_revoke.status_code == 404

    # 5. Once expired, both page and download return HTTP 410 Gone
    inv_row.expires_at = datetime.utcnow() - timedelta(seconds=5)
    await db.commit()

    exp_page = await web_remote_public_invite_page(_request(None, f"/web/remote/get/{token}"), token, db=db)
    assert exp_page.status_code == 410
    assert "expired" in bytes(exp_page.body).decode().lower()

    with pytest.raises(HTTPException) as exc410:
        await web_remote_public_invite_download(_request(None, f"/web/remote/get/{token}/download"), token, db=db)
    assert exc410.value.status_code == 410

    # 6. Revoking a fresh invite immediately invalidates it
    inv_row.expires_at = datetime.utcnow() + timedelta(minutes=10)
    await db.commit()
    rev_resp = await web_remote_invite_revoke(_request(tech.id), inv_row.id, db=db)
    assert rev_resp.status_code == 200

    with pytest.raises(HTTPException) as exc_revoked:
        await web_remote_public_invite_download(_request(None, f"/web/remote/get/{token}/download"), token, db=db)
    assert exc_revoked.value.status_code == 410


@pytest.mark.asyncio
async def test_remote_group_selection_rejects_unknown_and_cross_tenant_mesh_ids(seeded, monkeypatch):
    from app.web.meshcentral_routes import web_remote_agent_download, web_remote_invite_create

    db, ws1, ws2, _admin, tech, _other = seeded
    db.add(MeshCentralSettings(
        workspace_id=ws1.id, enabled=True, server_url="https://mesh.acme.test", verify_ssl=True,
    ))
    db.add(MeshCentralGroup(workspace_id=ws1.id, mesh_id="mesh//MINE", name="My Clients"))
    db.add(MeshCentralGroup(workspace_id=ws2.id, mesh_id="mesh//RIVAL", name="Rival Clients"))
    await db.commit()

    for foreign_id in ("mesh//RIVAL", "mesh//NOT-MINE"):
        with pytest.raises(HTTPException) as denied_download:
            await web_remote_agent_download(
                _request(tech.id), mesh_id=foreign_id, platform="windows64",
                install_mode="install", db=db,
            )
        assert denied_download.value.status_code == 400

        denied_invite = await web_remote_invite_create(
            _request(tech.id), mesh_id=foreign_id, mesh_name="", client_name="",
            platform="windows64", install_mode="connect", expires_minutes=15, db=db,
        )
        assert denied_invite.status_code == 400
        assert json.loads(denied_invite.body)["success"] is False

    async def fake_proxy(settings_row, mesh_id, platform="windows64", install_mode="install", brand_name="RemoteSupport", timeout=30.0):
        assert mesh_id == "mesh//MINE"
        return b"AGENT", "agent.exe", "application/octet-stream"

    monkeypatch.setattr("app.web.meshcentral_routes.proxy_agent_installer", fake_proxy)
    ok = await web_remote_agent_download(
        _request(tech.id), mesh_id="mesh//MINE", platform="windows64",
        install_mode="install", db=db,
    )
    assert ok.status_code == 200


@pytest.mark.asyncio
async def test_public_invite_pages_and_downloads_are_no_store_and_unindexable(seeded, monkeypatch):
    from datetime import timedelta

    from app.models.meshcentral import MeshCentralAgentInvite
    from app.web.meshcentral_routes import (
        web_remote_public_invite_download,
        web_remote_public_invite_page,
    )

    db, ws1, _ws2, _admin, tech, _other = seeded
    db.add(MeshCentralSettings(
        workspace_id=ws1.id, enabled=True, server_url="https://mesh.acme.test", verify_ssl=True,
    ))
    invite = MeshCentralAgentInvite(
        workspace_id=ws1.id,
        token="publicheaders123",
        mesh_id="mesh//G1",
        mesh_name="Acme Clients",
        client_name="Sarah",
        platform="linux",
        install_mode="connect",
        created_by_id=tech.id,
        expires_at=datetime.utcnow() + timedelta(minutes=10),
    )
    db.add(invite)
    await db.commit()

    async def fake_proxy(settings_row, mesh_id, platform="windows64", install_mode="install", brand_name="RemoteSupport", timeout=30.0):
        return b"AGENT-SCRIPT", "agent.sh", "text/x-shellscript"

    monkeypatch.setattr("app.web.meshcentral_routes.proxy_agent_installer", fake_proxy)

    page = await web_remote_public_invite_page(_request(None, "/web/client/remote/get/publicheaders123"),
                                               invite.token, db=db)
    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["x-robots-tag"] == "noindex, nofollow"

    download = await web_remote_public_invite_download(
        _request(None, "/web/client/remote/get/publicheaders123/download"), invite.token, db=db
    )
    assert download.status_code == 200
    assert download.headers["cache-control"] == "no-store"
    assert download.headers["x-robots-tag"] == "noindex, nofollow"

    invite.expires_at = datetime.utcnow() - timedelta(seconds=1)
    await db.commit()
    expired = await web_remote_public_invite_page(_request(None), invite.token, db=db)
    assert expired.status_code == 410
    assert expired.headers["cache-control"] == "no-store"
    assert expired.headers["x-robots-tag"] == "noindex, nofollow"


@pytest.mark.asyncio
async def test_public_invite_download_caps_uses_and_rechecks_after_proxy(seeded, monkeypatch):
    from datetime import timedelta

    from app.models.meshcentral import MeshCentralAgentInvite
    from app.web.meshcentral_routes import web_remote_public_invite_download

    db, ws1, _ws2, _admin, tech, _other = seeded
    db.add(MeshCentralSettings(
        workspace_id=ws1.id, enabled=True, server_url="https://mesh.acme.test", verify_ssl=True,
    ))
    invite = MeshCentralAgentInvite(
        workspace_id=ws1.id,
        token="publicmaxuses1",
        mesh_id="mesh//G1",
        platform="windows64",
        created_by_id=tech.id,
        expires_at=datetime.utcnow() + timedelta(minutes=10),
        max_downloads=1,
    )
    db.add(invite)
    await db.commit()

    async def fake_proxy(settings_row, mesh_id, platform="windows64", install_mode="install", brand_name="RemoteSupport", timeout=30.0):
        return b"AGENT-BINARY", "agent.exe", "application/octet-stream"

    monkeypatch.setattr("app.web.meshcentral_routes.proxy_agent_installer", fake_proxy)

    first = await web_remote_public_invite_download(_request(None), invite.token, db=db)
    assert first.status_code == 200
    await db.refresh(invite)
    assert invite.download_count == 1

    with pytest.raises(HTTPException) as exhausted:
        await web_remote_public_invite_download(_request(None), invite.token, db=db)
    assert exhausted.value.status_code == 410
    await db.refresh(invite)
    assert invite.download_count == 1

    # A link revoked while the installer is being proxied must never be streamed.
    invite.token = "publicrevoked2"
    invite.download_count = 0
    invite.revoked = False
    invite.expires_at = datetime.utcnow() + timedelta(minutes=10)
    await db.commit()

    async def revoking_proxy(settings_row, mesh_id, platform="windows64", install_mode="install", brand_name="RemoteSupport", timeout=30.0):
        other_engine = create_async_engine(db.get_bind().url)
        try:
            async with AsyncSession(other_engine, expire_on_commit=False) as other:
                row = (await other.execute(
                    select(MeshCentralAgentInvite).where(MeshCentralAgentInvite.token == "publicrevoked2")
                )).scalar_one()
                row.revoked = True
                await other.commit()
        finally:
            await other_engine.dispose()
        return b"MUST-NOT-STREAM", "agent.exe", "application/octet-stream"

    monkeypatch.setattr("app.web.meshcentral_routes.proxy_agent_installer", revoking_proxy)
    with pytest.raises(HTTPException) as revoked:
        await web_remote_public_invite_download(_request(None), invite.token, db=db)
    assert revoked.value.status_code == 410
    await db.refresh(invite)
    assert invite.download_count == 0


@pytest.mark.asyncio
async def test_cem_autolink_json_auto_populates_settings_without_manual_typing(seeded, tmp_path, monkeypatch):
    db, ws1, _ws2, admin, _tech, _other = seeded

    mc_dir = tmp_path / "meshcentral"
    data_dir = mc_dir / "meshcentral-data"
    data_dir.mkdir(parents=True)
    key_hex = secrets.token_hex(80)
    (data_dir / "cem-autolink.json").write_text(
        json.dumps({
            "server_url": "https://tickets.hubconnect.co.za:4433",
            "username": "admin",
            "password": "AutoGeneratedPass99!",
            "login_token_key": key_hex,
            "local_install_path": str(mc_dir),
            "verify_ssl": True,
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("MESHCENTRAL_DIR", str(mc_dir))

    imported = await mc.auto_import_local_meshcentral_if_present(db, ws1.id)
    assert imported is not None
    assert imported.enabled is True
    assert imported.server_url == "https://tickets.hubconnect.co.za:4433"
    assert imported.username == "admin"
    assert mc.decrypt_secret(imported.password_encrypted) == "AutoGeneratedPass99!"
    assert mc.decrypt_secret(imported.login_token_key_encrypted) == key_hex
    assert imported.login_user_id == "user//admin"

    # Subsequent admin edit (e.g. disabling) is preserved and never overwritten by autolink
    imported.enabled = False
    await db.commit()
    again = await mc.auto_import_local_meshcentral_if_present(db, ws1.id)
    assert again is not None
    assert again.enabled is False


@pytest.mark.asyncio
async def test_user_pages_never_import_shared_autolink_credentials(seeded, tmp_path, monkeypatch):
    """Shared server credentials must not be imported by an ordinary tenant visit."""
    from app.web.meshcentral_routes import web_remote_dashboard, web_remote_session

    db, ws1, _ws2, _admin, tech, _other = seeded
    mc_dir = tmp_path / "meshcentral"
    data_dir = mc_dir / "meshcentral-data"
    data_dir.mkdir(parents=True)
    (data_dir / "cem-autolink.json").write_text(json.dumps({
        "server_url": "https://mesh.shared.test",
        "username": "shared-admin",
        "password": "SharedSecret123",
        "login_token_key": secrets.token_hex(40),
    }), encoding="utf-8")
    monkeypatch.setenv("MESHCENTRAL_DIR", str(mc_dir))

    device = MeshCentralDevice(
        workspace_id=ws1.id, node_id="DEVICE1", name="Reception PC", is_online=True,
    )
    db.add(device)
    await db.commit()

    await web_remote_dashboard(_request(tech.id, "/web/remote"), db=db)
    await web_remote_session(_request(tech.id, "/web/remote/session"), device.id, mode="desktop", db=db)

    rows = (await db.execute(
        select(MeshCentralSettings).where(MeshCentralSettings.workspace_id == ws1.id)
    )).scalars().all()
    assert rows == [], "a tenant page silently imported shared MeshCentral credentials"


# ---------------------------------------------------------------------------
# Slice 1: persistent groups, loopback auth fallback, protocol & diagnostics
# ---------------------------------------------------------------------------

def _mc_settings(**overrides):
    values = {
        "workspace_id": 1,
        "enabled": True,
        "server_url": "https://tickets.hubconnect.co.za:4433",
        "domain": "",
        "username": "Admin",
        "password_encrypted": mc.encrypt_secret("Pass123"),
        "verify_ssl": True,
    }
    values.update(overrides)
    return MeshCentralSettings(**values)


def _fake_aiohttp(monkeypatch, ws_scripts=None, get_responses=None):
    """Install an in-memory aiohttp stand-in and return a call recorder.

    ``ws_scripts[i]`` is the frame script for the i-th ws_connect attempt (a
    list of JSON dicts, or an exception to raise on connect). ``get_responses``
    is a list of ``(status, body)`` tuples or exceptions for HTTP downloads.
    """
    state = {"ws_calls": [], "get_calls": []}
    ws_scripts = list(ws_scripts or [])
    get_responses = list(get_responses or [])

    class _MsgType:
        TEXT = "TEXT"
        CLOSED = "CLOSED"
        ERROR = "ERROR"

    class _Timeout:
        def __init__(self, **kwargs):
            pass

    class _Context:
        def __init__(self, value):
            self.value = value

        async def __aenter__(self):
            if isinstance(self.value, BaseException):
                raise self.value
            return self.value

        async def __aexit__(self, *exc):
            return False

    class _Message:
        def __init__(self, data, kind):
            # Real aiohttp delivers JSON frames as strings.
            self.data = json.dumps(data) if isinstance(data, dict) else data
            self.type = kind

    class _WS:
        def __init__(self, script, call):
            self._script = script
            self._call = call

        async def send_json(self, payload):
            self._call["sent"].append(payload)

        async def receive(self, timeout=None):
            if not self._script:
                raise asyncio.TimeoutError()
            frame = self._script.pop(0)
            if isinstance(frame, BaseException):
                raise frame
            return _Message(frame, _MsgType.TEXT)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _Response:
        def __init__(self, status, body):
            self.status = status
            self._body = body

        async def read(self):
            return self._body

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _Session:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def ws_connect(self, url, headers=None, **kwargs):
            call = {"url": url, "headers": dict(headers or {}), "sent": []}
            state["ws_calls"].append(call)
            index = len(state["ws_calls"]) - 1
            if index >= len(ws_scripts):
                raise AssertionError(f"Unexpected ws_connect #{index + 1}: {url}")
            script = ws_scripts[index]
            if isinstance(script, BaseException):
                return _Context(script)
            return _WS(list(script), call)

        def get(self, url, headers=None, **kwargs):
            call = {"url": url, "headers": dict(headers or {})}
            state["get_calls"].append(call)
            index = len(state["get_calls"]) - 1
            if index >= len(get_responses):
                raise AssertionError(f"Unexpected HTTP GET #{index + 1}: {url}")
            response = get_responses[index]
            if isinstance(response, BaseException):
                return _Context(response)
            status, body = response
            return _Response(status, body)

    class _FakeAiohttp:
        ClientSession = _Session
        ClientTimeout = _Timeout
        WSMsgType = _MsgType

    monkeypatch.setattr(mc, "_require_aiohttp", lambda: _FakeAiohttp)
    return state


DNS_ERROR_TEXT = (
    "Cannot connect to host tickets.hubconnect.co.za:443 "
    "ssl:default [Name or service not known]"
)


async def _cached_groups(db, workspace_id):
    result = await db.execute(
        select(MeshCentralGroup).where(MeshCentralGroup.workspace_id == workspace_id)
    )
    return result.scalars().all()


def test_group_model_is_persistent_indexed_and_tenant_scoped():
    assert TENANT_SCOPE["meshcentralgroup"] == ("direct",)
    assert ("meshcentralsettings", "local_port", "INTEGER") in MESHCENTRAL_SETTINGS_EXTRA_COLUMNS

    group = MeshCentralGroup(workspace_id=7, mesh_id="mesh//G1", name="Acme Clients")
    assert group.id is None
    assert group.description is None
    assert group.is_default is False
    assert group.created_at is not None and group.updated_at is not None
    assert MeshCentralGroup.__tablename__ == "meshcentralgroup"
    assert MeshCentralGroup.__table__.c.workspace_id.index is True
    assert MeshCentralGroup.__table__.c.mesh_id.index is True
    assert MeshCentralGroup.__table__.c.name.index is True


@pytest.mark.asyncio
async def test_upsert_workspace_groups_is_idempotent_and_cross_tenant_safe(seeded):
    db, ws1, ws2, *_ = seeded

    first = await mc.upsert_workspace_groups(db, ws1.id, {
        "mesh//A": "Acme Clients",
        "mesh//B": "Acme Servers",
    })
    assert first == {"created": 2, "updated": 0}

    rows = await _cached_groups(db, ws1.id)
    by_id = {row.mesh_id: row for row in rows}
    assert set(by_id) == {"mesh//A", "mesh//B"}
    assert by_id["mesh//A"].name == "Acme Clients"

    second = await mc.upsert_workspace_groups(db, ws1.id, {
        "mesh//A": "Renamed Clients",
        "mesh//C": "New Group",
    })
    assert second == {"created": 1, "updated": 1}
    rows = await _cached_groups(db, ws1.id)
    by_id = {row.mesh_id: row for row in rows}
    assert by_id["mesh//A"].name == "Renamed Clients"
    assert by_id["mesh//B"].name == "Acme Servers"  # stale groups are never deleted
    assert by_id["mesh//C"].name == "New Group"

    rival = await mc.upsert_workspace_groups(db, ws2.id, {"mesh//A": "Rival Clients"})
    assert rival == {"created": 1, "updated": 0}
    rival_rows = await _cached_groups(db, ws2.id)
    assert len(rival_rows) == 1
    assert rival_rows[0].name == "Rival Clients"
    assert rival_rows[0].id != by_id["mesh//A"].id

    empty = await mc.upsert_workspace_groups(db, ws1.id, {})
    assert empty == {"created": 0, "updated": 0}


def test_local_loopback_candidates_append_without_changing_browser_url(tmp_path):
    cfg = _mc_settings(
        local_install_path="/opt/meshcentral",
        local_port=4430,
        login_token_key_encrypted=mc.encrypt_secret(secrets.token_hex(32)),
    )

    candidates = mc.build_control_ws_candidates(cfg)
    urls = [url for url, _headers in candidates]
    headers = [headers for _url, headers in candidates]

    assert any(url.startswith("wss://tickets.hubconnect.co.za:4433/control.ashx") for url in urls)
    assert any("x-meshauth" in item for item in headers)
    assert any("?auth=" in url for url in urls)
    assert "ws://127.0.0.1:4430/control.ashx" in urls
    assert any(url.startswith("ws://127.0.0.1:4430/control.ashx?auth=") for url in urls)

    # Browser iframes keep using the public HTTPS server_url, never loopback.
    embed = mc.build_embed_url(cfg, "node//PC123")
    assert urlparse(embed["url"]).netloc == "tickets.hubconnect.co.za:4433"

    # A real local install path alone enables the default loopback port, but an
    # arbitrary path does not (no credential leak to an unrelated listener).
    install_dir = tmp_path / "meshcentral"
    (install_dir / "meshcentral-data").mkdir(parents=True)
    path_only = mc.build_control_ws_candidates(
        _mc_settings(local_install_path=str(install_dir))
    )
    assert any("ws://127.0.0.1:4430/control.ashx" in url for url, _ in path_only)
    arbitrary = mc.build_control_ws_candidates(_mc_settings(local_install_path="/opt/nowhere"))
    assert not any("127.0.0.1" in url for url, _ in arbitrary)
    # ...and without either setting there is no loopback candidate.
    cloud_only = mc.build_control_ws_candidates(_mc_settings())
    assert not any("127.0.0.1" in url for url, _ in cloud_only)

    # HTTP bases keep the domain prefix and end in '/' for correct path joins.
    dom_cfg = _mc_settings(domain="tenant1", local_port=4430)
    bases = mc.build_control_http_bases(dom_cfg)
    assert bases[0] == "https://tickets.hubconnect.co.za:4433/tenant1/"
    assert bases[1] == "http://127.0.0.1:4430/tenant1/"

    # A public URL mounted under a path prefix must be mirrored on loopback.
    mounted_cfg = _mc_settings(
        server_url="https://tickets.hubconnect.co.za:4433/meshcentral",
        domain="tenant1",
        local_port=4430,
    )
    mounted_urls = [url for url, _ in mc.build_control_ws_candidates(mounted_cfg)]
    assert "ws://127.0.0.1:4430/meshcentral/tenant1/control.ashx" in mounted_urls
    mounted_bases = mc.build_control_http_bases(mounted_cfg)
    assert mounted_bases[0] == "https://tickets.hubconnect.co.za:4433/meshcentral/tenant1/"
    assert mounted_bases[1] == "http://127.0.0.1:4430/meshcentral/tenant1/"


@pytest.mark.asyncio
async def test_create_meshcentral_group_handles_direct_event_and_meshes_frames(monkeypatch):
    state = _fake_aiohttp(monkeypatch, [[{"action": "createmesh", "meshid": "mesh//D1"}]])
    created = await mc.create_meshcentral_group(_mc_settings(), "Acme Clients", "Managed by CEM")
    assert created == {"mesh_id": "mesh//D1", "mesh_name": "Acme Clients"}
    assert state["ws_calls"][0]["sent"][0] == {
        "action": "createmesh",
        "meshname": "Acme Clients",
        "meshtype": 2,
        "desc": "Managed by CEM",
        "responseid": "cem",
    }
    assert any(frame.get("action") == "meshes" for frame in state["ws_calls"][0]["sent"])

    _fake_aiohttp(monkeypatch, [[{
        "action": "event",
        "event": {"action": "createmesh", "meshid": "mesh//E1"},
        "responseid": "cem",
    }]])
    created = await mc.create_meshcentral_group(_mc_settings(), "Event Group")
    assert created["mesh_id"] == "mesh//E1"

    # An uncorrelated createmesh event is another user's broadcast on a shared
    # server and must not be recorded as this workspace's new group.
    _fake_aiohttp(monkeypatch, [[
        {"action": "event", "event": {"action": "createmesh", "meshid": "mesh//SOMEONE_ELSE"}},
        {"action": "createmesh", "meshid": "mesh//MINE", "responseid": "cem"},
    ]])
    created = await mc.create_meshcentral_group(_mc_settings(), "My Group")
    assert created["mesh_id"] == "mesh//MINE"

    _fake_aiohttp(monkeypatch, [[
        {"action": "createmesh", "result": "ok"},
        {"action": "meshes", "meshes": [{"_id": "mesh//F1", "name": "acme clients"}]},
    ]])
    created = await mc.create_meshcentral_group(_mc_settings(), "Acme Clients")
    assert created["mesh_id"] == "mesh//F1"


@pytest.mark.asyncio
async def test_create_meshcentral_group_falls_back_from_noauth_to_login_token(monkeypatch):
    key_hex = secrets.token_hex(32)
    cfg = _mc_settings(login_token_key_encrypted=mc.encrypt_secret(key_hex))
    state = _fake_aiohttp(monkeypatch, [
        [{"action": "close", "cause": "noauth"}],
        [{"action": "close", "cause": "noauth"}],
        [{"action": "createmesh", "meshid": "mesh//TOKEN"}],
    ])

    created = await mc.create_meshcentral_group(cfg, "Token Group")
    assert created["mesh_id"] == "mesh//TOKEN"
    assert len(state["ws_calls"]) == 3
    success_url = state["ws_calls"][2]["url"]
    assert "?auth=" in success_url
    token = parse_qs(urlparse(success_url).query)["auth"][0]
    assert mc.decode_login_token(token, key_hex)["u"] in {"user//Admin", "user//admin"}


@pytest.mark.asyncio
async def test_create_meshcentral_group_uses_loopback_after_public_connection_failure(monkeypatch):
    cfg = _mc_settings(local_port=4430)
    dns_error = OSError(
        DNS_ERROR_TEXT
    )
    state = _fake_aiohttp(monkeypatch, [
        dns_error,
        dns_error,
        [{"action": "createmesh", "meshid": "mesh//LOOPBACK", "responseid": "cem"}],
    ])
    created = await mc.create_meshcentral_group(cfg, "Loopback Group")
    assert created["mesh_id"] == "mesh//LOOPBACK"
    assert len(state["ws_calls"]) == 3
    assert state["ws_calls"][2]["url"].startswith("ws://127.0.0.1:4430/control.ashx")


@pytest.mark.asyncio
async def test_create_meshcentral_group_reports_actionable_noauth_dns_and_ssl_errors(monkeypatch):
    cfg = _mc_settings()

    _fake_aiohttp(monkeypatch, [[{"action": "close", "cause": "noauth"}] for _ in range(2)])
    with pytest.raises(RuntimeError) as noauth:
        await mc.create_meshcentral_group(cfg, "Acme Clients")
    noauth_msg = str(noauth.value).lower()
    assert "noauth" in noauth_msg
    assert "credential" in noauth_msg or "password" in noauth_msg

    dns_error = OSError("[Errno -2] Name or service not known: tickets.hubconnect.co.za")
    _fake_aiohttp(monkeypatch, [dns_error for _ in range(2)])
    with pytest.raises(RuntimeError) as dns:
        await mc.create_meshcentral_group(cfg, "Acme Clients")
    assert "dns" in str(dns.value).lower() or "hostname" in str(dns.value).lower()

    ssl_error = RuntimeError("SSL: CERTIFICATE_VERIFY_FAILED certificate verify failed")
    _fake_aiohttp(monkeypatch, [ssl_error for _ in range(2)])
    with pytest.raises(RuntimeError) as ssl:
        await mc.create_meshcentral_group(cfg, "Acme Clients")
    assert "ssl" in str(ssl.value).lower() or "certificate" in str(ssl.value).lower()


def test_error_messages_redact_live_auth_tokens_and_credentials():
    key_hex = secrets.token_hex(32)
    cfg = _mc_settings(login_token_key_encrypted=mc.encrypt_secret(key_hex))
    token = mc.generate_login_token(key_hex, "user//admin")
    err = OSError(
        f"connection failed at wss://tickets.hubconnect.co.za:4433/control.ashx?auth={token}&x=1"
    )

    kind, hint = mc.classify_meshcentral_error(err, cfg)
    wrapped = mc.actionable_meshcentral_error(err, cfg)
    assert kind == "connection"
    assert token not in hint and token not in str(wrapped)
    assert "<redacted>" in str(wrapped)

    # Real aiohttp errors include `ssl:default` even for DNS failures.
    aiohttp_dns = OSError(
        DNS_ERROR_TEXT
    )
    kind, hint = mc.classify_meshcentral_error(aiohttp_dns, cfg)
    assert kind == "dns"
    assert "dns" in hint.lower() or "hostname" in hint.lower()

    credential_err = mc.actionable_meshcentral_error(
        RuntimeError("x-meshauth dXNlcjpwYXNzd29yZA=="), cfg
    )
    assert "dXNlcjpwYXNzd29yZA==" not in str(credential_err)


@pytest.mark.asyncio
async def test_agent_download_rejects_non_installer_payload(monkeypatch):
    cfg = _mc_settings(local_port=4430)
    _fake_aiohttp(monkeypatch, get_responses=[
        (200, b"not-a-real-installer"),
        (200, b"still-not-a-real-installer"),
    ])
    with pytest.raises(RuntimeError) as exc:
        await mc.proxy_agent_installer(cfg, "mesh//GROUP1")
    assert "unexpected" in str(exc.value)


@pytest.mark.asyncio
async def test_auth_cookie_retries_remaining_candidates(monkeypatch):
    state = _fake_aiohttp(monkeypatch, [
        [{"action": "close", "cause": "noauth"}],
        [{"action": "authcookie", "cookie": "LIVE_COOKIE_123"}],
    ])
    assert await mc.fetch_meshcentral_auth_cookie(_mc_settings()) == "LIVE_COOKIE_123"
    assert len(state["ws_calls"]) == 2


@pytest.mark.asyncio
async def test_agent_download_falls_back_to_local_loopback_http(monkeypatch):
    cfg = _mc_settings(local_port=4430)
    state = _fake_aiohttp(monkeypatch, get_responses=[(502, b""), (200, b"MZ-LOCAL-AGENT")])

    payload, filename, content_type = await mc.proxy_agent_installer(cfg, "mesh//GROUP1")
    assert payload == b"MZ-LOCAL-AGENT"
    assert filename.endswith(".exe")
    assert content_type == "application/octet-stream"
    urls = [call["url"] for call in state["get_calls"]]
    assert urls[0].startswith("https://tickets.hubconnect.co.za:4433/meshagents?")
    assert urls[1].startswith("http://127.0.0.1:4430/meshagents?")


@pytest.mark.asyncio
async def test_sync_and_group_creation_persist_groups(seeded, monkeypatch):
    db, ws1, *_ = seeded
    db.add(_mc_settings())
    await db.commit()

    async def fake_inventory(settings_row, timeout=12.0):
        return ({"mesh//SYNC": "Synced Group"}, [])

    monkeypatch.setattr(mc, "fetch_meshcentral_inventory", fake_inventory)
    await mc.sync_workspace_devices(db, ws1.id, _mc_settings())
    groups = await _cached_groups(db, ws1.id)
    assert [group.mesh_id for group in groups] == ["mesh//SYNC"]

    from app.web.meshcentral_routes import web_remote_groups_create

    async def fake_create(settings_row, name, description="", timeout=10.0):
        return {"mesh_id": "mesh//CREATED", "mesh_name": name}

    monkeypatch.setattr("app.web.meshcentral_routes.create_meshcentral_group", fake_create)
    admin = (await db.execute(select(User).where(User.username == "admin"))).scalar_one()
    response = await web_remote_groups_create(
        _request(admin.id), name="Created Group", description="desc", db=db,
    )
    assert json.loads(response.body)["success"] is True
    created = (
        await db.execute(
            select(MeshCentralGroup).where(
                MeshCentralGroup.workspace_id == ws1.id,
                MeshCentralGroup.mesh_id == "mesh//CREATED",
            )
        )
    ).scalar_one()
    assert created.name == "Created Group"


@pytest.mark.asyncio
async def test_diagnose_connection_reports_success_noauth_and_dns_hints(monkeypatch):
    cfg = _mc_settings(login_token_key_encrypted=mc.encrypt_secret(secrets.token_hex(32)))

    async def healthy(settings_row, timeout=12.0):
        return ({"mesh//G1": "Group"}, [{"is_online": True}, {"is_online": False}])

    monkeypatch.setattr(mc, "fetch_meshcentral_inventory", healthy)
    report = await mc.diagnose_meshcentral_connection(cfg, timeout=8.0)
    assert report["ok"] is True
    assert report["auth_method"] == "x-meshauth"
    assert report["groups"] == 1
    assert report["devices"] == 2
    assert report["online"] == 1
    assert report["sso_mode"] == "token_key"
    assert report["error"] is None

    async def rejected(settings_row, timeout=12.0):
        raise RuntimeError("MeshCentral closed the connection: noauth")

    monkeypatch.setattr(mc, "fetch_meshcentral_inventory", rejected)
    report = await mc.diagnose_meshcentral_connection(cfg)
    assert report["ok"] is False
    assert "noauth" in report["error"].lower()
    assert "hint:" not in report["error"].lower()  # UIs append the structured hint
    hint = report["hint"].lower()
    assert "credential" in hint or "password" in hint

    async def unreachable(settings_row, timeout=12.0):
        raise OSError("[Errno -2] Name or service not known: tickets.hubconnect.co.za")

    monkeypatch.setattr(mc, "fetch_meshcentral_inventory", unreachable)
    report = await mc.diagnose_meshcentral_connection(cfg)
    assert report["ok"] is False
    assert "dns" in report["hint"].lower() or "hostname" in report["hint"].lower()


@pytest.mark.asyncio
async def test_settings_test_route_surfaces_actionable_diagnostics(seeded, monkeypatch):
    db, ws1, *_ = seeded
    db.add(_mc_settings(workspace_id=ws1.id))
    await db.commit()
    admin = (await db.execute(select(User).where(User.username == "admin"))).scalar_one()

    async def rejected(settings_row, timeout=10.0):
        return {
            "ok": False,
            "auth_method": "x-meshauth",
            "groups": 0,
            "devices": 0,
            "online": 0,
            "sso_mode": "none",
            "error": "MeshCentral noauth error",
            "hint": "Check the MeshCentral username/password.",
        }

    monkeypatch.setattr(
        "app.web.meshcentral_routes.diagnose_meshcentral_connection", rejected
    )
    response = await web_remote_settings_test(_request(admin.id), db=db)
    assert response.status_code == 502
    payload = json.loads(response.body)
    assert payload["success"] is False
    assert payload["hint"] == "Check the MeshCentral username/password."


@pytest.mark.asyncio
async def test_auto_import_seeds_default_group_and_local_port(seeded, tmp_path, monkeypatch):
    db, ws1, ws2, *_ = seeded
    mc_dir = tmp_path / "meshcentral"
    data_dir = mc_dir / "meshcentral-data"
    data_dir.mkdir(parents=True)
    (data_dir / "cem-autolink.json").write_text(json.dumps({
        "server_url": "https://tickets.hubconnect.co.za:4433",
        "username": "admin",
        "password": "AutoPass99!",
        "login_token_key": secrets.token_hex(80),
        "default_mesh_id": "mesh//DEFAULT1",
        "default_mesh_name": "HubConnect Clients",
        "local_port": 4430,
    }), encoding="utf-8")
    monkeypatch.setenv("MESHCENTRAL_DIR", str(mc_dir))

    imported = await mc.auto_import_local_meshcentral_if_present(db, ws1.id)
    assert imported.local_port == 4430
    assert imported.default_mesh_id == "mesh//DEFAULT1"
    assert imported.default_mesh_name == "HubConnect Clients"

    group = (await _cached_groups(db, ws1.id))[0]
    assert group.mesh_id == "mesh//DEFAULT1"
    assert group.name == "HubConnect Clients"
    assert group.is_default is True

    rival = await _cached_groups(db, ws2.id)
    assert rival == []


# ---------------------------------------------------------------------------
# Slice 2: hardened group routes, inline modal diagnostics & self-verifying setup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_groups_route_returns_persisted_groups_and_live_warning_when_offline(seeded, monkeypatch):
    db, ws1, _ws2, _admin, tech, _other = seeded
    db.add(_mc_settings(workspace_id=ws1.id))
    db.add(MeshCentralGroup(workspace_id=ws1.id, mesh_id="mesh//SAVED", name="Saved Clients"))
    await db.commit()

    async def offline(*_args, **_kwargs):
        raise OSError("Name or service not known: tickets.hubconnect.co.za")

    monkeypatch.setattr("app.web.meshcentral_routes.fetch_meshcentral_inventory", offline)
    offline_resp = await web_remote_groups(_request(tech.id), db=db)
    offline_data = json.loads(offline_resp.body)
    assert offline_data["success"] is True
    assert "mesh//SAVED" in {g["mesh_id"] for g in offline_data["groups"]}
    warning = (offline_data.get("live_warning") or "").lower()
    assert warning and ("not reachable" in warning or "offline" in warning)

    async def online(*_args, **_kwargs):
        return {"mesh//LIVE": "Live Group"}, []

    monkeypatch.setattr("app.web.meshcentral_routes.fetch_meshcentral_inventory", online)
    online_resp = await web_remote_groups(_request(tech.id), db=db)
    online_data = json.loads(online_resp.body)
    assert online_data["success"] is True
    assert not online_data.get("live_warning")
    assert {"mesh//SAVED", "mesh//LIVE"} <= {g["mesh_id"] for g in online_data["groups"]}
    cached = {row.mesh_id for row in await _cached_groups(db, ws1.id)}
    assert "mesh//LIVE" in cached  # live groups are upserted into MeshCentralGroup


@pytest.mark.asyncio
async def test_groups_create_persists_group_and_returns_actionable_errors(seeded, monkeypatch):
    db, ws1, _ws2, admin, _tech, _other = seeded

    # No MeshCentral settings yet: clear 400 with a hint, never a 500.
    resp = await web_remote_groups_create(_request(admin.id), name="Acme Clients", db=db)
    assert resp.status_code == 400
    payload = json.loads(resp.body)
    assert payload["success"] is False and payload["error"] and payload["hint"]

    db.add(_mc_settings(workspace_id=ws1.id))
    await db.commit()

    blank = await web_remote_groups_create(_request(admin.id), name="   ", db=db)
    assert blank.status_code == 400
    assert json.loads(blank.body)["hint"]

    async def noauth(*_args, **_kwargs):
        raise RuntimeError("MeshCentral closed the connection: noauth")

    monkeypatch.setattr("app.web.meshcentral_routes.create_meshcentral_group", noauth)
    failed = await web_remote_groups_create(_request(admin.id), name="Acme Clients", db=db)
    assert failed.status_code == 502
    failure = json.loads(failed.body)
    assert "noauth" in failure["error"].lower()
    assert "hint" not in failure["error"].lower()  # structured hint is not duplicated
    assert "credential" in failure["hint"].lower() or "password" in failure["hint"].lower()
    assert await _cached_groups(db, ws1.id) == []

    async def created(*_args, **_kwargs):
        return {"mesh_id": "mesh//NEW", "mesh_name": "Acme Clients"}

    monkeypatch.setattr("app.web.meshcentral_routes.create_meshcentral_group", created)
    ok = await web_remote_groups_create(
        _request(admin.id), name="Acme Clients", description="Managed by CEM", db=db
    )
    assert ok.status_code == 200
    assert json.loads(ok.body)["success"] is True
    rows = {row.mesh_id: row for row in await _cached_groups(db, ws1.id)}
    assert rows["mesh//NEW"].name == "Acme Clients"
    assert rows["mesh//NEW"].is_default is True
    settings = (
        await db.execute(select(MeshCentralSettings).where(MeshCentralSettings.workspace_id == ws1.id))
    ).scalar_one()
    assert settings.default_mesh_id == "mesh//NEW"


@pytest.mark.asyncio
async def test_sync_route_persists_groups_via_upsert(seeded, monkeypatch):
    db, ws1, _ws2, _admin, tech, _other = seeded
    db.add(_mc_settings(workspace_id=ws1.id))
    await db.commit()

    async def inventory(*_args, **_kwargs):
        return {"mesh//SYNCED": "Synced Clients"}, []

    monkeypatch.setattr(mc, "fetch_meshcentral_inventory", inventory)
    resp = await web_remote_sync(_request(tech.id), db=db)
    assert json.loads(resp.body)["success"] is True
    assert "mesh//SYNCED" in {row.mesh_id for row in await _cached_groups(db, ws1.id)}


@pytest.mark.asyncio
async def test_install_modal_has_inline_feedback_banner_and_live_warning_note(seeded):
    db, ws1, _ws2, admin, _tech, _other = seeded
    page = await web_remote_dashboard(_request(admin.id), db=db)
    html = bytes(page.body).decode()
    modal_html = html.split('x-show="showInstallModal"', 1)[1].split("<!-- Add Client PC Modal -->", 1)[0]

    assert 'id="modalFeedback"' in modal_html
    assert 'x-text="modalFeedback"' in modal_html
    assert "modalFeedbackType" in modal_html
    assert 'role="alert"' in modal_html
    assert "liveWarning" in modal_html
    assert "data.live_warning" in html
    assert "no device group" in modal_html.lower()
    assert "setModalFeedback" in html  # group/link actions report inside the modal


@pytest.mark.asyncio
async def test_non_admin_settings_routes_blocked_and_admin_hints_present(seeded):
    from app.web.routes import web_admin_site_settings

    db, ws1, _ws2, admin, tech, _other = seeded
    db.add(_mc_settings(
        workspace_id=ws1.id,
        enabled=True,
        login_token_key_encrypted=mc.encrypt_secret(secrets.token_hex(32)),
    ))
    db.add(MeshCentralDevice(
        workspace_id=ws1.id,
        node_id="NODE123",
        name="Reception PC",
        mesh_id="mesh//GROUP",
        mesh_name="Clients",
    ))
    await db.commit()

    admin_only_calls = [
        lambda: web_remote_settings(_request(tech.id), db=db),
        lambda: web_remote_settings_save(_request(tech.id), db=db),
        lambda: web_remote_settings_test(_request(tech.id), db=db),
        lambda: web_remote_settings_autofill(_request(tech.id), db=db),
    ]
    for invoke in admin_only_calls:
        with pytest.raises(HTTPException) as exc:
            await invoke()
        assert exc.value.status_code == 403

    tech_dash = await web_remote_dashboard(_request(tech.id), db=db)
    dash_html = bytes(tech_dash.body).decode()
    tech_sess = await web_remote_session(_request(tech.id), 1, mode="desktop", db=db)
    sess_html = bytes(tech_sess.body).decode()
    for needle in (
        "/web/remote/settings", "MeshCentral Setup", "config.json", "Pop Out", "Open Direct Tab",
    ):
        assert needle not in dash_html
        assert needle not in sess_html

    for page in (
        await web_remote_settings(_request(admin.id), db=db),
        await web_admin_site_settings(_request(admin.id), db=db),
    ):
        html = bytes(page.body).decode()
        assert "http://127.0.0.1:8000" in html
        assert "https://tickets.hubconnect.co.za:4433" in html
        assert "lowercase" in html.lower()


def test_setup_script_live_verification_guards_against_noauth_and_missing_groups():
    script = (Path(__file__).resolve().parents[1] / "setup_meshcentral.sh").read_text(encoding="utf-8")
    for needle in (
        "meshctrl.js", "listdevicegroups", "adddevicegroup", '"Client PCs"',
        "--loginuser", "--loginpass", "--loginkey", "--name",
        "default_mesh_id", "default_mesh_name", '"local_port"', '"verify_ssl"',
        "chmod 640", "/tmp/meshcentral-user.log", "resetaccount", "adminaccount",
    ):
        assert needle in script, f"setup_meshcentral.sh missing: {needle}"


def test_setup_script_group_parser_handles_real_shapes_and_fails_closed():
    """The installer must never mistake an unparseable reply for 'zero groups'."""
    script = (Path(__file__).resolve().parents[1] / "setup_meshcentral.sh").read_text(encoding="utf-8")
    match = re.search(r"parse_device_groups\(\) \{\npython3 -c '(.*?)'\n\}", script, re.DOTALL)
    assert match, "Could not locate parse_device_groups() in setup_meshcentral.sh"
    code = match.group(1)

    def run(raw: str):
        return subprocess.run(
            [sys.executable, "-c", code], input=raw, text=True, capture_output=True
        )

    obj = run('{"mesh//A": {"name": "Client PCs"}, "mesh//B": {"name": "Other"}}')
    assert obj.returncode == 0
    assert obj.stdout.strip().splitlines()[:3] == ["2", "mesh//A", "Client PCs"]

    arr = run('[{"_id": "mesh//Z", "name": "Client PCs"}]')
    assert arr.returncode == 0
    assert arr.stdout.strip().splitlines()[:3] == ["1", "mesh//Z", "Client PCs"]

    csv_table = run('id, name\r\n---------------\n"ABC123", "Client PCs"\n')
    assert csv_table.returncode == 0
    assert csv_table.stdout.strip().splitlines()[:3] == ["1", "mesh//ABC123", "Client PCs"]

    ansi = run('\x1b[0m[{"id": "mesh//Q", "name": "Client PCs"}]')
    assert ansi.returncode == 0 and "mesh//Q" in ansi.stdout

    empty = run("  \n")
    assert empty.returncode == 0 and empty.stdout.strip().splitlines()[:1] == ["0"]

    garbage = run("meshctrl: auth failed")
    assert garbage.returncode == 2
    assert "unparseable" in garbage.stderr.lower()


@pytest.mark.asyncio
async def test_admin_diagnostics_report_is_admin_only_and_redacts_secrets(seeded, tmp_path, monkeypatch):
    from app.web.meshcentral_routes import web_remote_settings_diagnostics

    db, ws1, _ws2, admin, tech, _other = seeded
    secret_pass = "UltraSecretMeshPass999!"
    secret_key = secrets.token_hex(40)

    db.add(
        MeshCentralSettings(
            workspace_id=ws1.id,
            enabled=True,
            server_url="https://tickets.hubconnect.co.za:4433",
            username="admin",
            password_encrypted=mc.encrypt_secret(secret_pass),
            login_token_key_encrypted=mc.encrypt_secret(secret_key),
            local_install_path=str(tmp_path / "mc"),
            local_port=4430,
        )
    )
    await db.commit()

    # Non-admin technician gets HTTP 403
    with pytest.raises(HTTPException) as exc403:
        await web_remote_settings_diagnostics(_request(tech.id), download=0, db=db)
    assert exc403.value.status_code == 403

    # Mock live probe echoing a secret to verify redaction
    async def fake_diag(_cfg, timeout=8.0):
        return {"ok": False, "error": f"Auth failed with {secret_pass}", "hint": "Check credentials."}

    monkeypatch.setattr("app.core.meshcentral.diagnose_meshcentral_connection", fake_diag)

    resp = await web_remote_settings_diagnostics(_request(admin.id), download=0, db=db)
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"
    body = json.loads(resp.body)
    assert body["success"] is True
    assert secret_pass not in body["report_text"]
    assert secret_key not in body["report_text"]
    assert secret_pass not in json.dumps(body)
    assert secret_key not in json.dumps(body)
    assert "<redacted>" in body["report_text"]
    assert "tickets.hubconnect.co.za:4433" in body["report_text"]

    dl = await web_remote_settings_diagnostics(_request(admin.id), download=1, db=db)
    assert dl.status_code == 200
    assert "attachment; filename=\"meshcentral-diagnostics.txt\"" in dl.headers["content-disposition"]
    assert secret_pass not in dl.body.decode("utf-8")


def test_no_server_side_login_token_key_generation_endpoint():
    routes = Path("app/web/meshcentral_routes.py").read_text(encoding="utf-8")
    settings_ui = Path("app/templates/remote/settings.html").read_text(encoding="utf-8")
    site_ui = Path("app/templates/admin/site_settings.html").read_text(encoding="utf-8")
    assert "remote/settings/generate-key" not in routes
    assert "remote/settings/generate-key" not in settings_ui
    assert "remote/settings/generate-key" not in site_ui


def test_installer_seeds_one_workspace_not_every_tenant():
    script = Path("setup_meshcentral.sh").read_text(encoding="utf-8")
    assert "--workspace-id" in script
    assert "CEM_MESHCENTRAL_WORKSPACE_ID" in script
    assert "order_by(Workspace.id.asc()).limit(1)" in script
    assert "does not exist; nothing was seeded" in script
    assert "no CEM workspace exists; nothing was seeded" in script
    assert script.count("sys.exit(1)") >= 2
    assert "for wid in ws_ids" not in script
    assert "select(Workspace.id)" not in script


def test_installer_root_hint_never_echoes_admin_password():
    script = Path("setup_meshcentral.sh").read_text(encoding="utf-8")
    assert "Try:  sudo bash $0 $*" not in script
    assert "ORIGINAL_ARGS=(" in script
    assert "<redacted>" in script


def test_mesh_installer_shell_escapes_su_arguments_and_redacts_attached_passwords():
    script = Path("setup_meshcentral.sh").read_text(encoding="utf-8")
    assert '--admin-pass=*)' in script
    assert '-p*)' in script
    assert '-p*|--admin-pass=*) SAFE_ARGS="$SAFE_ARGS <redacted>"' in script
    assert "q_dir=$(printf '%q' \"$MC_DIR\")" in script
    assert "q_user=$(printf '%q' \"$ADMIN_USER\")" in script
    assert "q_pass=$(printf '%q' \"$ADMIN_PASS\")" in script
    assert "cd '$MC_DIR'" not in script
    assert "--pass '$ADMIN_PASS'" not in script
    assert "MESHCENTRAL_DIR='$MC_DIR'" not in script
    assert "CEM_MESHCENTRAL_WORKSPACE_ID='$TARGET_WORKSPACE_ID'" not in script
    assert "q_crm_dir=$(printf '%q' \"$CRM_DIR\")" in script
    assert "q_crm_py=$(printf '%q' \"$CRM_PY\")" in script
    assert "q_sync_script=$(printf '%q' \"$SYNC_SCRIPT\")" in script
    assert "q_autolink_file=$(printf '%q' \"$AUTOLINK_FILE\")" in script
    assert "q_workspace_id=$(printf '%q' \"$TARGET_WORKSPACE_ID\")" in script


def test_mesh_installer_does_not_claim_ready_after_dependency_or_sync_failure():
    script = Path("setup_meshcentral.sh").read_text(encoding="utf-8")
    assert '"$CRM_PY" -m pip install -q aiohttp cryptography || true' in script
    assert 'print_error "Python dependency verification failed (aiohttp, cryptography)."' in script
    assert '"$CRM_PY" -c "import aiohttp, cryptography"' in script
    assert "CEM will auto-import" not in script
    assert "manual Auto-Setup is required" in script
    assert "Python dependency verification failed" in script
    assert "curl -fsSL https://rpm.nodesource.com/setup_20.x | bash - >/dev/null" not in script
    assert "curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null" not in script
    assert "ACCOUNT_OK=0" in script
    assert "ADMIN_OK=0" in script
    assert "Could not configure internal MeshCentral admin account" in script
    assert "TOKEN_KEY_OK=0" in script
    assert "Could not retrieve Login Token Key" in script


def test_installers_fail_closed_on_secrets_dependencies_and_health():
    ubuntu = Path("install_ubuntu.sh").read_text(encoding="utf-8")
    assert "RANDOM_SECRET=" in ubuntu
    assert "chmod 600 .env" in ubuntu
    assert 'chown "$SERVICE_USER:$SERVICE_USER" .env' in ubuntu
    assert "change-me-in-production" in ubuntu
    assert "Python dependencies verification failed" in ubuntu
    assert "Database initialization failed" in ubuntu
    assert "Service failed to start" in ubuntu

    domain = Path("setup_domain_ssl.sh").read_text(encoding="utf-8")
    assert 'if [ ! -f "$APP_DIR/.env" ]' in domain
    assert "SERVICE_USER=$(awk -F= '/^User=/" in domain
    assert "Could not update $APP_DIR/.env" in domain
    assert "Could not reach ${SCHEME}://${PRIMARY_DOMAIN}/health" in domain
    # The public health failure must stop the success banner.
    failure_line = domain.index("Could not reach ${SCHEME}://${PRIMARY_DOMAIN}/health")
    banner_line = domain.index("Domain setup complete")
    assert "exit 1" in domain[failure_line:banner_line]


@pytest.mark.asyncio
async def test_linux_installer_single_quotes_dollar_mesh_ids_and_passes_bash_syntax():
    import subprocess

    cfg = MeshCentralSettings(
        workspace_id=1,
        enabled=True,
        server_url="https://tickets.hubconnect.co.za:4433",
        verify_ssl=True,
    )
    mesh_id_with_dollar = "mesh//abc$sUsy@123"
    payload, filename, ctype = await mc.proxy_agent_installer(
        cfg,
        mesh_id_with_dollar,
        platform="linux",
        brand_name="Kyotech",
    )
    script_text = payload.decode("utf-8")
    assert filename == "Kyotech-Support-Linux.sh"
    assert ctype == "text/x-shellscript"
    # Mesh ID containing $sUsy MUST be single-quoted so bash `set -u` never expands $sUsy
    assert "'abc$sUsy@123'" in script_text
    assert '"abc$sUsy@123"' not in script_text
    assert "tr -d '\\r'" in script_text

    check = subprocess.run(["bash", "-n"], input=script_text, text=True, capture_output=True)
    assert check.returncode == 0, check.stderr


# ---------------------------------------------------------------------------
# Overnight slice: all-OS installers, Linux 1-line portal command, live refresh
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_linux_public_portal_shows_one_line_command_without_upstream_leak(seeded):
    from datetime import datetime, timedelta
    from app.models.meshcentral import MeshCentralAgentInvite
    from app.web.meshcentral_routes import web_remote_public_invite_page

    db, ws1, _ws2, admin, _tech, _other = seeded
    db.add(MeshCentralSettings(
        workspace_id=ws1.id,
        enabled=True,
        server_url="https://mesh.hubconnect.co.za:4433",
        username="admin",
        password_encrypted=mc.encrypt_secret("Pass123-super-secret"),
        default_mesh_id="mesh//GROUP1",
    ))
    now = datetime.utcnow()
    for platform, token in (("linux", "linux-token123"), ("windows64", "win-token123"), ("macos", "mac-token123")):
        db.add(MeshCentralAgentInvite(
            workspace_id=ws1.id,
            token=token,
            mesh_id="mesh//GROUP1",
            mesh_name="Acme Clients",
            client_name="Sarah — Reception",
            platform=platform,
            install_mode="install",
            created_by_id=admin.id,
            created_at=now,
            expires_at=now + timedelta(minutes=15),
        ))
    await db.commit()

    def public_page(token):
        req = _request(None, f"/web/client/remote/get/{token}")
        req.url = SimpleNamespace(
            path=f"/web/client/remote/get/{token}",
            scheme="https",
            netloc="tickets.hubconnect.co.za",
        )
        return web_remote_public_invite_page(req, token, db=db)

    linux_html = bytes((await public_page("linux-token123")).body).decode()
    one_line = (
        "curl -fsSL https://tickets.hubconnect.co.za"
        "/web/client/remote/get/linux-token123/download | sudo bash"
    )
    assert one_line in linux_html
    assert "Copy Command" in linux_html
    assert "1-Click" in linux_html
    assert "Step 1" in linux_html and "Step 2" in linux_html
    assert "sudo bash" in linux_html
    # Nothing about the upstream MeshCentral server may reach the public page.
    for leak in (
        "mesh.hubconnect.co.za", "4433", "meshagents", "x-meshauth",
        "Pass123-super-secret", "mesh//GROUP1", "/meshcentral-data/",
    ):
        assert leak not in linux_html, f"public page leaked {leak!r}"

    win_html = bytes((await public_page("win-token123")).body).decode()
    assert "SmartScreen" in win_html or "More info" in win_html
    assert "sudo bash" not in win_html

    mac_html = bytes((await public_page("mac-token123")).body).decode()
    assert ".pkg" in mac_html.lower() and "double-click" in mac_html.lower()
    assert "sudo bash" not in mac_html

    # A spoofed Host header must never end up inside a `curl | sudo bash` command.
    spoof_req = _request(None, "/web/client/remote/get/linux-token123")
    spoof_req.url = SimpleNamespace(
        path="/web/client/remote/get/linux-token123",
        scheme="https",
        netloc="evil.example.invalid",
    )
    spoof_html = bytes((await web_remote_public_invite_page(
        spoof_req, "linux-token123", db=db
    )).body).decode()
    assert "evil.example.invalid" not in spoof_html
    assert (
        "curl -fsSL https://tickets.hubconnect.co.za"
        "/web/client/remote/get/linux-token123/download | sudo bash"
    ) in spoof_html


@pytest.mark.asyncio
async def test_devices_json_requires_auth_and_enforces_workspace_isolation(seeded):
    from app.web.meshcentral_routes import web_remote_devices_json

    db, ws1, ws2, _admin, tech, other_admin = seeded
    db.add_all([
        MeshCentralDevice(workspace_id=ws1.id, node_id="node-a1", name="Acme PC 1",
                          client_name="Acme Alpha", is_online=True, is_pinned=True),
        MeshCentralDevice(workspace_id=ws1.id, node_id="node-a2", name="Acme PC 2",
                          client_name="Acme Alpha", is_online=False),
        MeshCentralDevice(workspace_id=ws1.id, node_id="node-a3", name="Acme PC 3",
                          client_name="Acme Beta", is_online=True),
        MeshCentralDevice(workspace_id=ws2.id, node_id="node-r1", name="Rival PC",
                          client_name="Rival Office", is_online=True),
    ])
    await db.commit()

    unauth = await web_remote_devices_json(_request(None), db=db)
    assert unauth.status_code == 401
    assert json.loads(unauth.body)["success"] is False

    ws1_resp = await web_remote_devices_json(_request(tech.id), db=db)
    assert ws1_resp.status_code == 200
    ws1_data = json.loads(ws1_resp.body)
    assert ws1_data["success"] is True
    assert {d["name"] for d in ws1_data["devices"]} == {"Acme PC 1", "Acme PC 2", "Acme PC 3"}
    assert ws1_data["stats"] == {
        "total": 3, "online": 2, "offline": 1, "pinned": 1, "groups": 2,
    }

    ws2_resp = await web_remote_devices_json(_request(other_admin.id), db=db)
    ws2_data = json.loads(ws2_resp.body)
    assert {d["name"] for d in ws2_data["devices"]} == {"Rival PC"}
    assert ws2_data["stats"] == {
        "total": 1, "online": 1, "offline": 0, "pinned": 0, "groups": 1,
    }


@pytest.mark.asyncio
async def test_macos_installer_magic_accepts_pkg_macho_and_shell_fallback(monkeypatch):
    cfg = _mc_settings()

    # Primary macOS agent (id=10005) is a normal xar! .pkg archive.
    state = _fake_aiohttp(monkeypatch, get_responses=[(200, b"xar!-signed-pkg-body")])
    payload, filename, ctype = await mc.proxy_agent_installer(cfg, "mesh//GROUP1", platform="macos")
    assert payload == b"xar!-signed-pkg-body"
    assert filename.endswith(".pkg")
    assert ctype == "application/octet-stream"
    assert "id=10005" in state["get_calls"][0]["url"]

    # If 10005 is unavailable, agent id=29 may return a Mach-O binary.
    state = _fake_aiohttp(monkeypatch, get_responses=[
        (404, b""), (200, b"\xcf\xfa\xed\xfe-macho-agent"),
    ])
    payload, _filename, _ctype = await mc.proxy_agent_installer(cfg, "mesh//GROUP1", platform="macos")
    assert payload.startswith(b"\xcf\xfa\xed\xfe")
    assert "id=29" in state["get_calls"][1]["url"]

    # Some MeshCentral versions serve the legacy macOS installer at
    # /meshosxagent?id=29 as a shell script; accept it rather than fail closed.
    state = _fake_aiohttp(monkeypatch, get_responses=[
        (404, b""), (404, b""), (200, b"#!/bin/bash\necho macos installer\n"),
    ])
    payload, _filename, _ctype = await mc.proxy_agent_installer(cfg, "mesh//GROUP1", platform="macos")
    assert payload.startswith(b"#!/bin/bash")
    assert any("meshosxagent" in call["url"] and "id=29" in call["url"]
               for call in state["get_calls"])

    # A shell body from the primary macOS endpoint is not a valid .pkg: keep
    # walking the fallbacks instead of accepting it and hiding id=29.
    state = _fake_aiohttp(monkeypatch, get_responses=[
        (200, b"#!/bin/sh\necho not-the-pkg"),
        (404, b""),
        (200, b"#!/bin/bash\necho legacy-shell-agent\n"),
    ])
    payload, filename, ctype = await mc.proxy_agent_installer(cfg, "mesh//GROUP1", platform="macos")
    assert len(state["get_calls"]) == 3
    assert payload.startswith(b"#!/bin/bash")
    assert filename.endswith(".sh")
    assert ctype == "text/x-shellscript"
    assert "id=10005" in state["get_calls"][0]["url"]
    assert "meshosxagent" in state["get_calls"][2]["url"]

    # Windows agent IDs and Linux script mode stay distinct.
    for platform, expected_id in (("windows64", "4"), ("windows32", "3")):
        state = _fake_aiohttp(monkeypatch, get_responses=[(200, b"MZ-agent")])
        await mc.proxy_agent_installer(cfg, "mesh//GROUP1", platform=platform)
        assert f"id={expected_id}" in state["get_calls"][0]["url"]
    state = _fake_aiohttp(monkeypatch)
    payload, _filename, _ctype = await mc.proxy_agent_installer(
        cfg, "mesh//GROUP1", platform="linux"
    )
    assert b"meshagents?script=1" in payload
    assert state["get_calls"] == []  # Linux is generated locally; no upstream call


@pytest.mark.asyncio
async def test_remote_dashboard_exposes_auto_refresh_and_inline_linux_copy(seeded):
    from html.parser import HTMLParser
    from app.web.meshcentral_routes import web_remote_dashboard

    db, ws1, _ws2, _admin, tech, _other = seeded
    db.add(MeshCentralSettings(
        workspace_id=ws1.id, enabled=True,
        server_url="https://mesh.hubconnect.co.za:4433", verify_ssl=True,
    ))
    await db.commit()

    page = await web_remote_dashboard(_request(tech.id), db=db)
    html = bytes(page.body).decode()
    assert "Auto-refresh (30s)" in html
    assert "autoRefresh" in html
    assert "/web/remote/devices/json" in html
    assert "Copy 1-Line Command" in html
    assert "latestInvite.platform === 'linux'" in html
    assert "inv.platform !== 'linux'" in html
    assert "linuxOneLineCommand" in html
    assert "refreshing: false" in html and "refreshSeq: 0" in html
    assert "seq !== this.refreshSeq" in html
    assert "remote-stats-data" in html

    # `tojson` output contains quotes; embedding it directly in the x-data
    # attribute would truncate the Alpine component at `liveStats: {`.
    class _XDataCollector(HTMLParser):
        def __init__(self):
            super().__init__()
            self.values: list[str] = []

        def handle_starttag(self, tag, attrs):
            for name, value in attrs:
                if name == "x-data" and value:
                    self.values.append(value)

    collector = _XDataCollector()
    collector.feed(html)
    assert collector.values, "dashboard x-data attribute missing"
    x_data = "\n".join(collector.values)
    assert "liveStats:" in x_data
    assert "notify(msg" in x_data
    assert "refreshDevices" in x_data
    assert "toast: null" in x_data


# ---------------------------------------------------------------------------
# Slice 2: ticket -> remote support hand-off and admin audit CSV export
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_remote_audit_export_is_admin_only_workspace_scoped_and_formula_safe(seeded):
    from app.web.meshcentral_routes import web_remote_audit_export

    db, ws1, ws2, admin, tech, other_admin = seeded

    injected_tech = User(
        username="injected", full_name="\ufeff=CMD('from-name')", hashed_password="x",
        email="injected@acme.test", workspace_id=ws1.id, is_admin=False, is_active=True,
    )
    db.add(injected_tech)
    await db.flush()
    db.add_all([
        MeshCentralSessionLog(
            workspace_id=ws1.id,
            device_id=7,
            node_id="=CMD('calc.exe')",
            device_name='+SUM(1+1), "unsafe"',
            user_id=admin.id,
            view_mode="desktop",
            started_at=datetime(2026, 11, 24, 8, 30),
        ),
        MeshCentralSessionLog(
            workspace_id=ws1.id,
            device_id=8,
            node_id="@HYPERLINK(\"http://evil.invalid\")",
            device_name="Reception PC",
            user_id=tech.id,
            view_mode="terminal",
            started_at=datetime(2026, 11, 24, 9, 15),
        ),
        MeshCentralSessionLog(
            workspace_id=ws1.id,
            device_id=10,
            node_id="SAFE10",
            device_name="Injected Name PC",
            user_id=injected_tech.id,
            view_mode="desktop",
            started_at=datetime(2026, 11, 24, 9, 30),
        ),
        MeshCentralSessionLog(
            workspace_id=ws2.id,
            device_id=9,
            node_id="RIVAL-NODE-SECRET",
            device_name="Rival Machine",
            user_id=other_admin.id,
            view_mode="desktop",
            started_at=datetime(2026, 11, 24, 9, 45),
        ),
    ])
    await db.commit()

    with pytest.raises(HTTPException) as denied:
        await web_remote_audit_export(_request(tech.id, "/web/remote/audit/export.csv"), db)
    assert denied.value.status_code == 403

    response = await web_remote_audit_export(_request(admin.id, "/web/remote/audit/export.csv"), db)
    assert response.status_code == 200
    assert response.media_type.startswith("text/csv")
    assert response.headers["cache-control"] == "no-store"
    assert "attachment" in response.headers["content-disposition"]

    rows = list(csv.reader(io.StringIO(response.body.decode())))
    assert any("Technician" in cell for cell in rows[0])
    assert any("Device" in cell for cell in rows[0])
    assert any("Node" in cell for cell in rows[0])
    assert any("Mode" in cell for cell in rows[0])
    assert any("Started" in cell for cell in rows[0])
    assert len(rows) == 4  # header + only this workspace's three sessions

    flat = [cell for row in rows[1:] for cell in row]
    assert not any("RIVAL-NODE-SECRET" in cell or "Rival Machine" in cell for cell in flat)
    assert any(cell.startswith("'=CMD('calc.exe')") for cell in flat)
    assert any(cell.startswith("'+SUM(1+1)") for cell in flat)
    assert any(cell.startswith("'@HYPERLINK") for cell in flat)
    assert any(cell.startswith("'\ufeff=CMD('from-name')") for cell in flat)  # invisible leader too
    assert any(cell == "'+SUM(1+1), \"unsafe\"" for cell in flat)  # comma/quote stays one CSV cell
    assert any("Alice Admin" in cell or "admin" in cell for cell in flat)
    assert any("Tom Tech" in cell or "tech" in cell for cell in flat)

    rival = await web_remote_audit_export(_request(other_admin.id, "/web/remote/audit/export.csv"), db)
    rival_rows = list(csv.reader(io.StringIO(rival.body.decode())))
    assert len(rival_rows) == 2
    assert any("RIVAL-NODE-SECRET" in cell for cell in rival_rows[1])
    assert not any("=CMD" in cell for cell in rival_rows[1])


@pytest.mark.asyncio
async def test_remote_dashboard_export_link_is_visible_only_to_admins(seeded):
    db, _ws1, _ws2, admin, tech, _other = seeded

    admin_html = bytes((await web_remote_dashboard(_request(admin.id), db=db)).body).decode()
    assert 'href="/web/remote/audit/export.csv"' in admin_html
    assert "Export CSV" in admin_html

    tech_html = bytes((await web_remote_dashboard(_request(tech.id), db=db)).body).decode()
    assert 'href="/web/remote/audit/export.csv"' not in tech_html
    assert "Export CSV" not in tech_html


@pytest.mark.asyncio
async def test_site_settings_meshcentral_form_preserves_login_user_id(seeded):
    """The admin form must submit the same SSO identity as the remote settings form."""
    from app.web.routes import web_admin_site_settings

    db, ws1, _ws2, admin, _tech, _other = seeded
    req = _request(admin.id, "/web/remote/settings/save")
    await web_remote_settings_save(
        req,
        enabled="1",
        server_url="https://mesh.acme.test",
        domain="",
        username="admin",
        password="",
        clear_password=None,
        login_token_key="",
        clear_login_token_key=None,
        login_user_id="user//meshsvc",
        default_view_mode=11,
        hide_mesh_chrome="1",
        verify_ssl="1",
        return_to="/web/admin/site-settings",
        db=db,
    )

    html = bytes((await web_admin_site_settings(_request(admin.id), db=db)).body).decode()
    assert 'name="login_user_id"' in html
    assert 'value="user//meshsvc"' in html
    assert "data.login_user_id && form.querySelector('[name=login_user_id]')" in html

    saved = (
        await db.execute(select(MeshCentralSettings).where(MeshCentralSettings.workspace_id == ws1.id))
    ).scalar_one()
    assert saved.login_user_id == "user//meshsvc"


@pytest.mark.asyncio
async def test_ticket_remote_support_card_is_gated_and_never_leaks_meshcentral_settings(seeded):
    from app.models.company import Company
    from app.models.ticket import Ticket
    from app.web.routes import web_tickets_detail

    db, ws1, ws2, admin, tech, other_admin = seeded

    company = Company(name="Northwind HQ", workspace_id=ws1.id, created_by=admin.id)
    rival_device = MeshCentralDevice(
        workspace_id=ws2.id, node_id="RIVAL999", name="Rival Reception PC", client_name="Northwind HQ",
        is_online=True,
    )
    db.add(company)
    db.add(rival_device)
    await db.flush()
    ticket = Ticket(
        ticket_number="TKT-REM-2",
        subject="Printer offline",
        workspace_id=ws1.id,
        created_by_id=admin.id,
        guest_company="Northwind HQ",
        guest_name="Sarah",
        guest_surname="Reception",
    )
    db.add(ticket)
    await db.flush()
    db.add_all([
        MeshCentralDevice(
            workspace_id=ws1.id, node_id="MATCH001", name="Sarah Reception PC",
            client_name="Northwind HQ", mesh_name="Northwind HQ", company_id=company.id,
            is_online=True,
        ),
        MeshCentralDevice(
            workspace_id=ws1.id, node_id="MATCH002", name="Archive Server",
            mesh_name="Northwind HQ", is_online=False,
        ),
        MeshCentralDevice(
            workspace_id=ws1.id, node_id="OTHER001", name="Unrelated PC",
            client_name="Somebody Else", is_online=True,
        ),
    ])
    settings = MeshCentralSettings(
        workspace_id=ws1.id,
        enabled=True,
        server_url="https://mesh.acme.test",
        username="svc-mesh",
        password_encrypted=mc.encrypt_secret("PlainTextPassword123"),
        login_token_key_encrypted=mc.encrypt_secret("LoginTokenSecret123"),
        default_mesh_id="mesh//G1",
        default_mesh_name="Northwind HQ",
    )
    db.add(settings)
    await db.commit()

    request = _request(admin.id, f"/web/tickets/{ticket.id}")
    html = bytes((await web_tickets_detail(request, ticket.id, db=db)).body).decode()

    assert 'id="ticket-remote-support-card"' in html, "enabled workspace should show Remote Support"
    assert "Remote Support" in html
    assert f"/web/remote/session/{rival_device.id}" not in html
    device_ids = re.findall(r'/web/remote/session/(\d+)\?mode=desktop', html)
    assert len(device_ids) == 2, "only this workspace's matching PCs are offered"
    assert any(f"/web/remote/session/{device_id}?mode=terminal" in html for device_id in device_ids)
    assert "Send 15-Min Support Link" in html
    assert 'name="client_name" value="Northwind HQ"' in html
    assert 'name="expires_minutes" value="15"' in html
    assert 'name="mesh_id"' not in html  # invite uses the workspace default group, not a fuzzy match
    assert "/web/remote/invites" in html
    assert "data.invite.share_url" in html  # generated /web/client/remote/get/<token> URL
    assert "link.textContent = shareUrl" in html
    assert "Copy Link" in html and "Copy Client Message" in html

    for leak in (
        "https://mesh.acme.test", "mesh.acme.test", "/web/remote/settings", "MeshCentral Setup",
        "PlainTextPassword123", "LoginTokenSecret123", "password_encrypted", "login_token_key",
        "svc-mesh", "Rival Reception PC",
    ):
        assert leak not in html, f"ticket page leaked {leak!r}"

    # Technicians need the same 1-click hand-off, but no CSV/admin export.
    tech_html = bytes((await web_tickets_detail(
        _request(tech.id, f"/web/tickets/{ticket.id}"), ticket.id, db=db
    )).body).decode()
    assert 'id="ticket-remote-support-card"' in tech_html
    assert "/web/remote/audit/export.csv" not in tech_html

    settings.enabled = False
    await db.commit()
    disabled_html = bytes((await web_tickets_detail(request, ticket.id, db=db)).body).decode()
    assert 'id="ticket-remote-support-card"' not in disabled_html
    assert "Send 15-Min Support Link" not in disabled_html
    assert "/web/remote/invites" not in disabled_html
    assert "/web/remote/session/" not in disabled_html
    assert "https://mesh.acme.test" not in disabled_html


@pytest.mark.asyncio
async def test_ticket_remote_matching_ignores_short_ambiguous_names(seeded):
    from app.models.ticket import Ticket
    from app.web.routes import web_tickets_detail

    db, ws1, _ws2, admin, _tech, _other = seeded

    ticket = Ticket(
        ticket_number="TKT-REM-3", subject="Laptop slow", workspace_id=ws1.id,
        created_by_id=admin.id, guest_company="IT", guest_name="Ann",
    )
    db.add(ticket)
    await db.flush()
    db.add_all([
        MeshCentralDevice(
            workspace_id=ws1.id, node_id="KITCHEN", name="Kitchen PC",
            client_name="Other Clients", is_online=True,
        ),
        MeshCentralDevice(
            workspace_id=ws1.id, node_id="BANNER", name="Banner Server",
            mesh_name="Other Clients", is_online=True,
        ),
    ])
    db.add(MeshCentralSettings(
        workspace_id=ws1.id, enabled=True, server_url="https://mesh.acme.test",
    ))
    await db.commit()

    html = bytes((await web_tickets_detail(
        _request(admin.id, f"/web/tickets/{ticket.id}"), ticket.id, db=db
    )).body).decode()
    assert 'id="ticket-remote-support-card"' in html
    assert "Kitchen PC" not in html and "Banner Server" not in html
    assert "/web/remote/session/" not in html
    assert 'name="client_name" value="IT"' in html


