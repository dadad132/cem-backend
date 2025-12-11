"""
WebRTC Call Models for peer-to-peer voice/video calls
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from enum import Enum

from sqlmodel import Field, SQLModel, Relationship


class CallStatus(str, Enum):
    """Status of a call"""
    RINGING = "ringing"
    ACTIVE = "active"
    ENDED = "ended"
    MISSED = "missed"
    DECLINED = "declined"
    BUSY = "busy"


class CallType(str, Enum):
    """Type of call"""
    VOICE = "voice"
    VIDEO = "video"


class Call(SQLModel, table=True):
    """A voice or video call between users"""
    __tablename__ = "call"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    
    # Caller and recipient
    caller_id: int = Field(foreign_key="user.id", index=True)
    recipient_id: int = Field(foreign_key="user.id", index=True)
    workspace_id: int = Field(foreign_key="workspace.id", index=True)
    
    # Call details
    call_type: CallType = Field(default=CallType.VOICE)
    status: CallStatus = Field(default=CallStatus.RINGING)
    
    # WebRTC signaling data (stored temporarily during call setup)
    offer_sdp: Optional[str] = Field(default=None)  # Caller's SDP offer
    answer_sdp: Optional[str] = Field(default=None)  # Recipient's SDP answer
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    answered_at: Optional[datetime] = Field(default=None)
    ended_at: Optional[datetime] = Field(default=None)
    
    # Duration in seconds (calculated when call ends)
    duration_seconds: Optional[int] = Field(default=None)
    
    # End reason
    end_reason: Optional[str] = Field(default=None)


class CallIceCandidate(SQLModel, table=True):
    """ICE candidates for WebRTC connection"""
    __tablename__ = "call_ice_candidate"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    call_id: int = Field(foreign_key="call.id", index=True)
    
    # Who sent this candidate
    from_user_id: int = Field(foreign_key="user.id")
    
    # ICE candidate data (JSON string)
    candidate_data: str
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
