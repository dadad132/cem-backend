"""
Master Migration Script - Runs all database migrations in order
This ensures all database schema changes are applied correctly
"""
import sqlite3
import os
from datetime import datetime

def run_migration(db_path, migration_name, sql_commands):
    """Run a single migration"""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Check if column exists
        cursor.execute("PRAGMA table_info(user)" if 'user' in migration_name.lower() else "PRAGMA table_info(task)" if 'task' in migration_name.lower() else "PRAGMA table_info(meeting)")
        columns = [row[1] for row in cursor.fetchall()]
        
        # Check which columns are missing
        needs_migration = False
        for cmd in sql_commands:
            if 'ADD COLUMN' in cmd:
                col_name = cmd.split('ADD COLUMN')[1].strip().split()[0]
                if col_name not in columns:
                    needs_migration = True
                    break
        
        if not needs_migration:
            print(f"  ✓ {migration_name} - Already applied")
            conn.close()
            return True
        
        print(f"  [i] Applying {migration_name}...")
        
        for cmd in sql_commands:
            try:
                cursor.execute(cmd)
            except sqlite3.OperationalError as e:
                if 'duplicate column' in str(e).lower():
                    print(f"      Column already exists, skipping...")
                else:
                    raise
        
        conn.commit()
        conn.close()
        print(f"  ✓ {migration_name} - Complete")
        return True
        
    except Exception as e:
        print(f"  ✗ {migration_name} - Error: {e}")
        return False

def main():
    print("=" * 70)
    print("Master Migration Script - CRM Backend")
    print("=" * 70)
    print()
    
    # Find database
    db_path = 'data.db'
    if not os.path.exists(db_path):
        db_path = '/home/eugene/crm-backend/data.db'
    
    if not os.path.exists(db_path):
        print("❌ Error: Could not find data.db")
        return False
    
    print(f"Database: {db_path}\n")
    
    # Define all migrations in order
    migrations = [
        {
            'name': 'User Profile Picture',
            'table': 'user',
            'commands': [
                "ALTER TABLE user ADD COLUMN profile_picture VARCHAR"
            ]
        },
        {
            'name': 'Task Archiving',
            'table': 'task',
            'commands': [
                "ALTER TABLE task ADD COLUMN is_archived BOOLEAN DEFAULT 0",
                "ALTER TABLE task ADD COLUMN archived_at DATETIME DEFAULT NULL"
            ]
        },
        {
            'name': 'Meeting Cancellation',
            'table': 'meeting',
            'commands': [
                "ALTER TABLE meeting ADD COLUMN is_cancelled BOOLEAN DEFAULT 0",
                "ALTER TABLE meeting ADD COLUMN cancelled_at DATETIME DEFAULT NULL",
                "ALTER TABLE meeting ADD COLUMN cancelled_by INTEGER DEFAULT NULL"
            ]
        }
    ]
    
    print("Running migrations...\n")
    
    success_count = 0
    for migration in migrations:
        # Check what columns exist
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(f"PRAGMA table_info({migration['table']})")
        existing_columns = [row[1] for row in cursor.fetchall()]
        conn.close()
        
        # Filter commands to only add missing columns
        needed_commands = []
        for cmd in migration['commands']:
            if 'ADD COLUMN' in cmd:
                col_name = cmd.split('ADD COLUMN')[1].strip().split()[0]
                if col_name not in existing_columns:
                    needed_commands.append(cmd)
        
        if not needed_commands:
            print(f"  ✓ {migration['name']} - Already applied")
            success_count += 1
        else:
            # Run the migration
            try:
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()
                
                for cmd in needed_commands:
                    cursor.execute(cmd)
                    col_name = cmd.split('ADD COLUMN')[1].strip().split()[0]
                    print(f"      Added column: {col_name}")
                
                conn.commit()
                
                # Create indexes if needed
                if migration['name'] == 'Task Archiving':
                    try:
                        cursor.execute("CREATE INDEX IF NOT EXISTS ix_task_is_archived ON task (is_archived)")
                        print(f"      Created index: ix_task_is_archived")
                    except:
                        pass
                
                if migration['name'] == 'Meeting Cancellation':
                    try:
                        cursor.execute("CREATE INDEX IF NOT EXISTS ix_meeting_is_cancelled ON meeting (is_cancelled)")
                        print(f"      Created index: ix_meeting_is_cancelled")
                    except:
                        pass
                
                conn.commit()
                conn.close()
                print(f"  ✓ {migration['name']} - Complete")
                success_count += 1
                
            except Exception as e:
                print(f"  ✗ {migration['name']} - Error: {e}")
                if conn:
                    conn.close()
    
    print()
    print("=" * 70)
    print(f"Migration Summary: {success_count}/{len(migrations)} successful")
    print("=" * 70)
    
    if success_count == len(migrations):
        print("\n✓ All migrations completed successfully!")
        print("\nNext step: Restart your server")
        print("  sudo systemctl restart crm-backend")
        return True
    else:
        print("\n⚠ Some migrations had issues. Check errors above.")
        return False

if __name__ == "__main__":
    main()
