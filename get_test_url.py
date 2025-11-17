import sqlite3

conn = sqlite3.connect('data.db')
c = conn.cursor()
c.execute('SELECT c.id, ca.id as att_id FROM comment c JOIN comment_attachment ca ON c.id = ca.comment_id LIMIT 1')
result = c.fetchone()
if result:
    print(f'Test URL: http://localhost:8000/web/attachments/{result[1]}/preview')
    print(f'Attachment ID: {result[1]}')
else:
    print('No comments with attachments found')
conn.close()
