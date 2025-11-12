from __future__ import annotations

from typing import Optional
from datetime import datetime

from sqlmodel import Field, SQLModel


class Company(SQLModel, table=True):
    """Company/Organization in the CRM"""
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    industry: Optional[str] = None
    website: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    postal_code: Optional[str] = None
    notes: Optional[str] = None
    workspace_id: int = Field(foreign_key="workspace.id")
    created_by: int = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
