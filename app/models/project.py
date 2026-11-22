from __future__ import annotations

from datetime import datetime, date
from typing import Optional

from sqlmodel import Field, SQLModel


class ProjectBase(SQLModel):
    name: str
    description: Optional[str] = None
    support_email: Optional[str] = None  # Email address for auto-creating tickets
    # IMAP settings for project-specific email processing
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
    owner_id: int = Field(foreign_key="user.id", index=True)
    workspace_id: Optional[int] = Field(default=None, foreign_key="workspace.id", index=True)

    # Mandatory quotation / paperwork supplied when the project is created.
    # Only the generated storage name is a path; the original name is display
    # text and is always cleaned before it is kept or shown to a user.
    quotation_filename: Optional[str] = Field(default=None, max_length=255)
    quotation_original_name: Optional[str] = Field(default=None, max_length=255)
    quotation_content_type: Optional[str] = Field(default=None, max_length=100)
    quotation_size: Optional[int] = Field(default=None)
    quotation_uploaded_at: Optional[datetime] = Field(default=None)


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
    # Archiving is only allowed through the dedicated, admin-only routes so
    # it always sets archived_at and writes an activity entry.
    start_date: Optional[date] = None
    due_date: Optional[date] = None
