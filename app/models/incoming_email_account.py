"""
Incoming Email Account model
Allows multiple email accounts per workspace for ticket creation
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class IncomingEmailAccount(SQLModel, table=True):
    """Multiple incoming email accounts per workspace for ticket processing"""
    __tablename__ = "incoming_email_account"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.id", index=True)
    
    # Account identification
    name: str = Field(default="Support Email")  # Friendly name like "Sales Support", "Tech Support"
    email_address: str  # The email address this account represents
    
    # Link to project - tickets from this email go into this project
    project_id: Optional[int] = Field(default=None, foreign_key="project.id", index=True)
    
    # Mail Server Settings (IMAP or POP3)
    protocol: str = Field(default="imap")  # 'imap' or 'pop3'
    imap_host: str
    imap_port: int = Field(default=993)
    imap_username: str
    imap_password: str  # Store encrypted in production
    imap_use_ssl: bool = Field(default=True)
    
    # Processing settings
    is_active: bool = Field(default=True)
    auto_assign_to_user_id: Optional[int] = Field(default=None, foreign_key="user.id")  # Auto-assign tickets to this user
    default_priority: str = Field(default="medium")  # Default priority for new tickets
    default_category: str = Field(default="support")  # Default category
    
    # Highest IMAP UID already seen, per folder, as JSON:
    #   {"INBOX": {"uidvalidity": 12, "last_uid": 9481}, ...}
    # Lets a poll ask only for messages newer than that, instead of rescanning
    # a rolling window every couple of minutes. UIDVALIDITY is stored with it
    # because a mailbox can renumber, which invalidates the mark.
    imap_uid_state: Optional[str] = Field(default=None)

    # Timestamps
    last_checked_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class IncomingEmailAccountCreate(SQLModel):
    """Schema for creating an incoming email account"""
    name: str
    email_address: str
    project_id: Optional[int] = None
    protocol: str = "imap"  # 'imap' or 'pop3'
    imap_host: str
    imap_port: int = 993
    imap_username: str
    imap_password: str
    imap_use_ssl: bool = True
    auto_assign_to_user_id: Optional[int] = None
    default_priority: str = "medium"
    default_category: str = "support"


class IncomingEmailAccountUpdate(SQLModel):
    """Schema for updating an incoming email account"""
    name: Optional[str] = None
    email_address: Optional[str] = None
    project_id: Optional[int] = None
    protocol: Optional[str] = None  # 'imap' or 'pop3'
    imap_host: Optional[str] = None
    imap_port: Optional[int] = None
    imap_username: Optional[str] = None
    imap_password: Optional[str] = None
    imap_use_ssl: Optional[bool] = None
    is_active: Optional[bool] = None
    auto_assign_to_user_id: Optional[int] = None
    default_priority: Optional[str] = None
    default_category: Optional[str] = None
