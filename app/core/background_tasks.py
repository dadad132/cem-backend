"""
Background Task Scheduler
Runs periodic tasks like email-to-ticket checking
"""
import asyncio
from datetime import datetime
from app.core.config import get_settings
from app.core.database import get_session


async def email_to_ticket_task():
    """Periodic task to check support inbox"""
    from app.core.email_to_ticket import check_support_inbox
    
    settings = get_settings()
    
    if not settings.email_to_ticket_enabled:
        print("[background-tasks] Email-to-ticket is disabled")
        return
    
    print(f"[background-tasks] Starting email-to-ticket checker (interval: {settings.email_to_ticket_interval}s)")
    
    while True:
        try:
            # Get database session
            async for session in get_session():
                await check_support_inbox(session)
                break  # Exit after first session
            
            # Wait for next check
            await asyncio.sleep(settings.email_to_ticket_interval)
            
        except Exception as e:
            print(f"[background-tasks] Error in email-to-ticket task: {e}")
            # Wait before retrying
            await asyncio.sleep(60)


async def start_background_tasks():
    """Start all background tasks"""
    settings = get_settings()
    
    tasks = []
    
    # Email-to-ticket checker
    if settings.email_to_ticket_enabled:
        tasks.append(asyncio.create_task(email_to_ticket_task()))
        print("[background-tasks] ✓ Email-to-ticket task scheduled")
    
    # Add more background tasks here as needed
    # tasks.append(asyncio.create_task(another_task()))
    
    if tasks:
        print(f"[background-tasks] Running {len(tasks)} background task(s)")
        await asyncio.gather(*tasks, return_exceptions=True)
    else:
        print("[background-tasks] No background tasks enabled")
