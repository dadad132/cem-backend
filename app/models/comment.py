from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel, Column
from sqlalchemy import Integer, String, DateTime, ForeignKey


class CommentBase(SQLModel):
    content: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Comment(CommentBase, table=True):
    __tablename__ = "comment"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: int = Field(foreign_key="task.id", index=True)
    author_id: int = Field(foreign_key="user.id", index=True)


class CommentCreate(SQLModel):
    content: str
    task_id: int


class CommentRead(CommentBase):
    id: int
    task_id: int

