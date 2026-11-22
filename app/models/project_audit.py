from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

MAX_DELETION_REASON = 2000


class ProjectDeletionAudit(SQLModel, table=True):
    """Why a project was deleted.

    Kept separate from the project row (and deliberately without a foreign key
    to it) so the reason survives the deletion itself. Workspace-scoped so it
    is included in per-company backups and never crosses tenant boundaries.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(index=True)
    project_name: str
    deleted_by_user_id: int = Field(index=True)
    workspace_id: int = Field(foreign_key="workspace.id", index=True)
    deletion_reason: str = Field(max_length=MAX_DELETION_REASON)
    deleted_at: datetime = Field(default_factory=datetime.utcnow, index=True)
