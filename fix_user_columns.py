"""
Add missing columns to user table
Run this to fix the database schema
"""
import sqlite3
import sys

def add_missing_columns():
    try:
        conn = sqlite3.connect('data.db')
        cursor = conn.cursor()
        
        print("Checking and adding missing columns to user table...")
        
        # Get existing columns
        cursor.execute("PRAGMA table_info(user)")
        existing_columns = [row[1] for row in cursor.fetchall()]
        print(f"Existing columns: {existing_columns}")
        
        columns_to_add = [
            ("profile_picture", "VARCHAR", "NULL"),
            ("calendar_color", "VARCHAR", "'#3b82f6'"),
            ("can_see_all_tickets", "BOOLEAN", "0"),
        ]
        
        for column_name, column_type, default_value in columns_to_add:
            if column_name not in existing_columns:
                try:
                    sql = f"ALTER TABLE user ADD COLUMN {column_name} {column_type} DEFAULT {default_value}"
                    cursor.execute(sql)
                    print(f"✅ Added column: {column_name}")
                except sqlite3.OperationalError as e:
                    print(f"⚠️  Column {column_name} might already exist: {e}")
            else:
                print(f"✓ Column {column_name} already exists")
        
        conn.commit()
        print("\n✅ Database schema updated successfully!")
        
        # Verify
        cursor.execute("PRAGMA table_info(user)")
        all_columns = [row[1] for row in cursor.fetchall()]
        print(f"\nFinal columns in user table: {all_columns}")
        
        conn.close()
        return True
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

if __name__ == "__main__":
    success = add_missing_columns()
    sys.exit(0 if success else 1)
