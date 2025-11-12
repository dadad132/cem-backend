from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class TaskHistory(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: int = Field(foreign_key="task.id", index=True)
    editor_id: int = Field(foreign_key="user.id")
    field: str = Field(index=True)  # e.g., "title", "description", "status", "priority", "due_date"
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
