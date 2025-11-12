from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class Notification(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    type: str = Field(default="assignment")  # assignment, meeting, task, comment, message, etc.
    message: str
    url: Optional[str] = None
    related_id: Optional[int] = None  # ID of related task/meeting/project/message
    created_at: datetime = Field(default_factory=datetime.utcnow)
    read_at: Optional[datetime] = None
    dismissed_at: Optional[datetime] = None  # For popup auto-dismiss tracking
