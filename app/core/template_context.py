"""
Template context processors - add global variables to all templates
"""
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.workspace import Workspace
from app.models.user import User


async def get_workspace_branding(db: AsyncSession, user_id: int = None):
    """Get workspace branding settings for templates"""
    try:
        if not user_id:
            return {
                'site_title': 'Kyotech Project Tracker',
                'logo_url': None,
                'primary_color': '#2563eb'
            }
        
        # Get user's workspace
        user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if not user:
            return {
                'site_title': 'Kyotech Project Tracker',
                'logo_url': None,
                'primary_color': '#2563eb'
            }
        
        # Get workspace with branding
        workspace = (await db.execute(
            select(Workspace).where(Workspace.id == user.workspace_id)
        )).scalar_one_or_none()
        
        if not workspace:
            return {
                'site_title': 'Kyotech Project Tracker',
                'logo_url': None,
                'primary_color': '#2563eb'
            }
        
        return {
            'site_title': workspace.site_title or workspace.name or 'Kyotech Project Tracker',
            'logo_url': workspace.logo_url if hasattr(workspace, 'logo_url') else None,
            'primary_color': workspace.primary_color if hasattr(workspace, 'primary_color') else '#2563eb'
        }
    except Exception as e:
        # If there's any error (like missing columns), return defaults
        return {
            'site_title': 'Kyotech Project Tracker',
            'logo_url': None,
            'primary_color': '#2563eb'
        }
