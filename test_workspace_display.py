"""
Test to verify workspace is being loaded with branding fields
"""
import asyncio
from app.core.database import get_session
from app.models.workspace import Workspace
from app.models.user import User
from sqlmodel import select

async def test_workspace_loading():
    async for db in get_session():
        # Get first user
        user = (await db.execute(select(User).limit(1))).scalar_one_or_none()
        if not user:
            print("No users found")
            return
        
        print(f"User: {user.username}, workspace_id: {user.workspace_id}")
        
        # Get workspace
        workspace = (await db.execute(select(Workspace).where(Workspace.id == user.workspace_id))).scalar_one_or_none()
        if not workspace:
            print("No workspace found")
            return
        
        print(f"\nWorkspace loaded:")
        print(f"  ID: {workspace.id}")
        print(f"  Name: {workspace.name}")
        print(f"  site_title: {getattr(workspace, 'site_title', 'ATTRIBUTE NOT FOUND')}")
        print(f"  logo_url: {getattr(workspace, 'logo_url', 'ATTRIBUTE NOT FOUND')}")
        print(f"  primary_color: {getattr(workspace, 'primary_color', 'ATTRIBUTE NOT FOUND')}")
        
        # Test the template logic
        display_name = workspace.site_title if workspace and workspace.site_title else (workspace.name if workspace else 'CRM')
        print(f"\nDisplay name would be: '{display_name}'")
        
        break

if __name__ == "__main__":
    asyncio.run(test_workspace_loading())
