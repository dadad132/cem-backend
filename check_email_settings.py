import asyncio
from app.core.database import get_session
from app.models.user import User
from app.models.workspace import Workspace
from app.models import EmailSettings
from sqlmodel import select

async def check():
    async for db in get_session():
        result = await db.execute(select(User).where(User.id == 1))
        user = result.scalar_one_or_none()
        if user:
            print(f'User workspace_id: {user.workspace_id}')
            ws = (await db.execute(select(Workspace).where(Workspace.id == user.workspace_id))).scalar_one_or_none()
            if ws:
                print(f'Workspace ID: {ws.id}, Name: {ws.name}')
                email = (await db.execute(select(EmailSettings).where(EmailSettings.workspace_id == ws.id))).scalar_one_or_none()
                if email:
                    print(f'Email settings found:')
                    print(f'  Incoming: {email.incoming_mail_host}:{email.incoming_mail_port}')
                    print(f'  Type: {email.incoming_mail_type}')
                    print(f'  Username: {email.incoming_mail_username}')
                    print(f'  Auto-reply enabled: {email.auto_reply_enabled}')
                else:
                    print('No email settings configured for this workspace')
        break

asyncio.run(check())
