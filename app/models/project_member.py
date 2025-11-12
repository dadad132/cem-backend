from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class ProjectMember(SQLModel, table=True):
    """Association table for many-to-many relationship between Projects and Users.
    
    This allows:
    - Admins to assign specific users to specific projects
    - Users to only see and work on projects they're assigned to
    - A user can be assigned to multiple projects
    - A project can have multiple users assigned to it
    """
    __tablename__ = "project_member"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    assigned_at: datetime = Field(default_factory=datetime.utcnow)
    assigned_by: Optional[int] = Field(default=None, foreign_key="user.id")  # Which admin assigned this user


class ProjectMemberCreate(SQLModel):
    project_id: int
    user_id: int


class ProjectMemberRead(SQLModel):
    id: int
    project_id: int
    user_id: int
    assigned_at: datetime
