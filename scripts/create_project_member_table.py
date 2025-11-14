"""Create project_member table in the database."""
import sqlite3

def create_project_member_table():
    conn = sqlite3.connect('data.db')
    cursor = conn.cursor()
    
    # Create project_member table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS project_member (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            assigned_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            assigned_by INTEGER,
            FOREIGN KEY (project_id) REFERENCES project(id),
            FOREIGN KEY (user_id) REFERENCES user(id),
            FOREIGN KEY (assigned_by) REFERENCES user(id)
        )
    ''')
    
    # Create indexes for better query performance
    cursor.execute('CREATE INDEX IF NOT EXISTS ix_project_member_project_id ON project_member(project_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS ix_project_member_user_id ON project_member(user_id)')
    
    conn.commit()
    
    # Verify table was created
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='project_member'")
    result = cursor.fetchone()
    
    if result:
        print("✓ project_member table created successfully")
        
        # Show table structure
        cursor.execute("PRAGMA table_info(project_member)")
        columns = cursor.fetchall()
        print("\nTable structure:")
        for col in columns:
            print(f"  - {col[1]} ({col[2]})")
        
        # Check if there are any existing project members
        cursor.execute("SELECT COUNT(*) FROM project_member")
        count = cursor.fetchone()[0]
        print(f"\nCurrent project members: {count}")
    else:
        print("✗ Failed to create project_member table")
    
    conn.close()

if __name__ == "__main__":
    create_project_member_table()
