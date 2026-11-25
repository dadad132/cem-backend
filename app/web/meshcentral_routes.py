"""Web routes for the custom MeshCentral Remote Client Access dashboard."""
from __future__ import annotations

import csv
import io
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_session
from app.core.meshcentral import (
    AGENT_PLATFORMS,
    VIEW_MODE_MAP,
    auto_import_local_meshcentral_if_present,
    autodetect_local_meshcentral,
    build_embed_url,
    build_embed_url_async,
    build_login_user_id,
    build_meshcentral_diagnostic_report,
    classify_meshcentral_error,
    create_meshcentral_group,
    diagnose_meshcentral_connection,
    detect_os_family,
    encrypt_secret,
    fetch_meshcentral_inventory,
    normalize_domain,
    normalize_mesh_id,
    normalize_node_id,
    normalize_server_url,
    proxy_agent_installer,
    redact_meshcentral_text,
    resolve_view_mode,
    sync_workspace_devices,
    upsert_workspace_groups,
)
from app.models.company import Company
from app.models.meshcentral import (
    MeshCentralAgentInvite,
    MeshCentralDevice,
    MeshCentralGroup,
    MeshCentralSessionLog,
    MeshCentralSettings,
)
from app.models.user import User
from app.models.workspace import Workspace
from app.web.routes import templates

logger = logging.getLogger(__name__)

# Public download links must never be cached or indexed by a search engine.
PUBLIC_LINK_HEADERS = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"}

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


def _wants_interactive_controls(show_controls) -> bool:
    """Normalize the query flag (direct route calls may pass FastAPI's Query default)."""
    if isinstance(show_controls, str):
        return show_controls.strip().lower() in ("1", "true", "on", "yes")
    return show_controls is True


def _form_text(value) -> Optional[str]:
    """Treat FastAPI's Form default object as 'field omitted' on direct calls."""
    return value if isinstance(value, str) else None


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


_DEFAULT_GROUP_NAME = "Client PCs"


async def _persist_discovered_groups(
    db: AsyncSession,
    workspace_id: int,
    settings_row: MeshCentralSettings,
    meshes: dict[str, str],
) -> Optional[tuple[str, str]]:
    """Cache live groups, and remember the first (preferably Client PCs) as default."""
    if not meshes:
        return None
    await upsert_workspace_groups(db, workspace_id, meshes)
    choice: Optional[tuple[str, str]] = None
    if not settings_row.default_mesh_id:
        preferred = next(
            ((mid, name) for mid, name in meshes.items() if _DEFAULT_GROUP_NAME.lower() in name.lower()),
            None,
        )
        choice = preferred or next(iter(meshes.items()))
        settings_row.default_mesh_id, settings_row.default_mesh_name = choice
        # Keep the cached default flag unambiguous when a later discovery pass
        # runs before a default was persisted in settings.
        all_rows = (
            await db.execute(
                select(MeshCentralGroup).where(MeshCentralGroup.workspace_id == workspace_id)
            )
        ).scalars().all()
        for cached in all_rows:
            cached.is_default = cached.mesh_id == choice[0]
    return choice


def _remember_local_port(settings_row: MeshCentralSettings, autolink: Optional[dict]) -> None:
    """Persist a valid loopback port discovered from cem-autolink.json."""
    if not autolink or autolink.get("local_port") is None:
        return
    try:
        port = int(autolink["local_port"])
        if 1 <= port <= 65535:
            settings_row.local_port = port
    except (TypeError, ValueError):
        pass


