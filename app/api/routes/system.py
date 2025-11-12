"""
System API endpoints for version and update management
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Dict, Any

from app.api.deps import get_current_user_id, get_db
from app.core.updates import check_for_updates, get_current_version
from app.core.version import VERSION, BUILD_DATE
from app.models.user import User

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/version")
async def get_version():
    """
    Get current application version information.
    Public endpoint - no authentication required.
    """
    return {
        "version": VERSION,
        "build_date": BUILD_DATE,
        "status": "running"
    }


@router.get("/updates/check")
async def check_updates(
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """
    Check for available updates.
    Admin only endpoint.
    """
    # Verify user is admin
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    update_info = await check_for_updates()
    return update_info


@router.get("/health")
async def health_check():
    """
    Health check endpoint for monitoring.
    """
    return {
        "status": "healthy",
        "version": VERSION,
        "timestamp": "2025-11-01T00:00:00Z"
    }
