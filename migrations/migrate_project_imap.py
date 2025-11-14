"""
Migration: Add per-project IMAP configuration fields
Allows each project/board to have its own email inbox
"""
import sqlite3
from datetime import datetime


def migrate():
    """Add IMAP configuration fields to project table"""
    conn = sqlite3.connect('data.db')
    cursor = conn.cursor()
    
    print("=" * 70)
    print("MIGRATION: Add Per-Project Email Configuration")
    print("=" * 70)
    
    # List of new columns to add
    new_columns = [
        ('imap_host', 'TEXT'),
        ('imap_port', 'INTEGER'),
        ('imap_username', 'TEXT'),
        ('imap_password', 'TEXT'),
        ('imap_use_ssl', 'BOOLEAN DEFAULT 1'),
    ]
    
    # Check existing columns
    cursor.execute("PRAGMA table_info(project)")
    existing_columns = [row[1] for row in cursor.fetchall()]
    
    for column_name, column_type in new_columns:
        if column_name in existing_columns:
            print(f"✓ Column '{column_name}' already exists")
        else:
            try:
                print(f"Adding column '{column_name}' ({column_type})...")
                cursor.execute(f"ALTER TABLE project ADD COLUMN {column_name} {column_type}")
                print(f"✓ Column '{column_name}' added successfully")
            except Exception as e:
                print(f"✗ Error adding '{column_name}': {e}")
    
    conn.commit()
    conn.close()
    
    print("\n" + "=" * 70)
    print("✅ Migration completed!")
    print("=" * 70)
    print("\nNext steps:")
    print("1. Each project can now have its own email inbox")
    print("2. Edit a project and set the support email + IMAP details")
    print("3. System will check that project's inbox for new emails")
    print("4. Tasks will be created automatically in that project")


if __name__ == "__main__":
    migrate()
