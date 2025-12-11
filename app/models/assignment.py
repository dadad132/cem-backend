from __future__ import annotations

from typing import Optional

from sqlmodel import Field, Relationship, SQLModel


class Assignment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: int = Field(foreign_key="task.id", index=True)
    assignee_id: int = Field(foreign_key="user.id", index=True)
