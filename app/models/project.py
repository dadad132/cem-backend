from __future__ import annotations

from datetime import datetime, date
from typing import Optional

from sqlmodel import Field, Relationship, SQLModel


class ProjectBase(SQLModel):
    name: str
    description: Optional[str] = None
    support_email: Optional[str] = None  # Email address for auto-creating tasks
    # Per-project IMAP settings
    imap_host: Optional[str] = None
    imap_port: Optional[int] = None
    imap_username: Optional[str] = None
    imap_password: Optional[str] = None
    imap_use_ssl: bool = True
    is_archived: bool = False
    archived_at: Optional[datetime] = None
    start_date: Optional[date] = None  # Project start date for calendar display
    due_date: Optional[date] = None  # Project due date for calendar display
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Project(ProjectBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    owner_id: int = Field(foreign_key="user.id")
    workspace_id: Optional[int] = Field(default=None, foreign_key="workspace.id")


class ProjectCreate(SQLModel):
    name: str
    description: Optional[str] = None
    start_date: Optional[date] = None
    due_date: Optional[date] = None


class ProjectRead(ProjectBase):
    id: int


class ProjectUpdate(SQLModel):
    name: Optional[str] = None
    description: Optional[str] = None
    support_email: Optional[str] = None
    imap_host: Optional[str] = None
    imap_port: Optional[int] = None
    imap_username: Optional[str] = None
    imap_password: Optional[str] = None
    imap_use_ssl: Optional[bool] = None
    is_archived: Optional[bool] = None
    start_date: Optional[date] = None
    due_date: Optional[date] = None
