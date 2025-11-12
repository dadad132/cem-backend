"""
Ticket model for support/help desk system
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class TicketBase(SQLModel):
    ticket_number: str = Field(index=True, unique=True)  # e.g., "TKT-2024-00001"
    subject: str
    description: Optional[str] = None
    priority: str = Field(default="medium")  # low, medium, high, urgent
    status: str = Field(default="open")  # open, in_progress, waiting, resolved, closed
    category: str = Field(default="general")  # support, bug, feature, billing, general
    assigned_to_id: Optional[int] = Field(default=None, foreign_key="user.id")
    created_by_id: Optional[int] = Field(default=None, foreign_key="user.id")  # Optional for guest tickets
    workspace_id: int = Field(foreign_key="workspace.id")
    
    # Guest submission fields (for clients without accounts)
    is_guest: bool = Field(default=False)
    guest_name: Optional[str] = None
    guest_surname: Optional[str] = None
    guest_email: Optional[str] = None
    guest_phone: Optional[str] = None
    guest_company: Optional[str] = None
    guest_office_number: Optional[str] = None
    guest_branch: Optional[str] = None
    
    # Related to project/task (optional)
    related_project_id: Optional[int] = Field(default=None, foreign_key="project.id")
    related_task_id: Optional[int] = Field(default=None, foreign_key="task.id")
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    resolved_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    
    # Archive support
    is_archived: bool = False
    archived_at: Optional[datetime] = None


class Ticket(TicketBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)


class TicketComment(SQLModel, table=True):
    """Comments on tickets"""
    id: Optional[int] = Field(default=None, primary_key=True)
    ticket_id: int = Field(foreign_key="ticket.id", ondelete="CASCADE")
    user_id: Optional[int] = Field(default=None, foreign_key="user.id")  # Optional for guest email comments
    content: str
    is_internal: bool = False  # Internal notes vs public comments
    created_at: datetime = Field(default_factory=datetime.utcnow)


class TicketAttachment(SQLModel, table=True):
    """File attachments for tickets"""
    id: Optional[int] = Field(default=None, primary_key=True)
    ticket_id: int = Field(foreign_key="ticket.id", ondelete="CASCADE")
    filename: str
    file_path: str
    file_size: int  # in bytes
    mime_type: Optional[str] = None
    uploaded_by_id: int = Field(foreign_key="user.id")
    uploaded_at: datetime = Field(default_factory=datetime.utcnow)


class TicketHistory(SQLModel, table=True):
    """Track all changes to tickets"""
    id: Optional[int] = Field(default=None, primary_key=True)
    ticket_id: int = Field(foreign_key="ticket.id", ondelete="CASCADE")
    user_id: Optional[int] = Field(default=None, foreign_key="user.id")  # Nullable for guest actions
    action: str  # created, status_changed, priority_changed, assigned, commented, closed, etc.
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
