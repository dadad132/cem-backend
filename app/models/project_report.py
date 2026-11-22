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


class WorkspaceWeeklyReport(SQLModel, table=True):
    """A workspace-wide weekly report that went out: one per company per week.

    Unlike ProjectWeeklyReport this is not keyed to a project: the recipients,
    projects and Excel attachments all come from that company's EmailSettings.
    """
    __table_args__ = (UniqueConstraint('workspace_id', 'week_start'),)

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.id", ondelete="CASCADE", index=True)
    week_start: date  # The Monday of the week reported on
    sent_to: str
    sent_at: datetime = Field(default_factory=datetime.utcnow)
