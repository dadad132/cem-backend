from __future__ import annotations

from typing import Optional
from datetime import datetime

from sqlmodel import Field, Relationship, SQLModel
from .enums import MeetingPlatform


class UserBase(SQLModel):
    username: str = Field(index=True, unique=True)
    email: Optional[str] = Field(default=None, index=True)
    full_name: Optional[str] = None
    is_active: bool = True
    is_admin: bool = False
    preferred_meeting_platform: Optional[MeetingPlatform] = None
    profile_completed: bool = False
    profile_picture: Optional[str] = None  # Path to profile picture file
    calendar_color: Optional[str] = Field(default="#3B82F6")  # Default blue color for calendar display


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
