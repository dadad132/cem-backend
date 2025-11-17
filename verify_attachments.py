import sqlite3
import os
from pathlib import Path

conn = sqlite3.connect('data.db')
c = conn.cursor()

c.execute('SELECT id, filename, file_path FROM comment_attachment')
attachments = c.fetchall()

print('Comment Attachments in Current Database:\n')
missing = []
for att_id, fname, fpath in attachments:
    exists = os.path.exists(fpath)
    status = '✓' if exists else '✗ MISSING'
    if not exists:
        missing.append((att_id, fname, fpath))
    print(f'{status} ID {att_id}: {fname}')
    print(f'   Path: {fpath}')
    print()

print(f'Total: {len(attachments)} attachments')
if missing:
    print(f'\n⚠ Missing files: {len(missing)}')
    for att_id, fname, fpath in missing:
        print(f'  - {fname} (ID {att_id})')
else:
    print('\n✓ All attachment files exist!')

conn.close()
