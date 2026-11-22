"""Helpers for the audit records written when a project is deleted or completed.

Both helpers only build rows; the caller adds them to its own session so the
audit is committed in the same transaction as the project change.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from app.models.project import Project
from app.models.project_audit import MAX_DELETION_REASON, ProjectDeletionAudit
from app.models.task_extensions import ActivityLog
from app.models.user import User


def clean_deletion_reason(value: object) -> str:
    """Trim a submitted reason and cap its size; empty means 'not provided'.

    Only real text counts: form fields submitted as file parts become UploadFile
    objects, which must not be mistaken for a typed reason.
    """
    if not isinstance(value, str):
        return ''
    return value.strip()[:MAX_DELETION_REASON]


def build_deletion_audit(
    *, project: Project, user: User, reason: str
) -> ProjectDeletionAudit:
    return ProjectDeletionAudit(
        project_id=project.id,
        project_name=project.name,
        deleted_by_user_id=user.id,
        workspace_id=project.workspace_id,
        deletion_reason=reason,
        deleted_at=datetime.utcnow(),
    )


def build_project_activity(
    *, project: Project, user: User, action_type: str, details: Optional[str] = None
) -> ActivityLog:
    return ActivityLog(
        workspace_id=project.workspace_id,
        user_id=user.id,
        action_type=action_type,
        entity_type='project',
        entity_id=project.id,
        details=details,
    )
