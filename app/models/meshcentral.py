from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class MeshCentralSettings(SQLModel, table=True):
    """Per-workspace MeshCentral server connection and SSO embedding settings."""

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.id", unique=True, index=True)

    enabled: bool = Field(default=False)
    server_url: Optional[str] = Field(default=None)
    domain: Optional[str] = Field(default="")

    # Service account credentials for WebSocket inventory sync (control.ashx)
    username: Optional[str] = Field(default=None)
    password_encrypted: Optional[str] = Field(default=None)

    # Server loginTokenKey (from `node node_modules/meshcentral --loginTokenKey`)
    # used to mint short-lived AES-256-GCM SSO tokens for embedded client logins.
    login_token_key_encrypted: Optional[str] = Field(default=None)
    login_user_id: Optional[str] = Field(default=None)

    # Embedded viewer preferences
    default_view_mode: int = Field(default=11)  # 11=Desktop, 12=Terminal, 13=Files, 10=Summary
    hide_mesh_chrome: bool = Field(default=True)
    verify_ssl: bool = Field(default=True)

    # Default MeshCentral device group for 1-click client installer downloads & links
    default_mesh_id: Optional[str] = Field(default=None)
    default_mesh_name: Optional[str] = Field(default=None)
    local_install_path: Optional[str] = Field(default=None)
    # Local MeshCentral listener used for server-side loopback calls. The public
    # server_url is still used for browser iframes/agent download links.
    local_port: Optional[int] = Field(default=None)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class MeshCentralGroup(SQLModel, table=True):
    """A MeshCentral device group (mesh) cached per workspace."""

    __table_args__ = (
        UniqueConstraint("workspace_id", "mesh_id", name="uq_meshcentralgroup_ws_mesh"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.id", index=True)
    mesh_id: str = Field(index=True)
    name: str = Field(index=True)
    description: Optional[str] = Field(default=None)
    is_default: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class MeshCentralDevice(SQLModel, table=True):
    """Managed client PC synced from MeshCentral or registered manually for a workspace."""

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.id", index=True)

    node_id: str = Field(index=True)
    mesh_id: Optional[str] = Field(default=None, index=True)
    mesh_name: Optional[str] = Field(default=None)

    name: str = Field(index=True)
    hostname: Optional[str] = Field(default=None)
    client_name: Optional[str] = Field(default=None, index=True)
    company_id: Optional[int] = Field(default=None, foreign_key="company.id", index=True)

    os_desc: Optional[str] = Field(default=None)
    os_family: str = Field(default="other")  # windows | linux | macos | other
    ip_address: Optional[str] = Field(default=None)
    power_state: int = Field(default=0)
    is_online: bool = Field(default=False, index=True)

    notes: Optional[str] = Field(default=None)
    is_pinned: bool = Field(default=False, index=True)

    last_connected_at: Optional[datetime] = Field(default=None)
    last_connected_by_id: Optional[int] = Field(default=None, foreign_key="user.id")
    last_synced_at: Optional[datetime] = Field(default=None)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class MeshCentralSessionLog(SQLModel, table=True):
    """Audit trail of remote support sessions launched from the dashboard."""

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.id", index=True)
    device_id: Optional[int] = Field(default=None, foreign_key="meshcentraldevice.id", index=True)
    node_id: str
    device_name: str
    user_id: int = Field(foreign_key="user.id", index=True)
    view_mode: str = Field(default="desktop")
    started_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class MeshCentralAgentInvite(SQLModel, table=True):
    """Time-limited client download link (5-15 min) proxied through this website."""

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.id", index=True)

    token: str = Field(unique=True, index=True)
    mesh_id: str = Field(index=True)
    mesh_name: Optional[str] = Field(default=None)
    client_name: Optional[str] = Field(default=None)

    platform: str = Field(default="windows64")  # windows64 | windows32 | macos | linux
    install_mode: str = Field(default="install")  # install | connect

    created_by_id: int = Field(foreign_key="user.id", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    expires_at: datetime = Field(index=True)

    download_count: int = Field(default=0)
    max_downloads: int = Field(default=5)
    revoked: bool = Field(default=False, index=True)
