"""
FORCE FIX: Update all attachment paths from absolute to relative
Run this directly on the server via SSH
"""
import sqlite3
from pathlib import Path

def fix_paths():
    db_path = Path("data.db")
    
    if not db_path.exists():
        print("ERROR: data.db not found")
        return
    
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    
    print("=" * 60)
    print("FORCING ATTACHMENT PATH FIX")
    print("=" * 60)
    
    # Fix comment_attachment
    cursor.execute("SELECT id, file_path FROM comment_attachment")
    rows = cursor.fetchall()
    
    fixed = 0
    for att_id, file_path in rows:
        if file_path and (file_path.startswith('/') or (len(file_path) > 1 and file_path[1] == ':')):
            uuid_filename = Path(file_path).name
            new_path = f"app/uploads/comments/{uuid_filename}"
            cursor.execute("UPDATE comment_attachment SET file_path = ? WHERE id = ?", (new_path, att_id))
            print(f"✓ Fixed ID {att_id}: {uuid_filename}")
            fixed += 1
    
    # Fix ticketattachment
    cursor.execute("SELECT id, file_path FROM ticketattachment")
    rows = cursor.fetchall()
    
    for att_id, file_path in rows:
        if file_path and (file_path.startswith('/') or (len(file_path) > 1 and file_path[1] == ':')):
            uuid_filename = Path(file_path).name
            new_path = f"app/uploads/tickets/{uuid_filename}"
            cursor.execute("UPDATE ticketattachment SET file_path = ? WHERE id = ?", (new_path, att_id))
            print(f"✓ Fixed ID {att_id}: {uuid_filename}")
            fixed += 1
    
    # Fix taskattachment
    try:
        cursor.execute("SELECT id, file_path FROM taskattachment")
        rows = cursor.fetchall()
        
        for att_id, file_path in rows:
            if file_path and (file_path.startswith('/') or (len(file_path) > 1 and file_path[1] == ':')):
                uuid_filename = Path(file_path).name
                new_path = f"app/uploads/tasks/{uuid_filename}"
                cursor.execute("UPDATE taskattachment SET file_path = ? WHERE id = ?", (new_path, att_id))
                print(f"✓ Fixed ID {att_id}: {uuid_filename}")
                fixed += 1
    except:
        pass
    
    conn.commit()
    conn.close()
    
    print("=" * 60)
    print(f"COMPLETE: Fixed {fixed} attachment paths")
    print("=" * 60)

if __name__ == "__main__":
    fix_paths()
