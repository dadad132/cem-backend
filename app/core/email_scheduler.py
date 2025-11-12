"""
Background Email Checker
Automatically checks for new emails and creates tickets
"""

import asyncio
from datetime import datetime
from sqlmodel.ext.asyncio.session import AsyncSession
from app.core.database import async_engine
from app.core.email_ticket import process_emails_to_tickets


class EmailTicketScheduler:
    """Background scheduler for email-to-ticket processing"""
    
    def __init__(self, check_interval: int = 300):
        """
        Initialize scheduler
        
        Args:
            check_interval: Seconds between email checks (default: 5 minutes)
        """
        self.check_interval = check_interval
        self.running = False
        self.task = None
    
    async def check_emails_task(self, workspace_id: int, config: dict):
        """Background task to check emails periodically"""
        
        print(f"[Email-to-Ticket] Scheduler started (checking every {self.check_interval}s)")
        
        while self.running:
            try:
                # Create database session
                async with AsyncSession(async_engine) as db:
                    # Process emails
                    tickets = await process_emails_to_tickets(db, workspace_id, config)
                    
                    if tickets:
                        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Created {len(tickets)} tickets from emails")
                
                # Wait for next check
                await asyncio.sleep(self.check_interval)
                
            except Exception as e:
                print(f"[Email-to-Ticket] Error in background task: {e}")
                # Continue running even if there's an error
                await asyncio.sleep(self.check_interval)
    
    def start(self, workspace_id: int, config: dict):
        """Start the email checking background task"""
        
        if self.running:
            print("[Email-to-Ticket] Scheduler already running")
            return
        
        self.running = True
        self.task = asyncio.create_task(self.check_emails_task(workspace_id, config))
        print("[Email-to-Ticket] Scheduler started successfully")
    
    def stop(self):
        """Stop the email checking background task"""
        
        if not self.running:
            return
        
        self.running = False
        if self.task:
            self.task.cancel()
        print("[Email-to-Ticket] Scheduler stopped")


# Global scheduler instance
email_scheduler = EmailTicketScheduler()


def start_email_scheduler(workspace_id: int = 1):
    """
    Start the email-to-ticket scheduler
    Call this from main.py on startup
    """
    
    try:
        from app.core.secrets_local import (
            SUPPORT_EMAIL_ADDRESS,
            SUPPORT_EMAIL_PASSWORD,
            SUPPORT_EMAIL_IMAP_SERVER,
            SUPPORT_EMAIL_DEFAULT_ASSIGNED_TO,
            EMAIL_CHECK_INTERVAL
        )
        
        config = {
            'imap_server': SUPPORT_EMAIL_IMAP_SERVER,
            'email_address': SUPPORT_EMAIL_ADDRESS,
            'email_password': SUPPORT_EMAIL_PASSWORD,
            'default_assigned_to': SUPPORT_EMAIL_DEFAULT_ASSIGNED_TO
        }
        
        # Create scheduler with custom interval
        scheduler = EmailTicketScheduler(check_interval=EMAIL_CHECK_INTERVAL)
        scheduler.start(workspace_id, config)
        
        return scheduler
        
    except ImportError:
        print("[Email-to-Ticket] Not configured - skipping scheduler")
        print("[Email-to-Ticket] Add email settings to secrets_local.py to enable")
        return None
    except Exception as e:
        print(f"[Email-to-Ticket] Failed to start scheduler: {e}")
        return None


def stop_email_scheduler():
    """Stop the email-to-ticket scheduler"""
    email_scheduler.stop()
