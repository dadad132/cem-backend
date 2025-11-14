"""
Email-to-Task Processor V3
Per-project IMAP configuration support
"""

import imaplib
import email
from email.header import decode_header
from email.utils import parseaddr
import logging
from datetime import datetime, date
from typing import Optional, List, Tuple
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.ticket import Ticket
from app.models.task import Task
from app.models.user import User
from app.models.notification import Notification
from app.models.project import Project
from app.models.processed_mail import ProcessedMail
from app.models.enums import TaskStatus, TaskPriority

logger = logging.getLogger(__name__)


class ProjectEmailProcessor:
    """Process emails for a specific project using its IMAP settings"""
    
    def __init__(self, project: Project):
        self.project = project
        
    def has_email_config(self) -> bool:
        """Check if project has complete email configuration"""
        return bool(
            self.project.support_email and
            self.project.imap_host and
            self.project.imap_username and
            self.project.imap_password
        )
    
    def connect_imap(self):
        """Connect to project's IMAP server"""
        try:
            port = self.project.imap_port or 993
            
            if self.project.imap_use_ssl:
                mail = imaplib.IMAP4_SSL(self.project.imap_host, port)
            else:
                mail = imaplib.IMAP4(self.project.imap_host, port)
            
            mail.login(self.project.imap_username, self.project.imap_password)
            return mail
        except Exception as e:
            logger.error(f"[Project {self.project.id}] IMAP connection failed: {e}")
            raise
    
    def decode_header_value(self, header: str) -> str:
        """Decode email header"""
        if not header:
            return ""
        
        decoded_parts = decode_header(header)
        decoded_string = ""
        
        for part, encoding in decoded_parts:
            if isinstance(part, bytes):
                decoded_string += part.decode(encoding or 'utf-8', errors='ignore')
            else:
                decoded_string += part
        
        return decoded_string
    
    def extract_email_address(self, from_header: str) -> Tuple[str, str]:
        """Extract name and email from 'From' header"""
        name, email_addr = parseaddr(from_header)
        return name or "Unknown", email_addr.lower()
    
    def extract_email_body(self, msg) -> str:
        """Extract plain text body from email"""
        body = ""
        
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type == "text/plain":
                    try:
                        body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                        break
                    except:
                        pass
        else:
            try:
                body = msg.get_payload(decode=True).decode('utf-8', errors='ignore')
            except:
                body = str(msg.get_payload())
        
        return body.strip()
    
    def determine_priority(self, subject: str, body: str) -> TaskPriority:
        """Determine task priority from email content"""
        urgent_keywords = ['urgent', 'critical', 'emergency', 'asap', 'immediately']
        high_keywords = ['important', 'high priority', 'soon']
        
        combined = f"{subject} {body}".lower()
        
        for keyword in urgent_keywords:
            if keyword in combined:
                return TaskPriority.critical
        
        for keyword in high_keywords:
            if keyword in combined:
                return TaskPriority.high
        
        return TaskPriority.medium
    
    async def is_email_processed(self, db: AsyncSession, message_id: str) -> bool:
        """Check if email already processed"""
        result = await db.execute(
            select(ProcessedMail).where(ProcessedMail.message_id == message_id)
        )
        return result.scalar_one_or_none() is not None
    
    async def mark_email_processed(
        self, 
        db: AsyncSession, 
        message_id: str, 
        email_from: str, 
        subject: str, 
        task_id: int
    ):
        """Mark email as processed"""
        processed = ProcessedMail(
            message_id=message_id,
            email_from=email_from,
            subject=subject,
            ticket_id=task_id,  # Using ticket_id field for task_id
            workspace_id=self.project.workspace_id,
            processed_at=datetime.utcnow()
        )
        db.add(processed)
        await db.commit()
    
    async def create_task_from_email(
        self,
        db: AsyncSession,
        sender_name: str,
        sender_email: str,
        subject: str,
        body: str
    ) -> Task:
        """Create task in project from email"""
        
        # Extract concise title from subject (max 100 chars)
        title = subject[:100] if len(subject) <= 100 else subject[:97] + "..."
        
        # Build description with email metadata
        description = f"📧 Email from: {sender_name} ({sender_email})\n"
        description += f"📅 Received: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        description += f"📋 Subject: {subject}\n\n"
        description += "─" * 50 + "\n\n"
        description += body[:4000]  # Limit description
        
        # Determine priority
        priority = self.determine_priority(subject, body)
        
        # Get project owner as task creator
        creator_id = self.project.owner_id
        if not creator_id:
            # Fallback: first admin in workspace
            admin_query = select(User).where(
                User.workspace_id == self.project.workspace_id,
                User.is_admin == True,
                User.is_active == True
            )
            result = await db.execute(admin_query)
            admin = result.scalars().first()
            creator_id = admin.id if admin else 1
        
        # Create task
        task = Task(
            title=title,
            description=description,
            project_id=self.project.id,
            creator_id=creator_id,
            status=TaskStatus.todo,
            priority=priority,
            start_date=date.today(),
            due_date=None
        )
        
        db.add(task)
        await db.flush()
        
        # Notify project members
        from app.models.project_member import ProjectMember
        members_query = select(ProjectMember).where(ProjectMember.project_id == self.project.id)
        result = await db.execute(members_query)
        members = result.scalars().all()
        
        for member in members:
            notification = Notification(
                user_id=member.user_id,
                type='task',
                message=f"New task from {sender_email}: {title}",
                url=f'/web/tasks/{task.id}',
                related_id=task.id
            )
            db.add(notification)
        
        await db.commit()
        await db.refresh(task)
        
        logger.info(f"[Project {self.project.name}] Created task '{title}' from {sender_email}")
        
        return task
    
    async def process_emails(self, db: AsyncSession) -> List[Task]:
        """Process unread emails from project's inbox"""
        tasks_created = []
        
        if not self.has_email_config():
            return tasks_created
        
        try:
            mail = self.connect_imap()
            mail.select('INBOX')
            
            # Search for unread emails
            status, messages = mail.search(None, 'UNSEEN')
            email_ids = messages[0].split()
            
            logger.info(f"[Project {self.project.name}] Found {len(email_ids)} unread emails")
            
            for email_id in email_ids:
                try:
                    # Fetch email
                    status, msg_data = mail.fetch(email_id, '(RFC822)')
                    msg = email.message_from_bytes(msg_data[0][1])
                    
                    # Get message ID
                    message_id = msg.get('Message-ID', f'no-id-{email_id.decode()}')
                    
                    # Check if already processed
                    if await self.is_email_processed(db, message_id):
                        mail.store(email_id, '+FLAGS', '\\Seen')
                        continue
                    
                    # Extract email info
                    from_header = msg.get('From', '')
                    sender_name, sender_email = self.extract_email_address(from_header)
                    subject = self.decode_header_value(msg.get('Subject', 'No Subject'))
                    body = self.extract_email_body(msg)
                    
                    # Create task
                    task = await self.create_task_from_email(
                        db, sender_name, sender_email, subject, body
                    )
                    
                    # Mark as processed
                    await self.mark_email_processed(
                        db, message_id, sender_email, subject, task.id
                    )
                    
                    # Mark email as read
                    mail.store(email_id, '+FLAGS', '\\Seen')
                    
                    tasks_created.append(task)
                    
                except Exception as e:
                    logger.error(f"[Project {self.project.name}] Error processing email {email_id}: {e}")
                    continue
            
            mail.close()
            mail.logout()
            
        except Exception as e:
            logger.error(f"[Project {self.project.name}] Email processing error: {e}")
        
        return tasks_created


async def process_all_project_emails(db: AsyncSession, workspace_id: int) -> dict:
    """
    Process emails for all projects with email configuration
    
    Returns:
        dict: {project_id: [tasks_created]}
    """
    results = {}
    
    # Get all projects with email configuration
    result = await db.execute(
        select(Project).where(
            Project.workspace_id == workspace_id,
            Project.support_email.isnot(None),
            Project.imap_host.isnot(None),
            Project.is_archived == False
        )
    )
    projects = result.scalars().all()
    
    logger.info(f"[Workspace {workspace_id}] Processing emails for {len(projects)} projects")
    
    for project in projects:
        processor = ProjectEmailProcessor(project)
        tasks = await processor.process_emails(db)
        results[project.id] = tasks
        
        if tasks:
            logger.info(f"[Project {project.name}] Created {len(tasks)} tasks from emails")
    
    return results
