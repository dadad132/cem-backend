"""
Test soft delete functionality
"""
import sqlite3
from datetime import datetime

conn = sqlite3.connect('data.db')
cursor = conn.cursor()

print("=" * 60)
print("SOFT DELETE TEST")
print("=" * 60)

# Show current users
print("\nCurrent active users:")
cursor.execute("SELECT id, username, full_name, is_admin, deleted_at FROM user WHERE deleted_at IS NULL")
for row in cursor.fetchall():
    admin_flag = "👑 ADMIN" if row[3] else "👤 USER"
    print(f"  {admin_flag} - ID:{row[0]} - {row[1]} ({row[2]})")

# Show if there are any deleted users
cursor.execute("SELECT COUNT(*) FROM user WHERE deleted_at IS NOT NULL")
deleted_count = cursor.fetchone()[0]
print(f"\nDeleted users: {deleted_count}")

if deleted_count > 0:
    print("\nDeleted user details:")
    cursor.execute("""
        SELECT u.id, u.username, u.full_name, u.deleted_at, u.deleted_by, admin.username 
        FROM user u
        LEFT JOIN user admin ON u.deleted_by = admin.id
        WHERE u.deleted_at IS NOT NULL
    """)
    for row in cursor.fetchall():
        print(f"  ID:{row[0]} - {row[1]} ({row[2]})")
        print(f"    Deleted at: {row[3]}")
        print(f"    Deleted by: {row[5] if row[5] else 'Unknown'} (ID:{row[4]})")

# Check data preservation for user #2 (jpb)
print("\n" + "=" * 60)
print("DATA PRESERVATION CHECK (using User #2 as example)")
print("=" * 60)

user_id_to_check = 2

# Check tasks created by this user
cursor.execute("SELECT COUNT(*) FROM task WHERE creator_id = ?", (user_id_to_check,))
task_count = cursor.fetchone()[0]
print(f"\nTasks created by user #{user_id_to_check}: {task_count}")

# Check comments by this user
cursor.execute("SELECT COUNT(*) FROM comment WHERE author_id = ?", (user_id_to_check,))
comment_count = cursor.fetchone()[0]
print(f"Comments by user #{user_id_to_check}: {comment_count}")

# Check assignments
cursor.execute("SELECT COUNT(*) FROM assignment WHERE assignee_id = ?", (user_id_to_check,))
assignment_count = cursor.fetchone()[0]
print(f"Task assignments for user #{user_id_to_check}: {assignment_count}")

# Check project memberships
cursor.execute("SELECT COUNT(*) FROM project_member WHERE user_id = ?", (user_id_to_check,))
membership_count = cursor.fetchone()[0]
print(f"Project memberships for user #{user_id_to_check}: {membership_count}")

print("\n" + "=" * 60)
print("If user #2 is deleted, all the data above will be preserved!")
print("The user just won't be able to log in or appear in user lists.")
print("=" * 60)

conn.close()
