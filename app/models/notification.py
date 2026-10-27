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
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    read_at: Optional[datetime] = Field(default=None, index=True)
    dismissed_at: Optional[datetime] = None  # For popup auto-dismiss tracking


def wants_ticket_notification(user, assigned_to_id: Optional[int]) -> bool:
    """Whether to tell this user about something that happened on a ticket.

    "Mute ticket notifications" (on the profile) stops the ones about other
    people's tickets; a ticket assigned to the user still notifies them.
    """
    return not getattr(user, 'mute_ticket_notifications', False) or user.id == assigned_to_id
