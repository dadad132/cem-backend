from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel, Relationship


class CommentAttachment(SQLModel, table=True):
    """File attachment for a comment"""
    __tablename__ = "comment_attachment"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    comment_id: int = Field(foreign_key="comment.id", index=True)
    filename: str  # Original filename
    file_path: str  # Path where file is stored on disk
    file_size: int  # Size in bytes
    content_type: str  # MIME type
    uploaded_by_id: int = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
