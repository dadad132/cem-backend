"""
Migration: Fix attachment file paths from absolute to relative
Converts paths like /home/eugene/crm-backend/app/uploads/... to app/uploads/...
"""
import sqlite3
from pathlib import Path

def migrate():
    """Convert all absolute attachment paths to relative paths"""
    db_path = Path("data.db")
    
    if not db_path.exists():
        print("❌ data.db not found, skipping attachment path migration")
        return
    
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    
    total_updated = 0
    
    # Fix comment_attachment table
    try:
        cursor.execute("SELECT id, file_path FROM comment_attachment")
        comment_attachments = cursor.fetchall()
        
        updates = []
        for att_id, file_path in comment_attachments:
            if file_path and (file_path.startswith('/') or (len(file_path) > 1 and file_path[1] == ':')):
                # Extract UUID filename from absolute path
                uuid_filename = Path(file_path).name
                relative_path = f"app/uploads/comments/{uuid_filename}"
                updates.append((relative_path, att_id))
        
        if updates:
            print(f"  Converting {len(updates)} comment attachment paths...")
            for relative_path, att_id in updates:
                cursor.execute(
                    "UPDATE comment_attachment SET file_path = ? WHERE id = ?",
                    (relative_path, att_id)
                )
            total_updated += len(updates)
    except sqlite3.OperationalError:
        pass  # Table doesn't exist
    
    # Fix ticketattachment table
    try:
        cursor.execute("SELECT id, file_path FROM ticketattachment")
        ticket_attachments = cursor.fetchall()
        
        updates = []
        for att_id, file_path in ticket_attachments:
            if file_path and (file_path.startswith('/') or (len(file_path) > 1 and file_path[1] == ':')):
                # Extract UUID filename from absolute path
                uuid_filename = Path(file_path).name
                relative_path = f"app/uploads/tickets/{uuid_filename}"
                updates.append((relative_path, att_id))
        
        if updates:
            print(f"  Converting {len(updates)} ticket attachment paths...")
            for relative_path, att_id in updates:
                cursor.execute(
                    "UPDATE ticketattachment SET file_path = ? WHERE id = ?",
                    (relative_path, att_id)
                )
            total_updated += len(updates)
    except sqlite3.OperationalError:
        pass  # Table doesn't exist
    
    # Fix taskattachment table
    try:
        cursor.execute("SELECT id, file_path FROM taskattachment")
        task_attachments = cursor.fetchall()
        
        updates = []
        for att_id, file_path in task_attachments:
            if file_path and (file_path.startswith('/') or (len(file_path) > 1 and file_path[1] == ':')):
                # Extract UUID filename from absolute path
                uuid_filename = Path(file_path).name
                relative_path = f"app/uploads/tasks/{uuid_filename}"
                updates.append((relative_path, att_id))
        
        if updates:
            print(f"  Converting {len(updates)} task attachment paths...")
            for relative_path, att_id in updates:
                cursor.execute(
                    "UPDATE taskattachment SET file_path = ? WHERE id = ?",
                    (relative_path, att_id)
                )
            total_updated += len(updates)
    except sqlite3.OperationalError:
        pass  # Table doesn't exist
    
    if total_updated > 0:
        conn.commit()
        print(f"✓ Migration complete: Converted {total_updated} attachment paths to relative format")
    else:
        print("✓ No absolute attachment paths found")
    
    conn.close()

if __name__ == "__main__":
    migrate()
