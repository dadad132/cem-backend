from __future__ import annotations

from typing import Optional
from datetime import datetime, date

from sqlmodel import Field, SQLModel
from enum import Enum


class DealStage(str, Enum):
    prospecting = "prospecting"
    qualification = "qualification"
    proposal = "proposal"
    negotiation = "negotiation"
    closed_won = "closed_won"
    closed_lost = "closed_lost"


class Deal(SQLModel, table=True):
    """Sales deal/opportunity in the CRM"""
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    value: float
    stage: DealStage = Field(default=DealStage.prospecting)
    probability: int = Field(default=0)  # 0-100%
    expected_close_date: Optional[date] = None
    contact_id: Optional[int] = Field(default=None, foreign_key="contact.id")
    company_id: Optional[int] = Field(default=None, foreign_key="company.id")
    description: Optional[str] = None
    notes: Optional[str] = None
    assigned_to: int = Field(foreign_key="user.id")
    closed_at: Optional[datetime] = None
    workspace_id: int = Field(foreign_key="workspace.id")
    created_by: int = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
