from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class APIKey(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.id", index=True)
    name: str                                      # human label, e.g. "Zapier Integration"
    key_prefix: str = Field(index=True)            # first 12 chars shown in UI, e.g. "cem_a1b2c3d4"
    key_hash: str = Field(unique=True, index=True) # SHA-256 of the full key
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_used_at: Optional[datetime] = Field(default=None)

    # Optional restrictions. A null/blank scopes value means a legacy key with
    # full workspace access, exactly as before these columns existed.
    scopes: Optional[str] = Field(default=None)               # comma-separated scopes
    allowed_company: Optional[str] = Field(default=None)      # client company name(s)
    allowed_email: Optional[str] = Field(default=None)        # client email address(es)
    allowed_email_domain: Optional[str] = Field(default=None) # email domain(s), @ optional
    allow_internal_comments: bool = Field(default=False)
    webhook_url: Optional[str] = Field(default=None)
    webhook_secret: Optional[str] = Field(default=None)
    webhook_events: Optional[str] = Field(default=None)       # comma-separated event names
