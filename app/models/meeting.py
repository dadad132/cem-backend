from __future__ import annotations

from datetime import datetime, date, time
from typing import Optional

from sqlmodel import Field, SQLModel

from .enums import MeetingPlatform


class Meeting(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.id", index=True)
    title: str
    description: Optional[str] = None
    date: date = Field(index=True)
    start_time: time
    duration_minutes: int = 30
    platform: MeetingPlatform
    url: Optional[str] = None
    organizer_id: int = Field(foreign_key="user.id", index=True)
    is_cancelled: bool = Field(default=False)
    cancelled_at: Optional[datetime] = None
    cancelled_by: Optional[int] = Field(default=None, foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class MeetingAttendee(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    meeting_id: int = Field(foreign_key="meeting.id", index=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    status: Optional[str] = Field(default=None, description="invited|accepted|declined")
    invited_at: datetime = Field(default_factory=datetime.utcnow)
