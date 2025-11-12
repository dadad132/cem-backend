"""
Restore database from backup
"""
import os
import shutil
from datetime import datetime

# Get the most recent backup
backups = [f for f in os.listdir('.') if f.startswith('data_backup_') and f.endswith('.db')]
if not backups:
    print("No backups found!")
    exit(1)

# Sort by modification time
backups.sort(key=lambda x: os.path.getmtime(x), reverse=True)
latest_backup = backups[0]

print(f"Found {len(backups)} backup(s)")
print(f"Latest backup: {latest_backup}")
backup_time = datetime.fromtimestamp(os.path.getmtime(latest_backup))
print(f"Backup date: {backup_time}")
backup_size = os.path.getsize(latest_backup) / 1024
print(f"Backup size: {backup_size:.2f} KB")

# Create a backup of the current (empty) database
if os.path.exists('data.db'):
    temp_backup = f"data_empty_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    shutil.copy2('data.db', temp_backup)
    print(f"\nCurrent database backed up to: {temp_backup}")

# Restore from backup
print(f"\nRestoring from {latest_backup}...")
shutil.copy2(latest_backup, 'data.db')
print("✓ Restore complete!")

# Verify restoration
import sqlite3
conn = sqlite3.connect('data.db')
cursor = conn.cursor()

# Check for project_member table
tables = cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='project_member'").fetchall()
if tables:
    print("✓ project_member table exists")
else:
    print("✗ WARNING: project_member table missing after restore!")
    print("  The backup may be from before the project assignment system was added.")

# Count records
cursor.execute("SELECT COUNT(*) FROM user")
user_count = cursor.fetchone()[0]
cursor.execute("SELECT COUNT(*) FROM project")
project_count = cursor.fetchone()[0]
cursor.execute("SELECT COUNT(*) FROM task")
task_count = cursor.fetchone()[0]

print(f"\nRestored data:")
print(f"  Users: {user_count}")
print(f"  Projects: {project_count}")
print(f"  Tasks: {task_count}")

conn.close()
