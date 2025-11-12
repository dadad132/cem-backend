from __future__ import annotations

from typing import Optional
from datetime import datetime
import random

from sqlmodel import Field, Relationship, SQLModel
from .enums import MeetingPlatform

# Distinct colors for user calendar display
USER_COLORS = [
    "#3B82F6",  # Blue
    "#EF4444",  # Red
    "#10B981",  # Green
    "#F59E0B",  # Amber
    "#8B5CF6",  # Violet
    "#EC4899",  # Pink
    "#14B8A6",  # Teal
    "#F97316",  # Orange
    "#6366F1",  # Indigo
    "#84CC16",  # Lime
    "#06B6D4",  # Cyan
    "#F43F5E",  # Rose
    "#A855F7",  # Purple
    "#22C55E",  # Green
    "#FACC15",  # Yellow
]

def get_random_user_color() -> str:
    """Return a random color from the predefined user colors list."""
    return random.choice(USER_COLORS)


class UserBase(SQLModel):
    username: str = Field(index=True, unique=True)
    email: Optional[str] = Field(default=None, index=True)
    full_name: Optional[str] = None
    is_active: bool = True
    is_admin: bool = False
    preferred_meeting_platform: Optional[MeetingPlatform] = None
    profile_completed: bool = False
    profile_picture: Optional[str] = None  # Path to profile picture file
    calendar_color: Optional[str] = Field(default_factory=get_random_user_color)  # Random color for calendar display


class User(UserBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    hashed_password: str
    workspace_id: Optional[int] = Field(default=None, foreign_key="workspace.id")
    email_verified: bool = False
    verification_code: Optional[str] = None
    verification_expires_at: Optional[datetime] = None
    # Google OAuth fields
    google_id: Optional[str] = Field(default=None, index=True)
    google_access_token: Optional[str] = None
    google_refresh_token: Optional[str] = None
    google_token_expiry: Optional[datetime] = None


class UserCreate(SQLModel):
    username: str
    password: str
    full_name: Optional[str] = None
    email: Optional[str] = None
    preferred_meeting_platform: Optional[MeetingPlatform] = None


class UserRead(UserBase):
    id: int


class UserUpdate(SQLModel):
    username: Optional[str] = None
    email: Optional[str] = None
    full_name: Optional[str] = None
    is_active: Optional[bool] = None
    is_admin: Optional[bool] = None
    preferred_meeting_platform: Optional[MeetingPlatform] = None
    profile_completed: Optional[bool] = None
    profile_picture: Optional[str] = None
    calendar_color: Optional[str] = None
