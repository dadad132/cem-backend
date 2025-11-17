#!/usr/bin/env python3
"""
Add site settings columns to workspace table
Run this directly on the server: python add_site_settings_columns.py
"""

import sqlite3
import sys

DB_PATH = "data.db"

def add_columns():
    """Add site settings columns to workspace table"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Get existing columns
        cursor.execute("PRAGMA table_info(workspace)")
        existing_columns = {row[1] for row in cursor.fetchall()}
        print(f"Existing workspace columns: {existing_columns}")
        
        # Add columns if they don't exist
        columns_to_add = [
            ("site_title", "TEXT"),
            ("logo_url", "TEXT"),
            ("favicon_url", "TEXT"),
            ("primary_color", "TEXT DEFAULT '#2563eb'")
        ]
        
        for column_name, column_type in columns_to_add:
            if column_name not in existing_columns:
                print(f"Adding {column_name} column...")
                try:
                    cursor.execute(f"ALTER TABLE workspace ADD COLUMN {column_name} {column_type}")
                    print(f"✓ Added {column_name}")
                except sqlite3.OperationalError as e:
                    print(f"✗ Failed to add {column_name}: {e}")
            else:
                print(f"✓ {column_name} already exists")
        
        conn.commit()
        conn.close()
        
        print("\n✅ Site settings columns added successfully!")
        print("You can now restart the service: sudo systemctl restart crm-backend")
        return 0
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(add_columns())
