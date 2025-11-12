"""
Email Settings model for admin configuration
Stores SMTP settings and email templates
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class EmailSettings(SQLModel, table=True):
    """Admin-configurable email settings for ticket confirmations"""
    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.id", unique=True)  # One config per workspace
    
    # SMTP Settings (Outgoing Mail)
    smtp_host: str = Field(default="smtp.gmail.com")
    smtp_port: int = Field(default=587)
    smtp_username: str
    smtp_password: str  # Store encrypted in production
    smtp_from_email: str
    smtp_from_name: str = Field(default="Support Team")
    smtp_use_tls: bool = Field(default=True)
    
    # Incoming Mail Settings (POP3/IMAP)
    incoming_mail_type: Optional[str] = Field(default="POP3")  # POP3 or IMAP
    incoming_mail_host: Optional[str] = Field(default=None)
    incoming_mail_port: Optional[int] = Field(default=110)  # 110 for POP3, 993 for IMAP
    incoming_mail_username: Optional[str] = Field(default=None)
    incoming_mail_password: Optional[str] = Field(default=None)
    incoming_mail_use_ssl: bool = Field(default=False)
    webmail_url: Optional[str] = Field(default=None)
    
    # Email Template Settings
    confirmation_subject: str = Field(default="Ticket Confirmation - #{ticket_number}")
    confirmation_body: str = Field(default="""Dear {guest_name} {guest_surname},

Thank you for contacting us. Your support ticket has been successfully created.

Ticket Details:
--------------
Ticket Number: {ticket_number}
Subject: {subject}
Status: Open
Priority: {priority}

Our team will review your request and someone will assist you as soon as possible.

You can reference your ticket number {ticket_number} in any future communication.

Best regards,
{company_name} Support Team

---
This is an automated message. Please do not reply to this email.""")
    
    # Additional Settings
    company_name: str = Field(default="Support Team")
    auto_reply_enabled: bool = Field(default=True)
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
