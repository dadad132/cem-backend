"""
Verify current database state
"""
import sqlite3

conn = sqlite3.connect('data.db')
cursor = conn.cursor()

print("=" * 60)
print("DATABASE VERIFICATION")
print("=" * 60)

# Check tables
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [row[0] for row in cursor.fetchall()]
print(f"\n✓ Total tables: {len(tables)}")
print(f"✓ project_member table exists: {'project_member' in tables}")

# Check data counts
print("\nData counts:")
cursor.execute("SELECT COUNT(*) FROM user")
user_count = cursor.fetchone()[0]
print(f"  Users: {user_count}")

cursor.execute("SELECT COUNT(*) FROM project")
project_count = cursor.fetchone()[0]
print(f"  Projects: {project_count}")

cursor.execute("SELECT COUNT(*) FROM task")
task_count = cursor.fetchone()[0]
print(f"  Tasks: {task_count}")

cursor.execute("SELECT COUNT(*) FROM project_member")
member_count = cursor.fetchone()[0]
print(f"  Project Members: {member_count}")

# Show users
print("\nUsers:")
cursor.execute("SELECT id, username, full_name, is_admin FROM user")
for row in cursor.fetchall():
    admin_flag = "👑 ADMIN" if row[3] else "👤 USER"
    print(f"  {admin_flag} - ID:{row[0]} - {row[1]} ({row[2]})")

# Show projects
print("\nProjects:")
cursor.execute("SELECT id, name, owner_id FROM project")
for row in cursor.fetchall():
    print(f"  ID:{row[0]} - {row[1]} (Owner: User #{row[2]})")

# Show project members
print("\nProject Members:")
cursor.execute("""
    SELECT pm.id, p.name, u.username 
    FROM project_member pm
    JOIN project p ON pm.project_id = p.id
    JOIN user u ON pm.user_id = u.id
""")
for row in cursor.fetchall():
    print(f"  ID:{row[0]} - {row[2]} assigned to '{row[1]}'")

conn.close()
print("\n" + "=" * 60)
