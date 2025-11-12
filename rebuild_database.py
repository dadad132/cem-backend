"""
Force database rebuild to add missing project_member table
"""
import asyncio
import os
from sqlmodel import SQLModel
from app.core.database import engine

async def rebuild_database():
    print("Starting database rebuild...")
    
    # Check if database exists
    db_path = "data.db"
    if os.path.exists(db_path):
        print(f"Current database found: {db_path}")
        
        # Create backup before rebuild
        import shutil
        from datetime import datetime
        backup_name = f"data_backup_MANUAL_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        shutil.copy2(db_path, backup_name)
        print(f"Backup created: {backup_name}")
    
    # Import all models to ensure they're registered with SQLModel
    print("Importing models...")
    from app.models import (
        Workspace, User, Project, ProjectMember, Task, Comment, 
        CommentAttachment, Assignment, TaskHistory, Notification,
        Chat, ChatMember, Message, Meeting, MeetingAttendee,
        Company, Contact, Lead, Deal, Activity, ActivityLog,
        CustomField, CustomFieldValue, TaskDependency, TaskAttachment,
        TimeLog, SavedView
    )
    
    print("Registered models:")
    for table in SQLModel.metadata.sorted_tables:
        print(f"  - {table.name}")
    
    # Drop and recreate all tables
    async with engine.begin() as conn:
        print("\nDropping all tables...")
        await conn.run_sync(SQLModel.metadata.drop_all)
        print("Creating all tables...")
        await conn.run_sync(SQLModel.metadata.create_all)
    
    print("\n✓ Database rebuild complete!")
    print("\nNOTE: All data has been cleared. You may need to:")
    print("  1. Restore from the backup created above")
    print("  2. Or let the automatic restore run on next server start")
    
    # Verify project_member table exists
    import sqlite3
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    tables = cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='project_member'").fetchall()
    if tables:
        print("\n✓ Verified: project_member table exists")
    else:
        print("\n✗ ERROR: project_member table still missing!")
    conn.close()

if __name__ == "__main__":
    asyncio.run(rebuild_database())
