from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class Subtask(SQLModel, table=True):
    """
    Subtask model for breaking down tasks into smaller actionable items.
    Each subtask belongs to a parent task and can be marked as complete.
    """
    __tablename__ = "subtask"

    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: int = Field(foreign_key="task.id", index=True)
    title: str
    is_completed: bool = Field(default=False)
    completed_at: Optional[datetime] = None
    order: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    def __repr__(self):
        return f"<Subtask(id={self.id}, task_id={self.task_id}, title='{self.title}', completed={self.is_completed})>"
