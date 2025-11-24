"""
Email-to-Ticket Scheduler V2
Uses database settings for each workspace
"""

import asyncio
from datetime import datetime
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from app.core.database import engine
from app.core.email_to_ticket_v2 import process_workspace_emails
from app.models.workspace import Workspace
from app.models.email_settings import EmailSettings


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
                    
                    for settings in email_settings_list:
                        try:
                            tickets = await process_workspace_emails(db, settings.workspace_id)
                            
                            if tickets:
                                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                                print(f"[{timestamp}] Workspace {settings.workspace_id}: Created {len(tickets)} ticket(s) from emails")
                        
                        except Exception as e:
                            print(f"[Email-to-Ticket] Error processing workspace {settings.workspace_id}: {e}")
                            continue
                
                # Wait for next check
                await asyncio.sleep(self.check_interval)
                
            except Exception as e:
                print(f"[Email-to-Ticket] Error in background task: {e}")
                await asyncio.sleep(self.check_interval)
    
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
