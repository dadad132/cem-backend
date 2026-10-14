from __future__ import annotations

from datetime import datetime, date
from typing import Optional

from sqlmodel import Field, SQLModel


# ============ GOALS & MILESTONES ============

class Goal(SQLModel, table=True):
    """Workspace or project goals with progress tracking"""
    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.id", index=True)
    project_id: Optional[int] = Field(default=None, foreign_key="project.id", index=True)  # Optional: link to project
    title: str
    description: Optional[str] = None
    target_date: Optional[date] = None
    progress: int = Field(default=0)  # 0-100 percentage
    status: str = Field(default="on_track")  # on_track, at_risk, behind, completed
    owner_id: int = Field(foreign_key="user.id")
    parent_goal_id: Optional[int] = Field(default=None, foreign_key="goal.id")  # For sub-goals
    is_archived: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Milestone(SQLModel, table=True):
    """Key milestones within a goal or project"""
    id: Optional[int] = Field(default=None, primary_key=True)
    goal_id: Optional[int] = Field(default=None, foreign_key="goal.id", index=True)
    project_id: Optional[int] = Field(default=None, foreign_key="project.id", index=True)
    title: str
    description: Optional[str] = None
    due_date: Optional[date] = None
    completed_date: Optional[date] = None
    is_completed: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class TaskTemplate(SQLModel, table=True):
    """Reusable task templates"""
    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.id", index=True)
    name: str
    title_template: str  # Template for task title with placeholders
    description_template: Optional[str] = None
    priority: str = Field(default="medium")  # low, medium, high, critical
    estimated_hours: Optional[float] = None
    default_tags: Optional[str] = None  # Comma-separated tags
    subtasks_json: Optional[str] = None  # JSON array of subtask titles
    created_by_id: int = Field(foreign_key="user.id")
    is_shared: bool = Field(default=True)  # Available to all workspace members
    use_count: int = Field(default=0)  # Track popularity
    created_at: datetime = Field(default_factory=datetime.utcnow)


class FocusTask(SQLModel, table=True):
    """Daily focus/priority tasks for users"""
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    task_id: int = Field(foreign_key="task.id", index=True)
    focus_date: date = Field(index=True)  # The day this is a focus
    order: int = Field(default=0)  # Sort order for the day
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============ EXISTING MODELS ============

class TaskDependency(SQLModel, table=True):
    """Tracks task dependencies - which tasks block other tasks"""
    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: int = Field(foreign_key="task.id", index=True)  # The task that is blocked
    depends_on_task_id: int = Field(foreign_key="task.id", index=True)  # The task it depends on
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by_id: Optional[int] = Field(default=None, foreign_key="user.id")


class TaskWatcher(SQLModel, table=True):
    """Users watching a task for updates"""
    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: int = Field(foreign_key="task.id", index=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class TicketWatcher(SQLModel, table=True):
    """Users watching a ticket for updates"""
    id: Optional[int] = Field(default=None, primary_key=True)
    ticket_id: int = Field(foreign_key="ticket.id", index=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class RecurringTask(SQLModel, table=True):
    """Template for recurring tasks"""
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    title: str
    description: Optional[str] = None
    status: str = "todo"  # Uses TaskStatus values: todo, in_progress, done, blocked
    priority: str = "medium"  # Uses TaskPriority values
    recurrence_type: str  # daily, weekly, monthly, yearly
    recurrence_value: Optional[str] = None  # e.g., "monday,wednesday" for weekly, "15" for monthly
    recurrence_interval: int = Field(default=1)  # Every X days/weeks/months
    start_date: datetime
    end_date: Optional[datetime] = None  # When to stop creating instances
    last_created_at: Optional[datetime] = None  # When the last instance was created
    next_due_date: Optional[datetime] = None  # When the next instance should be created
    created_by_id: int = Field(foreign_key="user.id")
    assign_to_id: Optional[int] = Field(default=None, foreign_key="user.id")  # Default assignee
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class RecurringTaskInstance(SQLModel, table=True):
    """Tracks created instances from recurring tasks"""
    id: Optional[int] = Field(default=None, primary_key=True)
    recurring_task_id: int = Field(foreign_key="recurringtask.id", index=True)
    task_id: int = Field(foreign_key="task.id", index=True)
    due_date: datetime
    created_at: datetime = Field(default_factory=datetime.utcnow)


class TaskAttachment(SQLModel, table=True):
    """File attachments on tasks"""
    # TODO: FUTURE FEATURE - Attachment Labels/Categories
    # Add a 'label' or 'category' field to categorize attachments:
    # Examples: "Purchase Order", "PO", "Invoice", "Quote", "Contract", "Receipt", "Specification", "Drawing", "Report"
    # Could be:
    #   - label: Optional[str] = None  # Free-text label
    #   - category: Optional[str] = None  # Predefined categories from a list
    #   - tags: Optional[str] = None  # JSON array of multiple tags
    # UI would need a dropdown or input field when uploading attachments
    # Also consider: description field for additional context about the attachment
    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: int = Field(foreign_key="task.id", index=True)
    filename: str
    file_path: str  # Path to stored file
    file_size: int  # Size in bytes
    file_type: Optional[str] = None  # MIME type
    uploaded_by: int = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class TimeLog(SQLModel, table=True):
    """Track time spent on tasks"""
    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: int = Field(foreign_key="task.id", index=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    hours: float
    description: Optional[str] = None
    logged_at: datetime = Field(default_factory=datetime.utcnow)


class TaskTimeEntry(SQLModel, table=True):
    """One stretch of work on a task, from Start/Resume until Pause.

    The entry that is still running has no ended_at. See app/core/work_timer.py.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: int = Field(foreign_key="task.id", ondelete="CASCADE", index=True)
    user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)  # Who started it
    started_at: datetime = Field(default_factory=datetime.utcnow)
    ended_at: Optional[datetime] = None


class ActivityLog(SQLModel, table=True):
    """Centralized activity feed for entire workspace"""
    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.id", index=True)
    user_id: int = Field(foreign_key="user.id", index=True)  # Who performed the action
    action_type: str  # created, updated, commented, assigned, completed, etc.
    entity_type: str  # task, project, meeting, chat, etc.
    entity_id: int  # ID of the affected entity
    details: Optional[str] = None  # JSON string with action details
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class CustomField(SQLModel, table=True):
    """Custom fields for projects/tasks"""
    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.id")
    name: str
    field_type: str  # text, number, select, multiselect, date, checkbox, url
    options: Optional[str] = None  # JSON array for select/multiselect options
    applies_to: str  # task, project, contact, lead, deal
    created_at: datetime = Field(default_factory=datetime.utcnow)


class CustomFieldValue(SQLModel, table=True):
    """Values for custom fields"""
    id: Optional[int] = Field(default=None, primary_key=True)
    custom_field_id: int = Field(foreign_key="customfield.id")
    entity_type: str  # task, project, contact, etc.
    entity_id: int  # ID of the task/project/etc
    value: str  # Stored as string, parsed based on field_type
    created_at: datetime = Field(default_factory=datetime.utcnow)


class SavedView(SQLModel, table=True):
    """Save custom filter/sort configurations"""
    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.id")
    user_id: int = Field(foreign_key="user.id")  # Owner of the view
    name: str
    view_type: str  # list, board, calendar, table, gantt
    filters: str  # JSON string of filter configuration
    sort_by: Optional[str] = None
    is_shared: bool = False  # Share with workspace or keep private
    created_at: datetime = Field(default_factory=datetime.utcnow)
