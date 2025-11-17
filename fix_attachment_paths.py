"""
Fix attachment file paths - convert absolute paths to relative paths
"""
import sqlite3
from pathlib import Path

def fix_attachment_paths():
    """Update all attachment file_path values to use relative paths"""
    db_path = Path("data.db")
    
    if not db_path.exists():
        print("Error: data.db not found")
        return
    
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    
    print("Analyzing attachment paths...")
    
    # Check comment_attachment table
    try:
        cursor.execute("SELECT id, filename, file_path FROM comment_attachment")
        comment_attachments = cursor.fetchall()
        
        updates = []
        for att_id, filename, file_path in comment_attachments:
            if file_path:
                # Check if it's an absolute path (Windows or Linux)
                if file_path.startswith('/') or (len(file_path) > 1 and file_path[1] == ':'):
                    # Extract just the UUID filename from the path
                    path_obj = Path(file_path)
                    uuid_filename = path_obj.name
                    
                    # Construct relative path
                    relative_path = f"app/uploads/comments/{uuid_filename}"
                    
                    # Check if file exists locally
                    local_file = Path.cwd() / relative_path
                    file_exists = local_file.exists()
                    
                    updates.append((relative_path, att_id, file_exists))
                    print(f"  ID {att_id}: {filename}")
                    print(f"    Old: {file_path}")
                    print(f"    New: {relative_path}")
                    print(f"    File exists locally: {'✓ Yes' if file_exists else '✗ No'}")
                    print()
        
        if updates:
            print(f"\nFound {len(updates)} comment attachments with absolute paths")
            missing_files = [u for u in updates if not u[2]]
            if missing_files:
                print(f"WARNING: {len(missing_files)} files are missing locally!")
                print("You'll need to copy these files from the server to app/uploads/comments/")
            
            response = input("\nUpdate these paths in database? (yes/no): ")
            
            if response.lower() == 'yes':
                for relative_path, att_id, _ in updates:
                    cursor.execute(
                        "UPDATE comment_attachment SET file_path = ? WHERE id = ?",
                        (relative_path, att_id)
                    )
                conn.commit()
                print(f"✓ Updated {len(updates)} comment attachment paths")
            else:
                print("Skipped comment attachments")
        else:
            print("No comment attachments with absolute paths found")
    
    except sqlite3.OperationalError as e:
        print(f"Comment attachments table error: {e}")
    
    # Check TicketAttachment table
    try:
        cursor.execute("SELECT id, filename, file_path FROM ticketattachment")
        ticket_attachments = cursor.fetchall()
        
        updates = []
        for att_id, filename, file_path in ticket_attachments:
            if file_path:
                # Check if it's an absolute path (Windows or Linux)
                if file_path.startswith('/') or (len(file_path) > 1 and file_path[1] == ':'):
                    # Extract just the UUID filename from the path
                    path_obj = Path(file_path)
                    uuid_filename = path_obj.name
                    
                    # Construct relative path
                    relative_path = f"app/uploads/tickets/{uuid_filename}"
                    
                    # Check if file exists locally
                    local_file = Path.cwd() / relative_path
                    file_exists = local_file.exists()
                    
                    updates.append((relative_path, att_id, file_exists))
                    print(f"  ID {att_id}: {filename}")
                    print(f"    Old: {file_path}")
                    print(f"    New: {relative_path}")
                    print(f"    File exists locally: {'✓ Yes' if file_exists else '✗ No'}")
                    print()
        
        if updates:
            print(f"\nFound {len(updates)} ticket attachments with absolute paths")
            missing_files = [u for u in updates if not u[2]]
            if missing_files:
                print(f"WARNING: {len(missing_files)} files are missing locally!")
                print("You'll need to copy these files from the server to app/uploads/tickets/")
            
            response = input("\nUpdate these paths in database? (yes/no): ")
            
            if response.lower() == 'yes':
                for relative_path, att_id, _ in updates:
                    cursor.execute(
                        "UPDATE ticketattachment SET file_path = ? WHERE id = ?",
                        (relative_path, att_id)
                    )
                conn.commit()
                print(f"✓ Updated {len(updates)} ticket attachment paths")
            else:
                print("Skipped ticket attachments")
        else:
            print("No ticket attachments with absolute paths found")
    
    except sqlite3.OperationalError as e:
        print(f"Ticket attachments table error: {e}")
    
    conn.close()
    print("\n✓ Done!")

if __name__ == "__main__":
    fix_attachment_paths()