def _serialize_devices_for_dashboard(devices) -> list[dict]:
    """Serialize devices identically for the initial page and the refresh API."""
    return [
        {
            "id": d.id,
            "node_id": d.node_id,
            "name": d.name,
            "hostname": d.hostname or "",
            "client": (d.client_name or d.mesh_name or "Unassigned").strip() or "Unassigned",
            # Keep the real company label separate from the derived display label so
            # the Edit modal never copies a mesh/group name into client_name.
            "client_name": d.client_name or "",
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


def _group_summary(devices) -> list[dict]:
    """Build the left-rail client-group summary counts for the dashboard."""
    group_map: dict[str, dict] = {}
    for dev in devices:
        label = (dev.client_name or dev.mesh_name or "Unassigned").strip() or "Unassigned"
        bucket = group_map.setdefault(label, {"name": label, "total": 0, "online": 0})
        bucket["total"] += 1
        if dev.is_online:
            bucket["online"] += 1
    return sorted(group_map.values(), key=lambda g: (-g["online"], g["name"].lower()))


def _device_stats(devices, groups) -> dict:
    online_count = sum(1 for d in devices if d.is_online)
    return {
        "total": len(devices),
        "online": online_count,
        "offline": len(devices) - online_count,
        "pinned": sum(1 for d in devices if d.is_pinned),
        "groups": len(groups),
    }


_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t")


def _csv_safe_cell(value) -> str:
    """Keep spreadsheet apps from evaluating audit data as a formula.

    csv.writer handles commas and quotes, but a cell starting with =, +, -, @
    or a tab is still executed by Excel/LibreOffice. Prefixing those with an
    apostrophe keeps the original text visible while making it inert. Invisible
    Unicode leaders (BOM/zero-width marks) and newlines are handled too, so one
    database row can never become multiple CSV rows or smuggle a formula.
    """
    text = "" if value is None else str(value)
    text = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    leading = text.lstrip().lstrip("\ufeff\u200b\u200e\u200f")
    if leading and (leading[0] in _CSV_FORMULA_PREFIXES):
        text = "'" + text
    return text


async def _workspace_device_rows(db: AsyncSession, workspace_id: int) -> list:
    """Workspace-scoped device query shared by the page and refresh endpoint."""
    return (
        await db.execute(
            select(MeshCentralDevice)
            .where(MeshCentralDevice.workspace_id == workspace_id)
            .order_by(
                desc(MeshCentralDevice.is_pinned),
                desc(MeshCentralDevice.is_online),
                MeshCentralDevice.name.asc(),
            )
        )
    ).scalars().all()


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
    # Never bootstrap shared server credentials from an ordinary page visit.
    # Only an admin who opens the explicit setup page may import an autolink file.
    mc_settings = await _get_or_none_settings(db, user.workspace_id)

    devices = await _workspace_device_rows(db, user.workspace_id)

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

    # Group rail counts and Alpine-friendly payload (shared with the refresh API)
    groups = _group_summary(devices)
    devices_payload = _serialize_devices_for_dashboard(devices)
    stats = _device_stats(devices, groups)

    is_configured = bool(mc_settings and mc_settings.enabled and mc_settings.server_url)
    has_sso = bool(
        mc_settings
        and (
            (mc_settings.login_token_key_encrypted and (mc_settings.login_user_id or mc_settings.username))
            or (mc_settings.username and mc_settings.password_encrypted)
        )
    )
    has_sync_creds = bool(
        mc_settings
        and (
            (mc_settings.username and mc_settings.password_encrypted)
            or (mc_settings.login_token_key_encrypted and (mc_settings.login_user_id or mc_settings.username))
        )
    )

    # Known MeshCentral device groups for 1-click installer & expiring client links:
    # persisted rows first, then the saved default, then any groups seen on devices.
    mesh_group_map: dict[str, str] = {}
    for group in (
        await db.execute(
            select(MeshCentralGroup)
            .where(MeshCentralGroup.workspace_id == user.workspace_id)
            .order_by(MeshCentralGroup.name.asc())
        )
    ).scalars().all():
        if group.mesh_id:
            mesh_group_map[group.mesh_id] = group.name or group.mesh_id
    if mc_settings and mc_settings.default_mesh_id:
        mesh_group_map[mc_settings.default_mesh_id] = mc_settings.default_mesh_name or "Default Client Group"
    for d in devices:
        if d.mesh_id and d.mesh_id not in mesh_group_map:
            mesh_group_map[d.mesh_id] = d.mesh_name or d.client_name or d.mesh_id
    mesh_groups = [{"mesh_id": mid, "mesh_name": mname} for mid, mname in mesh_group_map.items()]

    # Active & recent expiring client download links
    now = datetime.utcnow()
    recent_invites = (
        await db.execute(
            select(MeshCentralAgentInvite)
            .where(MeshCentralAgentInvite.workspace_id == user.workspace_id)
            .order_by(desc(MeshCentralAgentInvite.created_at))
            .limit(10)
        )
    ).scalars().all()
    base_origin = _request_public_origin(request)
    invites_payload = [_serialize_invite(inv, base_origin, now) for inv in recent_invites]

    flash_message = request.session.pop("flash_message", None)
    flash_type = request.session.pop("flash_type", None)

    return templates.TemplateResponse(
        "remote/index.html",
        {
            "request": request,
            "user": user,
            "workspace": workspace,
            "is_admin": bool(user.is_admin),
            "header_title": "Remote Client Access",
            "mc_settings": mc_settings,
            "is_configured": is_configured,
            "has_sso": has_sso,
            "has_sync_creds": has_sync_creds,
            "devices": devices,
            "devices_payload": devices_payload,
            "groups": groups,
            "mesh_groups": mesh_groups,
            "default_mesh_id": (mc_settings.default_mesh_id if mc_settings else None) or "",
            "invites_payload": invites_payload,
            "agent_platforms": AGENT_PLATFORMS,
            "companies": companies,
            "recent_logs": recent_logs,
            "stats": stats,
            "flash_message": flash_message,
            "flash_type": flash_type,
        },
    )


@router.get("/remote/audit/export.csv")
async def web_remote_audit_export(request: Request, db: AsyncSession = Depends(get_session)):
    """Admin-only CSV export of this workspace's remote session audit trail."""
    user, redirect = await _require_workspace_admin(request, db)
    if redirect:
        return redirect

    logs = (
        await db.execute(
            select(MeshCentralSessionLog)
            .where(MeshCentralSessionLog.workspace_id == user.workspace_id)
            .order_by(desc(MeshCentralSessionLog.started_at), desc(MeshCentralSessionLog.id))
        )
    ).scalars().all()

    user_ids = {log.user_id for log in logs if log.user_id}
    technicians: dict[int, User] = {}
    if user_ids:
        technicians = {
            row.id: row
            for row in (
                await db.execute(
                    select(User).where(
                        User.id.in_(user_ids),
                        User.workspace_id == user.workspace_id,
                    )
                )
            ).scalars().all()
        }

    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow([
        "Started At (UTC)",
        "Technician Username",
        "Technician Name",
        "Device Name",
        "Node ID",
        "Mode",
    ])
    for log in logs:
        tech = technicians.get(log.user_id)
        writer.writerow([
            _csv_safe_cell(log.started_at.strftime("%Y-%m-%d %H:%M:%S") if log.started_at else ""),
            _csv_safe_cell(tech.username if tech else ""),
            _csv_safe_cell((tech.shown_name or tech.full_name) if tech else ""),
            _csv_safe_cell(log.device_name),
            _csv_safe_cell(log.node_id),
            _csv_safe_cell(log.view_mode),
        ])

    return Response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="remote-session-audit.csv"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/remote/devices/json")
async def web_remote_devices_json(request: Request, db: AsyncSession = Depends(get_session)):
    """Lightweight workspace-scoped device payload for live dashboard refresh."""
    user, redirect = await _require_workspace_user(request, db)
    if redirect:
        return JSONResponse({"success": False, "error": "Authentication required"}, status_code=401)

    devices = await _workspace_device_rows(db, user.workspace_id)
    groups = _group_summary(devices)
    return JSONResponse({
        "success": True,
        "devices": _serialize_devices_for_dashboard(devices),
        "stats": _device_stats(devices, groups),
    }, headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------------------
# Embedded Live Remote Session View (Client PC inside our Dashboard)
# ---------------------------------------------------------------------------

@router.get("/remote/session/{device_id}", response_class=HTMLResponse)
async def web_remote_session(
    request: Request,
    device_id: int,
    mode: Optional[str] = Query(default=None),
    show_controls: bool = Query(default=False),
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

    wants_controls = _wants_interactive_controls(show_controls)
    embed = await build_embed_url_async(
        mc_settings,
        device.node_id,
        mode=mode,
        hide_chrome=False if wants_controls else None,
    )
    direct_embed = await build_embed_url_async(
        mc_settings, device.node_id, mode=embed["mode"], hide_chrome=False
    )
    direct_url = direct_embed["url"]

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
            "show_controls": wants_controls,
            "sibling_devices": sibling_devices,
            "modes": [
                {"key": "desktop", "label": "Desktop", "icon": "fa-desktop", "tip": "Remote Desktop (Mouse & Keyboard)"},
                {"key": "terminal", "label": "Terminal", "icon": "fa-terminal", "tip": "Command Shell / Terminal"},
                {"key": "files", "label": "Files", "icon": "fa-folder-open", "tip": "File Manager (Upload / Download)"},
                {"key": "console", "label": "Agent Console", "icon": "fa-code", "tip": "Agent Console"},
                {"key": "summary", "label": "Device Info", "icon": "fa-circle-info", "tip": "Device Info & Power"},
            ],
        },
    )


@router.get("/remote/session/{device_id}/embed-url")
async def web_remote_session_embed_url(
    request: Request,
    device_id: int,
    mode: Optional[str] = Query(default="desktop"),
    show_controls: bool = Query(default=False),
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

    wants_controls = _wants_interactive_controls(show_controls)
    embed = await build_embed_url_async(
        mc_settings,
        device.node_id,
        mode=mode,
        hide_chrome=False if wants_controls else None,
    )
    direct_embed = await build_embed_url_async(
        mc_settings, device.node_id, mode=embed["mode"], hide_chrome=False
    )
    direct_url = direct_embed["url"]

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

    # The embed URL carries a short-lived SSO token: never let caches retain it.
    return JSONResponse({
        "success": True,
        "embed_url": embed["url"],
        "direct_url": direct_url,
        "mode": embed["mode"],
        "sso_active": embed["sso_active"],
        "show_controls": bool(wants_controls),
    }, headers={
        "Cache-Control": "no-store, private",
        "X-Robots-Tag": "noindex, nofollow",
        "X-Content-Type-Options": "nosniff",
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
        # sync_workspace_devices upserts both devices and their groups in the
        # same transaction, so no extra group pass is needed here.
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


@router.post("/remote/devices/{device_id}/edit")
async def web_remote_device_edit(
    request: Request,
    device_id: int,
    name: Optional[str] = Form(None),
    client_name: Optional[str] = Form(None),
    mesh_name: Optional[str] = Form(None),
    os_desc: Optional[str] = Form(None),
    ip_address: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    node_id: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_session),
):
    """Update a client PC's details; available to every workspace technician."""
    user, redirect = await _require_workspace_user(request, db)
    if redirect:
        return JSONResponse({"success": False, "error": "Authentication required"}, status_code=401)

    device = await _get_workspace_device(db, user.workspace_id, device_id)
    if not device:
        return JSONResponse({"success": False, "error": "Device not found"}, status_code=404)

    form_name = _form_text(name)
    clean_name = form_name.strip() if form_name is not None else (device.name or "").strip()
    if not clean_name:
        return JSONResponse({"success": False, "error": "Device name is required."}, status_code=400)

    # Node ID identifies the actual MeshCentral device and is deliberately
    # immutable here: the UI shows it read-only, and letting a crafted form
    # repoint it would allow targeting a node outside this workspace.
    form_node = _form_text(node_id)
    if form_node:
        try:
            requested_node = normalize_node_id(form_node)
        except ValueError as exc:
            return JSONResponse({"success": False, "error": str(exc)}, status_code=400)
        if requested_node != device.node_id:
            return JSONResponse({
                "success": False,
                "error": "Node ID cannot be changed after a client PC is added.",
            }, status_code=400)

    device.name = clean_name
    form_client = _form_text(client_name)
    if form_client is not None:
        device.client_name = form_client.strip() or None
    form_mesh = _form_text(mesh_name)
    if form_mesh is not None:
        device.mesh_name = form_mesh.strip() or None
    form_os = _form_text(os_desc)
    if form_os is not None:
        clean_os = form_os.strip() or None
        device.os_desc = clean_os
        device.os_family = detect_os_family(clean_os) if clean_os else "other"
    form_ip = _form_text(ip_address)
    if form_ip is not None:
        device.ip_address = form_ip.strip() or None
    form_notes = _form_text(notes)
    if form_notes is not None:
        device.notes = form_notes.strip() or None
    device.updated_at = datetime.utcnow()
    await db.commit()

    return JSONResponse({
        "success": True,
        "device": {
            "id": device.id,
            "node_id": device.node_id,
            "name": device.name,
            "client_name": device.client_name or "",
            "mesh_name": device.mesh_name or "",
            "os_desc": device.os_desc or "",
            "os_family": device.os_family,
            "ip_address": device.ip_address or "",
            "notes": device.notes or "",
        },
    })


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
    user, redirect = await _require_workspace_user(request, db)
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
    if mc_settings is None:
        mc_settings = await auto_import_local_meshcentral_if_present(db, user.workspace_id)

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

    try:
        if clear_password in ("1", "true", "on"):
            row.password_encrypted = None
        elif password and password.strip():
            row.password_encrypted = encrypt_secret(password)

        if clear_login_token_key in ("1", "true", "on"):
            row.login_token_key_encrypted = None
        elif login_token_key and login_token_key.strip():
            row.login_token_key_encrypted = encrypt_secret(login_token_key)
    except RuntimeError as exc:
        return _settings_redirect(request, return_to, str(exc), ok=False)

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

    report = await diagnose_meshcentral_connection(mc_settings, timeout=10.0)
    if not report["ok"]:
        return JSONResponse({
            "success": False,
            "error": report["error"],
            "hint": report["hint"],
            "message": report["error"],
            "auth_method": report["auth_method"],
        }, status_code=502)

    # A successful probe is also a discovery pass: keep groups/defaults in sync.
    await _persist_discovered_groups(
        db, user.workspace_id, mc_settings, dict(report.get("meshes") or {})
    )
    await db.commit()
    message = (
        f"Connected to {mc_settings.server_url}: {report['devices']} device(s) "
        f"({report['online']} online) across {report['groups']} group(s)."
    )
    return JSONResponse({
        "success": True,
        "groups": report["groups"],
        "devices": report["devices"],
        "online": report["online"],
        "sso_ready": report["sso_mode"] != "none",
        "sso_mode": report["sso_mode"],
        "auth_method": report["auth_method"],
        "default_mesh_id": mc_settings.default_mesh_id or "",
        "default_mesh_name": mc_settings.default_mesh_name or "",
        "local_port": mc_settings.local_port,
        "message": message,
        "hint": report["hint"],
    })


# ---------------------------------------------------------------------------
# Auto-Configure / Autofill Settings from Web UI (No CLI Required)
# ---------------------------------------------------------------------------

def _request_public_origin(request: Request) -> str:
    """Return the browser-facing origin (e.g. https://tickets.hubconnect.co.za).

    Public share links and the copyable Linux command must not be built from an
    arbitrary Host header. Trust the request host only when it matches the
    configured public domain (or is a loopback dev address); otherwise pin the
    link to the canonical configured domain.
    """
    scheme = getattr(request.url, "scheme", "https") or "https"
    netloc = getattr(request.url, "netloc", "") or ""
    if not netloc:
        return get_settings().public_base_url.rstrip("/")

    origin = f"{scheme}://{netloc}".rstrip("/")
    hostname = (urlparse(origin).hostname or "").lower()
    if hostname in {"localhost", "127.0.0.1", "::1"}:
        return origin

    configured = (get_settings().app_domain or "").strip().rstrip("/")
    if not configured:
        return origin
    parsed_configured = urlparse(configured if "://" in configured else f"//{configured}")
    configured_host = (parsed_configured.hostname or "").lower()
    configured_netloc = parsed_configured.netloc or configured
    if configured_host and hostname == configured_host:
        return origin
    return f"{scheme}://{configured_netloc}".rstrip("/")


def _serialize_invite(
    invite: MeshCentralAgentInvite,
    base_origin: str,
    now: Optional[datetime] = None,
) -> dict:
    ref = now or datetime.utcnow()
    remaining = max(0, int((invite.expires_at - ref).total_seconds()))
    is_active = (
        not invite.revoked
        and remaining > 0
        and invite.download_count < invite.max_downloads
    )
    plat_meta = AGENT_PLATFORMS.get(invite.platform, AGENT_PLATFORMS["windows64"])
    return {
        "id": invite.id,
        "token": invite.token,
        "share_url": f"{base_origin}/web/client/remote/get/{invite.token}",
        "mesh_id": invite.mesh_id,
        "mesh_name": invite.mesh_name or "",
        "client_name": invite.client_name or "",
        "platform": invite.platform,
        "platform_label": plat_meta["short_label"],
        "install_mode": invite.install_mode,
        "download_count": invite.download_count,
        "max_downloads": invite.max_downloads,
        "revoked": bool(invite.revoked),
        "remaining_seconds": remaining,
        "is_active": is_active,
        "expires_at": invite.expires_at.strftime("%H:%M UTC"),
    }


@router.post("/remote/settings/autofill")
async def web_remote_settings_autofill(
    request: Request,
    server_url: str = Form(""),
    domain: str = Form(""),
    username: str = Form(""),
    password: str = Form(""),
    verify_ssl: Optional[str] = Form(None),
    local_install_path: str = Form(""),
    db: AsyncSession = Depends(get_session),
):
    """Auto-detect local MeshCentral config/token key AND auto-connect over WebSocket."""
    user, redirect = await _require_workspace_admin(request, db)
    if redirect:
        return JSONResponse({"success": False, "error": "Authentication required"}, status_code=401)

    row = await _get_or_none_settings(db, user.workspace_id)
    now = datetime.utcnow()
    if row is None:
        row = MeshCentralSettings(workspace_id=user.workspace_id, created_at=now)
        db.add(row)

    # 1. Local MeshCentral detection (same server) — auto-updates config.json & reads --loginTokenKey
    local_info = await autodetect_local_meshcentral(local_install_path or row.local_install_path)
    if local_info["found"] and local_info["path"]:
        row.local_install_path = local_info["path"]

    # 2. Resolve URL, domain, credentials.
    # When local cem-autolink.json is present (from setup_meshcentral.sh) and the admin
    # left form fields blank, refresh from autolink so re-running setup_meshcentral.sh
    # immediately clears any stale URL/password/key in 1 click.
    autolink = local_info.get("autolink") or {}
    raw_url = (
        (server_url or "").strip()
        or str(autolink.get("server_url") or "").strip()
        or row.server_url
        or local_info.get("suggested_url")
        or ""
    )
    if raw_url:
        try:
            row.server_url = normalize_server_url(raw_url)
        except ValueError as exc:
            return JSONResponse({"success": False, "error": str(exc)}, status_code=400)

    if domain is not None and domain.strip():
        try:
            row.domain = normalize_domain(domain)
        except ValueError as exc:
            return JSONResponse({"success": False, "error": str(exc)}, status_code=400)

    if username and username.strip():
        row.username = username.strip()
    elif autolink.get("username"):
        row.username = str(autolink["username"]).strip()

    if password and password.strip():
        row.password_encrypted = encrypt_secret(password)
    elif autolink.get("password"):
        row.password_encrypted = encrypt_secret(str(autolink["password"]))

    if verify_ssl is not None:
        row.verify_ssl = verify_ssl in ("1", "true", "on", "yes")
    elif "verify_ssl" in autolink:
        row.verify_ssl = bool(autolink["verify_ssl"])

    _remember_local_port(row, autolink)

    autofilled_key = local_info.get("login_token_key")
    if autofilled_key:
        row.login_token_key_encrypted = encrypt_secret(autofilled_key)

    derived_uid = build_login_user_id(row.username, row.domain, row.login_user_id)
    if derived_uid:
        row.login_user_id = derived_uid

    # 3. Live verification + group discovery through the hardened diagnostics helper.
    # The same report drives the inline hint shown in both Settings UIs.
    groups_map: dict[str, str] = {}
    devices_found = 0
    online_found = 0
    connected = False
    authcookie_ok = False
    connect_error: Optional[str] = None
    diagnostic_hint: Optional[str] = None
    default_group_error: Optional[str] = None

    has_creds = bool(
        row.server_url
        and (
            (row.username and row.password_encrypted)
            or (row.login_token_key_encrypted and (row.login_user_id or row.username))
        )
    )
    if has_creds:
        try:
            report = await diagnose_meshcentral_connection(row, timeout=10.0)
            connected = bool(report.get("ok"))
            groups_map = dict(report.get("meshes") or {})
            devices_found = int(report.get("devices") or 0)
            online_found = int(report.get("online") or 0)
            connect_error = report.get("error")
            diagnostic_hint = report.get("hint")
            authcookie_ok = report.get("sso_mode") == "authcookie"
            if connected:
                if not groups_map:
                    # Auto-create a default Client PCs group so technicians can
                    # download agents immediately after a fresh install.
                    try:
                        created_grp = await create_meshcentral_group(
                            row, _DEFAULT_GROUP_NAME, "Managed via CEM"
                        )
                        groups_map[created_grp["mesh_id"]] = created_grp["mesh_name"]
                    except Exception as exc:
                        default_group_error = redact_meshcentral_text(exc)
                        logger.warning(
                            "Auto-setup could not create default group for workspace %s: %s",
                            user.workspace_id,
                            default_group_error,
                        )
                if not groups_map:
                    default_group_error = default_group_error or "No device group was found or created."
                else:
                    try:
                        await _persist_discovered_groups(db, user.workspace_id, row, groups_map)
                    except Exception as exc:
                        default_group_error = redact_meshcentral_text(exc)
                        logger.warning(
                            "Auto-setup could not persist groups for workspace %s: %s",
                            user.workspace_id,
                            default_group_error,
                        )
        except Exception as exc:
            connect_error = redact_meshcentral_text(exc)
            logger.warning(
                "Auto-setup live verification failed for workspace %s: %s",
                user.workspace_id,
                connect_error,
            )

    verified = bool(connected and groups_map and not default_group_error and row.server_url)
    if verified:
        row.enabled = True
    row.updated_at = now
    await db.commit()

    has_token_key = bool(row.login_token_key_encrypted)
    sso_mode = "token_key" if has_token_key else ("authcookie" if authcookie_ok else "none")

    local_notes = list(local_info.get("notes") or [])
    config_error = next((n for n in local_notes if n.startswith("Could not edit local config.json")), None)
    token_key_error = next((n for n in local_notes if n.startswith("Could not run node --loginTokenKey")), None)
    autolink_error = next((n for n in local_notes if n.startswith("Could not read cem-autolink.json")), None)

    notes = list(local_notes)
    if default_group_error:
        notes.append(f"Device group error: {default_group_error}")
    if connected:
        notes.append(f"Connected to {row.server_url} ({devices_found} PC(s), {len(groups_map)} group(s)).")
        if has_token_key:
            notes.append("Instant SSO active using Login Token Key.")
        elif authcookie_ok:
            notes.append("Automatic SSO active via MeshCentral live auth cookie (no manual CLI key needed!).")
    elif connect_error:
        notes.append(f"Live connection note: {connect_error}")
    elif not row.server_url:
        notes.append("Enter your MeshCentral Server URL, Username, and Password, then click Auto-Setup again.")
    if diagnostic_hint and not connected:
        notes.append(diagnostic_hint)

    ok = verified
    return JSONResponse({
        "success": ok,
        "verified": verified,
        "enabled": bool(row.enabled),
        "server_url": row.server_url or "",
        "login_user_id": row.login_user_id or "",
        "has_login_token_key": has_token_key,
        "sso_ready": bool(has_token_key or authcookie_ok),
        "sso_mode": sso_mode,
        "local_found": bool(local_info["found"]),
        "config_updated": bool(local_info["config_updated"]),
        "default_mesh_id": row.default_mesh_id or "",
        "default_mesh_name": row.default_mesh_name or "",
        "default_group_error": default_group_error,
        "config_error": config_error,
        "token_key_error": token_key_error,
        "autolink_error": autolink_error,
        "local_port": row.local_port,
        "groups": [{"mesh_id": mid, "mesh_name": mname} for mid, mname in groups_map.items()],
        "devices": devices_found,
        "online": online_found,
        "message": " ".join(notes) if notes else (connect_error or "Auto-setup completed."),
        "hint": diagnostic_hint or (connect_error or ""),
    })


@router.get("/remote/settings/diagnostics")
async def web_remote_settings_diagnostics(
    request: Request,
    download: int = Query(default=0),
    db: AsyncSession = Depends(get_session),
):
    """Return a secret-redacted MeshCentral diagnostic report (admin-only)."""
    user, redirect = await _require_workspace_admin(request, db)
    if redirect:
        return JSONResponse({"success": False, "error": "Authentication required"}, status_code=401)

    mc_settings = await _get_or_none_settings(db, user.workspace_id)
    report = await build_meshcentral_diagnostic_report(db, user.workspace_id, mc_settings)

    if download:
        return Response(
            content=report["report_text"].encode("utf-8"),
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": 'attachment; filename="meshcentral-diagnostics.txt"',
                "Cache-Control": "no-store",
            },
        )
    return JSONResponse(report, headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------------------
# Client Installer Downloads & Expiring Share Links (5-15 min)
# ---------------------------------------------------------------------------

async def _resolve_mesh_group(
    db: AsyncSession,
    workspace_id: int,
    mc_settings: MeshCentralSettings,
    requested_mesh_id: Optional[str],
) -> tuple[str, str]:
    """Resolve a valid MeshCentral group ID + name from request, DB, or live server."""
    if requested_mesh_id and requested_mesh_id.strip():
        mid = requested_mesh_id.strip()
        normalize_mesh_id(mid)
        dev = (
            await db.execute(
                select(MeshCentralDevice)
                .where(
                    MeshCentralDevice.workspace_id == workspace_id,
                    MeshCentralDevice.mesh_id == mid,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        cached = (
            await db.execute(
                select(MeshCentralGroup).where(
                    MeshCentralGroup.workspace_id == workspace_id,
                    MeshCentralGroup.mesh_id == mid,
                )
            )
        ).scalars().first()
        mname = (dev.mesh_name if dev and dev.mesh_name else None) or (
            cached.name if cached else None
        ) or (
            mc_settings.default_mesh_name if mc_settings.default_mesh_id == mid else None
        ) or "Client PCs"
        if not dev and not cached and mc_settings.default_mesh_id != mid:
            # Never accept an arbitrary ID learned from another tenant's link.
            raise ValueError("Unknown MeshCentral group for this workspace")
        return mid, mname

    if mc_settings.default_mesh_id:
        return mc_settings.default_mesh_id, mc_settings.default_mesh_name or "Client PCs"

    cached_default = (
        await db.execute(
            select(MeshCentralGroup).where(
                MeshCentralGroup.workspace_id == workspace_id,
                MeshCentralGroup.is_default.is_(True),
            )
        )
    ).scalars().first()
    if cached_default and cached_default.mesh_id:
        return cached_default.mesh_id, cached_default.name or "Client PCs"

    dev = (
        await db.execute(
            select(MeshCentralDevice)
            .where(
                MeshCentralDevice.workspace_id == workspace_id,
                MeshCentralDevice.mesh_id.is_not(None),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if dev and dev.mesh_id:
        return dev.mesh_id, dev.mesh_name or dev.client_name or "Client PCs"

    # Last resort: query MeshCentral live for groups (or auto-create "Client PCs")
    meshes, _ = await fetch_meshcentral_inventory(mc_settings, timeout=10.0)
    if not meshes:
        created = await create_meshcentral_group(mc_settings, "Client PCs", "Managed via CEM")
        mc_settings.default_mesh_id = created["mesh_id"]
        mc_settings.default_mesh_name = created["mesh_name"]
        await db.commit()
        return created["mesh_id"], created["mesh_name"]
    first_id, first_name = next(iter(meshes.items()))
    mc_settings.default_mesh_id = first_id
    mc_settings.default_mesh_name = first_name
    await db.commit()
    return first_id, first_name


@router.get("/remote/groups")
async def web_remote_groups(request: Request, db: AsyncSession = Depends(get_session)):
    """Return available MeshCentral device groups for installer & invite creation."""
    user, redirect = await _require_workspace_user(request, db)
    if redirect:
        return JSONResponse({"success": False, "error": "Authentication required"}, status_code=401)

    mc_settings = await _get_or_none_settings(db, user.workspace_id)
    live_warning: Optional[str] = None
    groups: dict[str, str] = {}
    if mc_settings and mc_settings.default_mesh_id:
        groups[mc_settings.default_mesh_id] = mc_settings.default_mesh_name or _DEFAULT_GROUP_NAME

    cached_groups = (
        await db.execute(
            select(MeshCentralGroup).where(MeshCentralGroup.workspace_id == user.workspace_id)
        )
    ).scalars().all()
    for group in cached_groups:
        if group.mesh_id and group.mesh_id not in groups:
            groups[group.mesh_id] = group.name or group.mesh_id

    devs = (
        await db.execute(
            select(MeshCentralDevice).where(MeshCentralDevice.workspace_id == user.workspace_id)
        )
    ).scalars().all()
    for d in devs:
        if d.mesh_id and d.mesh_id not in groups:
            groups[d.mesh_id] = d.mesh_name or d.client_name or d.mesh_id

    if not mc_settings or not mc_settings.server_url:
        live_warning = (
            "MeshCentral is not configured yet — saved groups are shown. Ask a workspace "
            "administrator to run Auto-Setup in MeshCentral Setup."
        )
    else:
        try:
            live_meshes, _ = await fetch_meshcentral_inventory(mc_settings, timeout=8.0)
            groups.update(live_meshes)
            if live_meshes:
                await upsert_workspace_groups(db, user.workspace_id, live_meshes)
                await db.commit()
        except Exception as exc:
            _kind, hint = classify_meshcentral_error(exc, mc_settings)
            live_warning = (
                "MeshCentral is not reachable right now — showing saved groups only. "
                f"{hint}"
            )
            logger.warning("MeshCentral group lookup failed for workspace %s: %s", user.workspace_id, exc)

    return JSONResponse({
        "success": True,
        "groups": [{"mesh_id": mid, "mesh_name": mname} for mid, mname in groups.items()],
        "live_warning": live_warning,
    })


@router.post("/remote/groups/create")
async def web_remote_groups_create(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    db: AsyncSession = Depends(get_session),
):
    """Create a new MeshCentral device group directly from the Remote Support dashboard."""
    user, redirect = await _require_workspace_user(request, db)
    if redirect:
        return JSONResponse({"success": False, "error": "Authentication required"}, status_code=401)

    clean_name = (name or "").strip()
    if not clean_name:
        return JSONResponse({
            "success": False,
            "error": "Group name is required.",
            "hint": "Enter a name such as 'Client PCs' and try again.",
        }, status_code=400)

    mc_settings = await _get_or_none_settings(db, user.workspace_id)
    if not mc_settings or not mc_settings.server_url:
        return JSONResponse({
            "success": False,
            "error": "MeshCentral is not configured yet.",
            "hint": "Ask a workspace administrator to run Auto-Setup in MeshCentral Setup, then retry.",
        }, status_code=400)

    try:
        created = await create_meshcentral_group(mc_settings, clean_name, description=description)
        await _persist_discovered_groups(
            db, user.workspace_id, mc_settings, {created["mesh_id"]: created["mesh_name"]}
        )
        await db.commit()
        return JSONResponse({"success": True, **created})
    except Exception as exc:
        _kind, hint = classify_meshcentral_error(exc, mc_settings)
        error = redact_meshcentral_text(exc)
        logger.warning("MeshCentral group creation failed for workspace %s: %s", user.workspace_id, error)
        # Return the raw diagnosis and the hint as separate fields; templates
        # append the hint themselves, so embedding it in `error` duplicates it.
        return JSONResponse({
            "success": False,
            "error": error,
            "hint": hint,
        }, status_code=502)


@router.get("/remote/agent/download")
async def web_remote_agent_download(
    request: Request,
    mesh_id: Optional[str] = Query(default=None),
    platform: str = Query(default="windows64"),
    install_mode: str = Query(default="install"),
    db: AsyncSession = Depends(get_session),
):
    """Direct technician download of the pre-configured MeshAgent installer via CEM."""
    user, redirect = await _require_workspace_user(request, db)
    if redirect:
        return redirect

    mc_settings = await _get_or_none_settings(db, user.workspace_id)
    if not mc_settings or not mc_settings.enabled or not mc_settings.server_url:
        raise HTTPException(status_code=400, detail="MeshCentral is not configured and enabled.")
    if platform not in AGENT_PLATFORMS:
        raise HTTPException(status_code=400, detail="Unsupported platform.")
    mode = "connect" if install_mode == "connect" else "install"

    workspace = (
        await db.execute(select(Workspace).where(Workspace.id == user.workspace_id))
    ).scalar_one_or_none()
    brand_name = (workspace.site_title or workspace.name) if workspace else "RemoteSupport"

    try:
        resolved_mesh_id, _ = await _resolve_mesh_group(db, user.workspace_id, mc_settings, mesh_id)
        payload, filename, content_type = await proxy_agent_installer(
            mc_settings,
            resolved_mesh_id,
            platform=platform,
            install_mode=mode,
            brand_name=brand_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.warning("Agent installer proxy failed for workspace %s: %s", user.workspace_id, exc)
        raise HTTPException(status_code=502, detail=f"Could not fetch client installer: {exc}")

    return Response(
        content=payload,
        media_type=content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/remote/invites")
async def web_remote_invite_create(
    request: Request,
    mesh_id: str = Form(""),
    mesh_name: str = Form(""),
    client_name: str = Form(""),
    platform: str = Form("windows64"),
    install_mode: str = Form("install"),
    expires_minutes: int = Form(15),
    db: AsyncSession = Depends(get_session),
):
    """Create a 5-15 minute expiring client download link hosted on this website."""
    user, redirect = await _require_workspace_user(request, db)
    if redirect:
        return JSONResponse({"success": False, "error": "Authentication required"}, status_code=401)

    mc_settings = await _get_or_none_settings(db, user.workspace_id)
    if not mc_settings or not mc_settings.enabled or not mc_settings.server_url:
        return JSONResponse(
            {"success": False, "error": "Configure and enable MeshCentral in Settings first."},
            status_code=400,
        )

    if platform not in AGENT_PLATFORMS:
        return JSONResponse({"success": False, "error": "Unsupported platform."}, status_code=400)
    mode = "connect" if install_mode == "connect" else "install"
    ttl_minutes = max(1, min(30, int(expires_minutes or 10)))

    try:
        resolved_id, resolved_name = await _resolve_mesh_group(db, user.workspace_id, mc_settings, mesh_id)
    except Exception as exc:
        return JSONResponse(
            {"success": False, "error": f"Select or create a Device Group first ({exc})."},
            status_code=400,
        )

    now = datetime.utcnow()
    invite = MeshCentralAgentInvite(
        workspace_id=user.workspace_id,
        token=secrets.token_urlsafe(24),
        mesh_id=resolved_id,
        mesh_name=(mesh_name or "").strip() or resolved_name,
        client_name=(client_name or "").strip() or None,
        platform=platform,
        install_mode=mode,
        created_by_id=user.id,
        created_at=now,
        expires_at=now + timedelta(minutes=ttl_minutes),
        download_count=0,
        max_downloads=5,
        revoked=False,
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)

    base_origin = _request_public_origin(request)
    return JSONResponse({
        "success": True,
        "invite": _serialize_invite(invite, base_origin, now),
    })


@router.post("/remote/invites/{invite_id}/revoke")
async def web_remote_invite_revoke(
    request: Request,
    invite_id: int,
    db: AsyncSession = Depends(get_session),
):
    user, redirect = await _require_workspace_user(request, db)
    if redirect:
        return JSONResponse({"success": False, "error": "Authentication required"}, status_code=401)

    invite = (
        await db.execute(
            select(MeshCentralAgentInvite).where(
                MeshCentralAgentInvite.id == invite_id,
                MeshCentralAgentInvite.workspace_id == user.workspace_id,
            )
        )
    ).scalar_one_or_none()
    if not invite:
        return JSONResponse({"success": False, "error": "Link not found"}, status_code=404)

    invite.revoked = True
    await db.commit()
    return JSONResponse({"success": True, "id": invite.id})


def _invite_is_active(invite: MeshCentralAgentInvite, now: datetime) -> bool:
    return not invite.revoked and invite.expires_at > now


def _check_invite_validity(invite: Optional[MeshCentralAgentInvite], now: datetime) -> tuple[bool, int, str]:
    if not invite:
        return False, 0, "This support link is invalid or no longer exists."
    if invite.revoked:
        return False, 0, "This support link was revoked by your technician."
    remaining = int((invite.expires_at - now).total_seconds())
    if remaining <= 0:
        return False, 0, "This download link has expired. For security, links are only valid for a few minutes."
    if invite.download_count >= invite.max_downloads:
        return False, 0, "This link has reached its maximum number of downloads."
    return True, remaining, ""


async def _reserve_invite_download(db: AsyncSession, invite: MeshCentralAgentInvite, now: datetime) -> None:
    """Claim one download slot atomically, before fetching the installer."""
    result = await db.execute(
        update(MeshCentralAgentInvite)
        .where(
            MeshCentralAgentInvite.id == invite.id,
            MeshCentralAgentInvite.revoked.is_(False),
            MeshCentralAgentInvite.expires_at > now,
            MeshCentralAgentInvite.download_count < MeshCentralAgentInvite.max_downloads,
        )
        .values(download_count=MeshCentralAgentInvite.download_count + 1)
    )
    if result.rowcount != 1:
        raise HTTPException(
            status_code=410,
            detail="This link has expired or reached its maximum number of downloads.",
            headers=PUBLIC_LINK_HEADERS,
        )
    await db.commit()


async def _release_invite_download(db: AsyncSession, invite_id: int) -> None:
    """Return a reserved slot when the installer cannot be streamed."""
    await db.execute(
        update(MeshCentralAgentInvite)
        .where(
            MeshCentralAgentInvite.id == invite_id,
            MeshCentralAgentInvite.download_count > 0,
        )
        .values(download_count=MeshCentralAgentInvite.download_count - 1)
    )
    await db.commit()


@router.get("/client/remote/get/{token}", response_class=HTMLResponse)
@router.get("/remote/get/{token}", response_class=HTMLResponse)
async def web_remote_public_invite_page(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_session),
):
    """Public branded client download page hosted on this website (expires automatically)."""
    invite = (
        await db.execute(
            select(MeshCentralAgentInvite).where(MeshCentralAgentInvite.token == token)
        )
    ).scalar_one_or_none()
    workspace = None
    if invite:
        workspace = (
            await db.execute(select(Workspace).where(Workspace.id == invite.workspace_id))
        ).scalar_one_or_none()

    now = datetime.utcnow()
    is_valid, remaining, reason = _check_invite_validity(invite, now)
    platform_meta = AGENT_PLATFORMS.get(
        invite.platform if invite else "windows64",
        AGENT_PLATFORMS["windows64"],
    )
    site_title = (workspace.site_title or workspace.name) if workspace else "Remote Support"
    primary_color = (workspace.primary_color if workspace and workspace.primary_color else "#dc2626")

    platform = invite.platform if invite else "windows64"
    # The one-line command targets THIS public host only. No MeshCentral URL,
    # credentials or internal paths are ever rendered on the public page.
    public_origin = _request_public_origin(request)
    linux_install_command = (
        f"curl -fsSL {public_origin}/web/client/remote/get/{invite.token}/download | sudo bash"
        if is_valid and invite and platform == "linux" else ""
    )

    status_code = 200 if is_valid else 410
    return templates.TemplateResponse(
        "remote/client_download.html",
        {
            "request": request,
            "workspace": workspace,
            "site_title": site_title,
            "primary_color": primary_color,
            "invite": invite,
            "is_valid": is_valid,
            "remaining_seconds": remaining,
            "expired_reason": reason,
            "platform_meta": platform_meta,
            "platform": platform,
            "linux_install_command": linux_install_command,
        },
        status_code=status_code,
        headers=PUBLIC_LINK_HEADERS,
    )


@router.get("/client/remote/get/{token}/download")
@router.get("/remote/get/{token}/download")
async def web_remote_public_invite_download(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_session),
):
    """Stream the client installer directly through this website while the token is unexpired."""
    invite = (
        await db.execute(
            select(MeshCentralAgentInvite).where(MeshCentralAgentInvite.token == token)
        )
    ).scalar_one_or_none()

    now = datetime.utcnow()
    is_valid, _, reason = _check_invite_validity(invite, now)
    if not is_valid or not invite:
        raise HTTPException(status_code=410, detail=reason or "Link expired.", headers=PUBLIC_LINK_HEADERS)

    mc_settings = await _get_or_none_settings(db, invite.workspace_id)
    if not mc_settings or not mc_settings.enabled or not mc_settings.server_url:
        raise HTTPException(
            status_code=503,
            detail="Remote support service is currently unavailable.",
            headers=PUBLIC_LINK_HEADERS,
        )

    workspace = (
        await db.execute(select(Workspace).where(Workspace.id == invite.workspace_id))
    ).scalar_one_or_none()
    brand_name = (workspace.site_title or workspace.name) if workspace else "RemoteSupport"

    await _reserve_invite_download(db, invite, now)
    try:
        payload, filename, content_type = await proxy_agent_installer(
            mc_settings,
            invite.mesh_id,
            platform=invite.platform,
            install_mode=invite.install_mode,
            brand_name=brand_name,
        )
        # A technician may revoke the link (or it may expire) while the upstream
        # installer is being fetched. Never stream a payload after that happens.
        current = (await db.execute(
            select(MeshCentralAgentInvite).where(MeshCentralAgentInvite.id == invite.id)
        )).scalar_one_or_none()
        if current is not None:
            await db.refresh(current)  # bypass the identity map: revocation may be external
        if not current or not _invite_is_active(current, datetime.utcnow()):
            await _release_invite_download(db, invite.id)
            raise HTTPException(
                status_code=410,
                detail="This support link is no longer valid.",
                headers=PUBLIC_LINK_HEADERS,
            )
    except HTTPException:
        raise
    except Exception as exc:
        await _release_invite_download(db, invite.id)
        logger.warning("Public invite download failed for token %s: %s", token[:8], exc)
        raise HTTPException(
            status_code=502,
            detail="Could not retrieve installer from remote server.",
            headers=PUBLIC_LINK_HEADERS,
        )

    return Response(
        content=payload,
        media_type=content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            **PUBLIC_LINK_HEADERS,
        },
    )
