"""Web routes for the custom MeshCentral Remote Client Access dashboard."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.meshcentral import (
    VIEW_MODE_MAP,
    build_direct_url,
    build_embed_url,
    decrypt_secret,
    detect_os_family,
    encrypt_secret,
    fetch_meshcentral_inventory,
    normalize_domain,
    normalize_node_id,
    normalize_server_url,
    resolve_view_mode,
    sync_workspace_devices,
)
from app.models.company import Company
from app.models.meshcentral import (
    MeshCentralDevice,
    MeshCentralSessionLog,
    MeshCentralSettings,
)
from app.models.user import User
from app.models.workspace import Workspace
from app.web.routes import templates

logger = logging.getLogger(__name__)

router = APIRouter()


async def _require_workspace_user(request: Request, db: AsyncSession):
    """Return signed-in active user with workspace, or a redirect/403."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None, RedirectResponse("/web/login", status_code=303)

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_active:
        request.session.clear()
        return None, RedirectResponse("/web/login", status_code=303)

    if not user.workspace_id:
        raise HTTPException(status_code=403, detail="No company assigned to this account")

    return user, None


async def _require_workspace_admin(request: Request, db: AsyncSession):
    user, redirect = await _require_workspace_user(request, db)
    if redirect:
        return None, redirect
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user, None


async def _get_or_none_settings(db: AsyncSession, workspace_id: int) -> Optional[MeshCentralSettings]:
    return (
        await db.execute(
            select(MeshCentralSettings).where(MeshCentralSettings.workspace_id == workspace_id)
        )
    ).scalar_one_or_none()


