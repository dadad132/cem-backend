"""
Email-to-Ticket Scheduler V2
Uses database settings for each workspace AND per-project IMAP settings
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
                async with AsyncSession(engine) as db:
                    # Get all workspaces with email settings
                    result = await db.execute(
                        select(EmailSettings).where(EmailSettings.incoming_mail_host.isnot(None))
                    )
                    email_settings_list = result.scalars().all()
                    
                    # Process all workspaces in parallel for faster response
                    tasks = []
                    for settings in email_settings_list:
                        tasks.append(self._process_workspace(db, settings.workspace_id))
                    
                    # Also process projects with their own IMAP settings
                    project_result = await db.execute(
                        select(Project).where(Project.imap_host.isnot(None))
                    )
                    projects = project_result.scalars().all()
                    
                    for project in projects:
                        tasks.append(self._process_project(db, project))
                    
                    # Wait for all workspaces/projects to complete processing
                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)
                
                # Wait for next check
                await asyncio.sleep(self.check_interval)
                
            except Exception as e:
                print(f"[Email-to-Ticket] Error in background task: {e}")
                await asyncio.sleep(self.check_interval)
    
    async def _process_workspace(self, db: AsyncSession, workspace_id: int):
        """Process emails for a single workspace"""
        try:
            tickets = await process_workspace_emails(db, workspace_id)
            
            if tickets:
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                print(f"[{timestamp}] Workspace {workspace_id}: Created {len(tickets)} ticket(s) from emails")
        
        except Exception as e:
            print(f"[Email-to-Ticket] Error processing workspace {workspace_id}: {e}")
    
    async def _process_project(self, db: AsyncSession, project: Project):
        """Process emails for a project with its own IMAP settings"""
        try:
            tasks_created = await process_project_emails(db, project)
            
            if tasks_created:
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                print(f"[{timestamp}] Project '{project.name}': Created {len(tasks_created)} task(s) from emails")
        
        except Exception as e:
            print(f"[Email-to-Ticket] Error processing project {project.id} ({project.name}): {e}")
    
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
