"""
Email-to-Ticket Scheduler V2
Uses database settings for each workspace
"""

import asyncio
from datetime import datetime
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from app.core.database import engine
from app.core.email_to_ticket_v2 import process_workspace_emails, process_project_emails
from app.models.workspace import Workspace
from app.models.email_settings import EmailSettings
from app.models.project import Project


class EmailScheduler:
    """Background scheduler for email-to-ticket processing"""
    
    def __init__(self, check_interval: int = 300):
        """
        Initialize scheduler
        
        Args:
            check_interval: Seconds between checks (default: 5 minutes)
        """
        self.check_interval = check_interval
        self.running = False
        self.task = None
    
    async def check_emails_task(self):
        """Background task to check emails periodically"""
        
        print(f"[Email-to-Ticket] Scheduler started (checking every {self.check_interval}s)")
        
        while self.running:
            try:
                # Get list of workspaces to process
                workspace_ids = []
                
                async with AsyncSession(engine) as db:
                    # Get all workspaces with email settings
                    result = await db.execute(
                        select(EmailSettings.workspace_id).where(EmailSettings.incoming_mail_host.isnot(None))
                    )
                    workspace_ids = [row[0] for row in result.all()]
                
                # Process workspaces sequentially (each gets its own session)
                for ws_id in workspace_ids:
                    await self._process_workspace(ws_id)
                
                # Process projects with their own IMAP settings
                await self._process_project_emails()
                
                # Wait for next check
                await asyncio.sleep(self.check_interval)
                
            except Exception as e:
                print(f"[Email-to-Ticket] Error in background task: {e}")
                await asyncio.sleep(self.check_interval)
    
    async def _process_workspace(self, workspace_id: int):
        """Process emails for a single workspace with its own session"""
        try:
            async with AsyncSession(engine) as db:
                tickets = await process_workspace_emails(db, workspace_id)
                
                if tickets:
                    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    print(f"[{timestamp}] Workspace {workspace_id}: Created {len(tickets)} ticket(s) from emails")
        
        except Exception as e:
            print(f"[Email-to-Ticket] Error processing workspace {workspace_id}: {e}")
    
    async def _process_project_emails(self):
        """Process emails for all projects with their own IMAP settings"""
        try:
            async with AsyncSession(engine) as db:
                # Find all projects with IMAP configured
                result = await db.execute(
                    select(Project).where(
                        Project.imap_host.isnot(None),
                        Project.imap_username.isnot(None),
                        Project.is_archived == False
                    )
                )
                projects = result.scalars().all()
                
                for project in projects:
                    try:
                        tasks = await process_project_emails(db, project)
                        if tasks:
                            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            print(f"[{timestamp}] Project '{project.name}': Created {len(tasks)} task(s) from emails")
                    except Exception as e:
                        print(f"[Email-to-Ticket] Error processing project '{project.name}': {e}")
        
        except Exception as e:
            print(f"[Email-to-Ticket] Error processing project emails: {e}")
    
    async def start(self):
        """Start the scheduler"""
        if self.running:
            print("[Email-to-Ticket] Scheduler already running")
            return
        
        self.running = True
        self.task = asyncio.create_task(self.check_emails_task())
        print("[Email-to-Ticket] Scheduler started successfully")
    
    async def stop(self):
        """Stop the scheduler"""
        if not self.running:
            return
        
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        
        print("[Email-to-Ticket] Scheduler stopped")


# Global scheduler instance
from app.core.config import get_settings
settings = get_settings()
email_scheduler = EmailScheduler(check_interval=settings.email_check_interval)


async def start_email_scheduler():
    """Start the email-to-ticket scheduler"""
    try:
        await email_scheduler.start()
    except Exception as e:
        print(f"[Email-to-Ticket] Failed to start scheduler: {e}")


async def stop_email_scheduler():
    """Stop the email-to-ticket scheduler"""
    await email_scheduler.stop()