async def _get_workspace_device(
    db: AsyncSession, workspace_id: int, device_id: int
) -> Optional[MeshCentralDevice]:
    return (
        await db.execute(
            select(MeshCentralDevice).where(
                MeshCentralDevice.id == device_id,
                MeshCentralDevice.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Custom Remote Client Access Dashboard
# ---------------------------------------------------------------------------

@router.get("/remote", response_class=HTMLResponse)
async def web_remote_dashboard(request: Request, db: AsyncSession = Depends(get_session)):
    user, redirect = await _require_workspace_user(request, db)
    if redirect:
        return redirect

    workspace = (
        await db.execute(select(Workspace).where(Workspace.id == user.workspace_id))
    ).scalar_one_or_none()
    mc_settings = await _get_or_none_settings(db, user.workspace_id)

    devices = (
        await db.execute(
            select(MeshCentralDevice)
            .where(MeshCentralDevice.workspace_id == user.workspace_id)
            .order_by(
                desc(MeshCentralDevice.is_pinned),
                desc(MeshCentralDevice.is_online),
                MeshCentralDevice.name.asc(),
            )
        )
    ).scalars().all()

    companies = (
        await db.execute(
            select(Company)
            .where(Company.workspace_id == user.workspace_id)
            .order_by(Company.name.asc())
        )
    ).scalars().all()

    recent_logs = (
        await db.execute(
            select(MeshCentralSessionLog)
            .where(MeshCentralSessionLog.workspace_id == user.workspace_id)
            .order_by(desc(MeshCentralSessionLog.started_at))
            .limit(8)
        )
    ).scalars().all()

    # Build group summary counts for the left client-group rail
    group_map: dict[str, dict] = {}
    for dev in devices:
        label = (dev.client_name or dev.mesh_name or "Unassigned").strip() or "Unassigned"
        bucket = group_map.setdefault(label, {"name": label, "total": 0, "online": 0})
        bucket["total"] += 1
        if dev.is_online:
            bucket["online"] += 1
    groups = sorted(group_map.values(), key=lambda g: (-g["online"], g["name"].lower()))

    # Serialize devices cleanly for instant client-side Alpine.js filtering
    devices_payload = [
        {
            "id": d.id,
            "node_id": d.node_id,
            "name": d.name,
            "hostname": d.hostname or "",
            "client": (d.client_name or d.mesh_name or "Unassigned").strip() or "Unassigned",
            "mesh_name": d.mesh_name or "",
            "os_desc": d.os_desc or "",
            "os_family": d.os_family or "other",
            "ip_address": d.ip_address or "",
            "is_online": bool(d.is_online),
            "is_pinned": bool(d.is_pinned),
            "notes": d.notes or "",
            "last_connected_at": d.last_connected_at.strftime("%Y-%m-%d %H:%M") if d.last_connected_at else "",
            "last_synced_at": d.last_synced_at.strftime("%Y-%m-%d %H:%M") if d.last_synced_at else "",
        }
        for d in devices
    ]

    online_count = sum(1 for d in devices if d.is_online)
    pinned_count = sum(1 for d in devices if d.is_pinned)
    is_configured = bool(mc_settings and mc_settings.enabled and mc_settings.server_url)
    has_sso = bool(
        mc_settings
        and mc_settings.login_token_key_encrypted
        and (mc_settings.login_user_id or mc_settings.username)
    )
    has_sync_creds = bool(
        mc_settings
        and (
            (mc_settings.username and mc_settings.password_encrypted)
            or (mc_settings.login_token_key_encrypted and (mc_settings.login_user_id or mc_settings.username))
        )
    )

    flash_message = request.session.pop("flash_message", None)
    flash_type = request.session.pop("flash_type", None)

    return templates.TemplateResponse(
        "remote/index.html",
        {
            "request": request,
            "user": user,
            "workspace": workspace,
            "header_title": "Remote Client Access",
            "mc_settings": mc_settings,
            "is_configured": is_configured,
            "has_sso": has_sso,
            "has_sync_creds": has_sync_creds,
            "devices": devices,
            "devices_payload": devices_payload,
            "groups": groups,
            "companies": companies,
            "recent_logs": recent_logs,
            "stats": {
                "total": len(devices),
                "online": online_count,
                "offline": len(devices) - online_count,
                "pinned": pinned_count,
                "groups": len(groups),
            },
            "flash_message": flash_message,
            "flash_type": flash_type,
        },
    )


# ---------------------------------------------------------------------------
# Embedded Live Remote Session View (Client PC inside our Dashboard)
# ---------------------------------------------------------------------------

@router.get("/remote/session/{device_id}", response_class=HTMLResponse)
async def web_remote_session(
    request: Request,
    device_id: int,
    mode: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_session),
):
    user, redirect = await _require_workspace_user(request, db)
    if redirect:
        return redirect

    workspace = (
        await db.execute(select(Workspace).where(Workspace.id == user.workspace_id))
    ).scalar_one_or_none()
    mc_settings = await _get_or_none_settings(db, user.workspace_id)
    device = await _get_workspace_device(db, user.workspace_id, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Client device not found")

    if not mc_settings or not mc_settings.enabled or not mc_settings.server_url:
        request.session["flash_message"] = "Configure and enable MeshCentral in Remote Settings before connecting."
        request.session["flash_type"] = "error"
        return RedirectResponse("/web/remote", status_code=303)

    embed = build_embed_url(mc_settings, device.node_id, mode=mode)
    direct_url = build_direct_url(mc_settings, device.node_id, mode=embed["mode"])

    # Record connection audit log & last-connected timestamp
    now = datetime.utcnow()
    device.last_connected_at = now
    device.last_connected_by_id = user.id
    db.add(
        MeshCentralSessionLog(
            workspace_id=user.workspace_id,
            device_id=device.id,
            node_id=device.node_id,
            device_name=device.name,
            user_id=user.id,
            view_mode=embed["mode"],
            started_at=now,
        )
    )
    await db.commit()

    # Sibling devices in the same client group for 1-click quick switching
    group_label = device.client_name or device.mesh_name
    siblings_query = select(MeshCentralDevice).where(
        MeshCentralDevice.workspace_id == user.workspace_id,
        MeshCentralDevice.id != device.id,
    )
    if group_label:
        siblings_query = siblings_query.where(
            (MeshCentralDevice.client_name == group_label)
            | (MeshCentralDevice.mesh_name == group_label)
        )
    sibling_devices = (
        await db.execute(
            siblings_query.order_by(desc(MeshCentralDevice.is_online), MeshCentralDevice.name.asc()).limit(10)
        )
    ).scalars().all()

    return templates.TemplateResponse(
        "remote/session.html",
        {
            "request": request,
            "user": user,
            "workspace": workspace,
            "header_title": f"Remote: {device.name}",
            "mc_settings": mc_settings,
            "device": device,
            "embed": embed,
            "direct_url": direct_url,
            "sibling_devices": sibling_devices,
            "modes": [
                {"key": "desktop", "label": "Desktop", "icon": "fa-desktop"},
                {"key": "terminal", "label": "Terminal", "icon": "fa-terminal"},
                {"key": "files", "label": "Files", "icon": "fa-folder-open"},
                {"key": "console", "label": "Agent Console", "icon": "fa-code"},
                {"key": "summary", "label": "Device Info", "icon": "fa-circle-info"},
            ],
        },
    )


@router.get("/remote/session/{device_id}/embed-url")
async def web_remote_session_embed_url(
    request: Request,
    device_id: int,
    mode: Optional[str] = Query(default="desktop"),
    db: AsyncSession = Depends(get_session),
):
    """Mint a fresh short-lived SSO embed URL for mode switching or reconnecting."""
    user, redirect = await _require_workspace_user(request, db)
    if redirect:
        return JSONResponse({"success": False, "error": "Authentication required"}, status_code=401)

    mc_settings = await _get_or_none_settings(db, user.workspace_id)
    if not mc_settings or not mc_settings.enabled or not mc_settings.server_url:
        return JSONResponse({"success": False, "error": "MeshCentral is not configured"}, status_code=400)

    device = await _get_workspace_device(db, user.workspace_id, device_id)
    if not device:
        return JSONResponse({"success": False, "error": "Device not found"}, status_code=404)

    embed = build_embed_url(mc_settings, device.node_id, mode=mode)
    direct_url = build_direct_url(mc_settings, device.node_id, mode=embed["mode"])

    now = datetime.utcnow()
    device.last_connected_at = now
    device.last_connected_by_id = user.id
    db.add(
        MeshCentralSessionLog(
            workspace_id=user.workspace_id,
            device_id=device.id,
            node_id=device.node_id,
            device_name=device.name,
            user_id=user.id,
            view_mode=embed["mode"],
            started_at=now,
        )
    )
    await db.commit()

    return JSONResponse({
        "success": True,
        "embed_url": embed["url"],
        "direct_url": direct_url,
        "mode": embed["mode"],
        "sso_active": embed["sso_active"],
    })


# ---------------------------------------------------------------------------
# Device Management & Live Sync Actions
# ---------------------------------------------------------------------------

@router.post("/remote/sync")
async def web_remote_sync(request: Request, db: AsyncSession = Depends(get_session)):
    user, redirect = await _require_workspace_user(request, db)
    if redirect:
        return JSONResponse({"success": False, "error": "Authentication required"}, status_code=401)

    mc_settings = await _get_or_none_settings(db, user.workspace_id)
    if not mc_settings or not mc_settings.server_url:
        return JSONResponse(
            {"success": False, "error": "MeshCentral server is not configured yet."},
            status_code=400,
        )

    try:
        summary = await sync_workspace_devices(db, user.workspace_id, mc_settings)
        return JSONResponse({"success": True, **summary})
    except Exception as exc:
        logger.warning("MeshCentral sync failed for workspace %s: %s", user.workspace_id, exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=502)


@router.post("/remote/devices")
async def web_remote_device_create(
    request: Request,
    name: str = Form(...),
    node_id: str = Form(...),
    client_name: str = Form(""),
    os_desc: str = Form(""),
    ip_address: str = Form(""),
    notes: str = Form(""),
    db: AsyncSession = Depends(get_session),
):
    user, redirect = await _require_workspace_user(request, db)
    if redirect:
        return redirect

    clean_name = (name or "").strip()
    if not clean_name:
        request.session["flash_message"] = "Device name is required."
        request.session["flash_type"] = "error"
        return RedirectResponse("/web/remote", status_code=303)

    try:
        clean_node = normalize_node_id(node_id)
    except ValueError as exc:
        request.session["flash_message"] = str(exc)
        request.session["flash_type"] = "error"
        return RedirectResponse("/web/remote", status_code=303)

    existing = (
        await db.execute(
            select(MeshCentralDevice).where(
                MeshCentralDevice.workspace_id == user.workspace_id,
                MeshCentralDevice.node_id == clean_node,
            )
        )
    ).scalar_one_or_none()

    now = datetime.utcnow()
    clean_os = (os_desc or "").strip() or None
    if existing:
        existing.name = clean_name
        existing.client_name = (client_name or "").strip() or existing.client_name
        existing.os_desc = clean_os or existing.os_desc
        existing.os_family = detect_os_family(existing.os_desc)
        existing.ip_address = (ip_address or "").strip() or existing.ip_address
        existing.notes = (notes or "").strip() or existing.notes
        existing.updated_at = now
        request.session["flash_message"] = f"Updated client PC '{existing.name}'."
    else:
        device = MeshCentralDevice(
            workspace_id=user.workspace_id,
            node_id=clean_node,
            name=clean_name,
            client_name=(client_name or "").strip() or None,
            os_desc=clean_os,
            os_family=detect_os_family(clean_os),
            ip_address=(ip_address or "").strip() or None,
            notes=(notes or "").strip() or None,
            is_online=True,
            created_at=now,
            updated_at=now,
        )
        db.add(device)
        request.session["flash_message"] = f"Added client PC '{clean_name}' to Remote Access."

    request.session["flash_type"] = "success"
    await db.commit()
    return RedirectResponse("/web/remote", status_code=303)


@router.post("/remote/devices/{device_id}/notes")
async def web_remote_device_notes(
    request: Request,
    device_id: int,
    notes: str = Form(""),
    client_name: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_session),
):
    user, redirect = await _require_workspace_user(request, db)
    if redirect:
        return JSONResponse({"success": False, "error": "Authentication required"}, status_code=401)

    device = await _get_workspace_device(db, user.workspace_id, device_id)
    if not device:
        return JSONResponse({"success": False, "error": "Device not found"}, status_code=404)

    device.notes = (notes or "").strip() or None
    if client_name is not None:
        device.client_name = client_name.strip() or None
    device.updated_at = datetime.utcnow()
    await db.commit()
    return JSONResponse({"success": True, "notes": device.notes or "", "client_name": device.client_name or ""})


@router.post("/remote/devices/{device_id}/pin")
async def web_remote_device_pin(
    request: Request,
    device_id: int,
    db: AsyncSession = Depends(get_session),
):
    user, redirect = await _require_workspace_user(request, db)
    if redirect:
        return JSONResponse({"success": False, "error": "Authentication required"}, status_code=401)

    device = await _get_workspace_device(db, user.workspace_id, device_id)
    if not device:
        return JSONResponse({"success": False, "error": "Device not found"}, status_code=404)

    device.is_pinned = not device.is_pinned
    device.updated_at = datetime.utcnow()
    await db.commit()
    return JSONResponse({"success": True, "is_pinned": device.is_pinned})


@router.post("/remote/devices/{device_id}/delete")
async def web_remote_device_delete(
    request: Request,
    device_id: int,
    db: AsyncSession = Depends(get_session),
):
    user, redirect = await _require_workspace_admin(request, db)
    if redirect:
        return redirect

    device = await _get_workspace_device(db, user.workspace_id, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    dev_name = device.name
    await db.delete(device)
    await db.commit()
    request.session["flash_message"] = f"Removed '{dev_name}' from Remote Access."
    request.session["flash_type"] = "success"
    return RedirectResponse("/web/remote", status_code=303)


# ---------------------------------------------------------------------------
# Admin MeshCentral Setup & Diagnostics
# ---------------------------------------------------------------------------

@router.get("/remote/settings", response_class=HTMLResponse)
async def web_remote_settings(request: Request, db: AsyncSession = Depends(get_session)):
    user, redirect = await _require_workspace_admin(request, db)
    if redirect:
        return redirect

    workspace = (
        await db.execute(select(Workspace).where(Workspace.id == user.workspace_id))
    ).scalar_one_or_none()
    mc_settings = await _get_or_none_settings(db, user.workspace_id)

    flash_message = request.session.pop("flash_message", None)
    flash_type = request.session.pop("flash_type", None)

    return templates.TemplateResponse(
        "remote/settings.html",
        {
            "request": request,
            "user": user,
            "workspace": workspace,
            "header_title": "MeshCentral Setup",
            "mc_settings": mc_settings,
            "has_password": bool(mc_settings and mc_settings.password_encrypted),
            "has_login_token_key": bool(mc_settings and mc_settings.login_token_key_encrypted),
            "flash_message": flash_message,
            "flash_type": flash_type,
        },
    )


_ALLOWED_SETTINGS_RETURNS = frozenset({"/web/remote/settings", "/web/admin/site-settings"})


def _settings_redirect(request: Request, return_to: Optional[str], message: str, ok: bool) -> RedirectResponse:
    target = return_to if return_to in _ALLOWED_SETTINGS_RETURNS else "/web/remote/settings"
    if target == "/web/admin/site-settings":
        request.session["success_message" if ok else "error_message"] = message
    else:
        request.session["flash_message"] = message
        request.session["flash_type"] = "success" if ok else "error"
    return RedirectResponse(target, status_code=303)


@router.post("/remote/settings/save")
async def web_remote_settings_save(
    request: Request,
    enabled: Optional[str] = Form(None),
    server_url: str = Form(""),
    domain: str = Form(""),
    username: str = Form(""),
    password: str = Form(""),
    clear_password: Optional[str] = Form(None),
    login_token_key: str = Form(""),
    clear_login_token_key: Optional[str] = Form(None),
    login_user_id: str = Form(""),
    default_view_mode: int = Form(11),
    hide_mesh_chrome: Optional[str] = Form(None),
    verify_ssl: Optional[str] = Form(None),
    return_to: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_session),
):
    user, redirect = await _require_workspace_admin(request, db)
    if redirect:
        return redirect

    is_enabled = enabled in ("1", "true", "on", "yes")
    clean_url: Optional[str] = None
    if server_url and server_url.strip():
        try:
            clean_url = normalize_server_url(server_url)
        except ValueError as exc:
            return _settings_redirect(request, return_to, str(exc), ok=False)
    elif is_enabled:
        return _settings_redirect(
            request,
            return_to,
            "Please enter your MeshCentral Server URL before enabling.",
            ok=False,
        )

    try:
        clean_domain = normalize_domain(domain)
    except ValueError as exc:
        return _settings_redirect(request, return_to, str(exc), ok=False)

    if default_view_mode not in VIEW_MODE_MAP.values():
        default_view_mode = 11

    row = await _get_or_none_settings(db, user.workspace_id)
    now = datetime.utcnow()
    if row is None:
        row = MeshCentralSettings(workspace_id=user.workspace_id, created_at=now)
        db.add(row)

    row.enabled = is_enabled
    row.server_url = clean_url
    row.domain = clean_domain
    row.username = (username or "").strip() or None
    row.login_user_id = (login_user_id or "").strip() or None
    row.default_view_mode = default_view_mode
    row.hide_mesh_chrome = hide_mesh_chrome in ("1", "true", "on", "yes")
    row.verify_ssl = verify_ssl in ("1", "true", "on", "yes")
    row.updated_at = now

    if clear_password in ("1", "true", "on"):
        row.password_encrypted = None
    elif password and password.strip():
        row.password_encrypted = encrypt_secret(password)

    if clear_login_token_key in ("1", "true", "on"):
        row.login_token_key_encrypted = None
    elif login_token_key and login_token_key.strip():
        row.login_token_key_encrypted = encrypt_secret(login_token_key)

    await db.commit()
    return _settings_redirect(request, return_to, "MeshCentral settings saved successfully.", ok=True)


@router.post("/remote/settings/test")
async def web_remote_settings_test(request: Request, db: AsyncSession = Depends(get_session)):
    user, redirect = await _require_workspace_admin(request, db)
    if redirect:
        return JSONResponse({"success": False, "error": "Authentication required"}, status_code=401)

    mc_settings = await _get_or_none_settings(db, user.workspace_id)
    if not mc_settings or not mc_settings.server_url:
        return JSONResponse(
            {"success": False, "error": "Save a valid MeshCentral Server URL first."},
            status_code=400,
        )

    try:
        meshes, devices = await fetch_meshcentral_inventory(mc_settings, timeout=10.0)
        online = sum(1 for d in devices if d.get("is_online"))
        return JSONResponse({
            "success": True,
            "groups": len(meshes),
            "devices": len(devices),
            "online": online,
            "sso_ready": bool(decrypt_secret(mc_settings.login_token_key_encrypted)),
        })
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=502)
