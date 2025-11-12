from __future__ import annotations

from typing import Optional
from datetime import datetime, date

from sqlmodel import Field, SQLModel
from enum import Enum


class LeadStatus(str, Enum):
    new = "new"
    contacted = "contacted"
    qualified = "qualified"
    unqualified = "unqualified"
    converted = "converted"


class LeadSource(str, Enum):
    website = "website"
    referral = "referral"
    social_media = "social_media"
    cold_call = "cold_call"
    email_campaign = "email_campaign"
    trade_show = "trade_show"
    other = "other"


class Lead(SQLModel, table=True):
    """Sales lead in the CRM"""
    id: Optional[int] = Field(default=None, primary_key=True)
    first_name: str
    last_name: str
    email: Optional[str] = Field(default=None, index=True)
    phone: Optional[str] = None
    company_name: Optional[str] = None
    job_title: Optional[str] = None
    status: LeadStatus = Field(default=LeadStatus.new)
    source: Optional[LeadSource] = None
    estimated_value: Optional[float] = None
    notes: Optional[str] = None
    assigned_to: Optional[int] = Field(default=None, foreign_key="user.id")
    converted_to_contact_id: Optional[int] = Field(default=None, foreign_key="contact.id")
    converted_at: Optional[datetime] = None
    workspace_id: int = Field(foreign_key="workspace.id")
    created_by: int = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
