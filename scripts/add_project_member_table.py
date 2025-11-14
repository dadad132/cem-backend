"""
Add project_member table to existing database without losing data
"""
import sqlite3
from datetime import datetime

conn = sqlite3.connect('data.db')
cursor = conn.cursor()

print("Checking database schema...")

# Check if project_member table already exists
tables = cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='project_member'").fetchall()

if tables:
    print("✓ project_member table already exists")
else:
    print("Creating project_member table...")
    
    # Create the project_member table
    cursor.execute('''
        CREATE TABLE project_member (
            id INTEGER NOT NULL PRIMARY KEY,
            project_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            assigned_at DATETIME NOT NULL,
            assigned_by INTEGER,
            FOREIGN KEY(project_id) REFERENCES project (id),
            FOREIGN KEY(user_id) REFERENCES user (id),
            FOREIGN KEY(assigned_by) REFERENCES user (id)
        )
    ''')
    
    # Create indexes
    cursor.execute('CREATE INDEX ix_project_member_project_id ON project_member (project_id)')
    cursor.execute('CREATE INDEX ix_project_member_user_id ON project_member (user_id)')
    
    conn.commit()
    print("✓ project_member table created")
    
    # Optionally: Auto-assign project owners to their projects
    print("\nAuto-assigning project owners to their projects...")
    projects = cursor.execute("SELECT id, owner_id FROM project").fetchall()
    
    for project_id, owner_id in projects:
        cursor.execute('''
            INSERT INTO project_member (project_id, user_id, assigned_at, assigned_by)
            VALUES (?, ?, ?, NULL)
        ''', (project_id, owner_id, datetime.now()))
    
    conn.commit()
    print(f"✓ Auto-assigned {len(projects)} project owners")

# Verify the table structure
cursor.execute("PRAGMA table_info(project_member)")
columns = cursor.fetchall()
print("\nproject_member table columns:")
for col in columns:
    print(f"  - {col[1]} ({col[2]})")

# Count records
cursor.execute("SELECT COUNT(*) FROM project_member")
member_count = cursor.fetchone()[0]
print(f"\nTotal project members: {member_count}")

conn.close()
print("\n✓ Migration complete!")
