from __future__ import annotations

from datetime import datetime, date, time
from typing import Optional

from sqlmodel import Field, Relationship, SQLModel

from .enums import TaskPriority, TaskStatus


class TaskBase(SQLModel):
    title: str
    description: Optional[str] = None
    status: TaskStatus = TaskStatus.todo
    priority: TaskPriority = TaskPriority.medium
    # Scheduling
    start_date: Optional[date] = None
    start_time: Optional[time] = None
    due_date: Optional[date] = None
    due_time: Optional[time] = None
    # Working days (comma-separated: "0,1,2,3,4" for Mon-Fri, where Mon=0, Sun=6)
    working_days: Optional[str] = Field(default="0,1,2,3,4")
    # Time tracking
    estimated_hours: Optional[float] = None
    time_spent_hours: Optional[float] = None
    # Archive status - when task is marked done and locked
    is_archived: bool = Field(default=False)
    archived_at: Optional[datetime] = None
    # Tags for flexible categorization
    tags: Optional[str] = None  # Comma-separated tags
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Task(TaskBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id")
    creator_id: int = Field(foreign_key="user.id")
    # Subtask support - parent task relationship
    parent_task_id: Optional[int] = Field(default=None, foreign_key="task.id")


class TaskCreate(SQLModel):
    title: str
    description: Optional[str] = None
    status: Optional[TaskStatus] = None
    priority: Optional[TaskPriority] = None
    start_date: Optional[date] = None
    start_time: Optional[time] = None
    due_date: Optional[date] = None
    due_time: Optional[time] = None
    project_id: int


class TaskRead(TaskBase):
    id: int
    project_id: int


class TaskUpdate(SQLModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[TaskStatus] = None
    priority: Optional[TaskPriority] = None
    start_date: Optional[date] = None
    start_time: Optional[time] = None
    due_date: Optional[date] = None
    due_time: Optional[time] = None
