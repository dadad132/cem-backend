from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, Relationship, SQLModel


class Workspace(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    # Branding settings
    site_title: Optional[str] = Field(default=None)
    logo_url: Optional[str] = Field(default=None)
    favicon_url: Optional[str] = Field(default=None)
    primary_color: Optional[str] = Field(default="#2563eb")  # Default blue
    
    # Relationships are defined from User/Project side to avoid SQLAlchemy 2.0
    # typing issues with generic list annotations in this minimal setup.
