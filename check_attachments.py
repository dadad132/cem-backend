import sqlite3

conn = sqlite3.connect('data.db')
cursor = conn.cursor()
cursor.execute("SELECT id, filename, file_path FROM comment_attachment")
attachments = cursor.fetchall()

print(f"Found {len(attachments)} comment attachments:\n")
for att_id, filename, file_path in attachments:
    print(f"ID {att_id}: {filename}")
    print(f"  Path: {file_path}")
    print()

conn.close()
