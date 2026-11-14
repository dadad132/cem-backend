from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class ProjectWeeklyReport(SQLModel, table=True):
    """A weekly project report that went out: one per project per week, so a
    restart on a Monday morning does not send it twice."""
    __table_args__ = (UniqueConstraint('project_id', 'week_start'),)

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", ondelete="CASCADE", index=True)
    week_start: date  # The Monday of the week reported on
    sent_to: str
    sent_at: datetime = Field(default_factory=datetime.utcnow)
