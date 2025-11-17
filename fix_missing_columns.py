#!/usr/bin/env python3
"""
Fix missing database columns - adds all missing columns to existing database
"""
import sqlite3
import sys
from pathlib import Path

def add_column_if_not_exists(cursor, table, column, column_type, default_value=None):
    """Add a column to a table if it doesn't exist"""
    try:
        # Check if column exists
        cursor.execute(f"PRAGMA table_info({table})")
        columns = [row[1] for row in cursor.fetchall()]
        
        if column not in columns:
            # Add the column
            default_clause = f" DEFAULT {default_value}" if default_value is not None else ""
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}{default_clause}")
            print(f"✅ Added {table}.{column}")
            return True
        else:
            print(f"⏭️  {table}.{column} already exists")
            return False
    except Exception as e:
        print(f"❌ Error adding {table}.{column}: {e}")
        return False

def main():
    db_path = Path("data.db")
    
    if not db_path.exists():
        print("❌ Database file not found: data.db")
        sys.exit(1)
    
    print("=" * 60)
    print("Fixing Missing Database Columns")
    print("=" * 60)
    
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    
    changes_made = False
    
    # User table - missing columns
    print("\n📋 Checking 'user' table...")
    changes_made |= add_column_if_not_exists(cursor, "user", "calendar_color", "VARCHAR", "'#3b82f6'")
    changes_made |= add_column_if_not_exists(cursor, "user", "can_see_all_tickets", "BOOLEAN", "0")
    changes_made |= add_column_if_not_exists(cursor, "user", "google_id", "VARCHAR", "NULL")
    changes_made |= add_column_if_not_exists(cursor, "user", "google_access_token", "VARCHAR", "NULL")
    changes_made |= add_column_if_not_exists(cursor, "user", "google_refresh_token", "VARCHAR", "NULL")
    changes_made |= add_column_if_not_exists(cursor, "user", "google_token_expiry", "DATETIME", "NULL")
    
    # Project table - missing columns
    print("\n📋 Checking 'project' table...")
    changes_made |= add_column_if_not_exists(cursor, "project", "support_email", "VARCHAR", "NULL")
    changes_made |= add_column_if_not_exists(cursor, "project", "imap_host", "VARCHAR", "NULL")
    changes_made |= add_column_if_not_exists(cursor, "project", "imap_port", "INTEGER", "NULL")
    changes_made |= add_column_if_not_exists(cursor, "project", "imap_username", "VARCHAR", "NULL")
    changes_made |= add_column_if_not_exists(cursor, "project", "imap_password", "VARCHAR", "NULL")
    changes_made |= add_column_if_not_exists(cursor, "project", "imap_use_ssl", "BOOLEAN", "1")
    changes_made |= add_column_if_not_exists(cursor, "project", "is_archived", "BOOLEAN", "0")
    changes_made |= add_column_if_not_exists(cursor, "project", "archived_at", "DATETIME", "NULL")
    changes_made |= add_column_if_not_exists(cursor, "project", "start_date", "DATE", "NULL")
    changes_made |= add_column_if_not_exists(cursor, "project", "due_date", "DATE", "NULL")
    
    # Task table - missing columns
    print("\n📋 Checking 'task' table...")
    changes_made |= add_column_if_not_exists(cursor, "task", "is_archived", "BOOLEAN", "0")
    changes_made |= add_column_if_not_exists(cursor, "task", "archived_at", "DATETIME", "NULL")
    changes_made |= add_column_if_not_exists(cursor, "task", "parent_task_id", "INTEGER", "NULL")
    
    # Ticket table - missing columns
    print("\n📋 Checking 'ticket' table...")
    changes_made |= add_column_if_not_exists(cursor, "ticket", "is_archived", "BOOLEAN", "0")
    changes_made |= add_column_if_not_exists(cursor, "ticket", "archived_at", "DATETIME", "NULL")
    changes_made |= add_column_if_not_exists(cursor, "ticket", "closed_by_id", "INTEGER", "NULL")
    changes_made |= add_column_if_not_exists(cursor, "ticket", "is_guest", "BOOLEAN", "0")
    changes_made |= add_column_if_not_exists(cursor, "ticket", "guest_name", "VARCHAR", "NULL")
    changes_made |= add_column_if_not_exists(cursor, "ticket", "guest_surname", "VARCHAR", "NULL")
    changes_made |= add_column_if_not_exists(cursor, "ticket", "guest_email", "VARCHAR", "NULL")
    changes_made |= add_column_if_not_exists(cursor, "ticket", "guest_phone", "VARCHAR", "NULL")
    changes_made |= add_column_if_not_exists(cursor, "ticket", "guest_company", "VARCHAR", "NULL")
    changes_made |= add_column_if_not_exists(cursor, "ticket", "guest_office_number", "VARCHAR", "NULL")
    changes_made |= add_column_if_not_exists(cursor, "ticket", "guest_branch", "VARCHAR", "NULL")
    
    if changes_made:
        conn.commit()
        print("\n" + "=" * 60)
        print("✅ Database updated successfully!")
        print("=" * 60)
    else:
        print("\n" + "=" * 60)
        print("ℹ️  No changes needed - all columns exist")
        print("=" * 60)
    
    conn.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())
