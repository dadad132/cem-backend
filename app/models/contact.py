from __future__ import annotations

from typing import Optional
from datetime import datetime

from sqlmodel import Field, SQLModel


class Contact(SQLModel, table=True):
    """Contact person in the CRM"""
    id: Optional[int] = Field(default=None, primary_key=True)
    first_name: str
    last_name: str
    email: Optional[str] = Field(default=None, index=True)
    phone: Optional[str] = None
    mobile: Optional[str] = None
    job_title: Optional[str] = None
    company_id: Optional[int] = Field(default=None, foreign_key="company.id")
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    postal_code: Optional[str] = None
    notes: Optional[str] = None
    linkedin_url: Optional[str] = None
    twitter_handle: Optional[str] = None
    workspace_id: int = Field(foreign_key="workspace.id")
    created_by: int = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
