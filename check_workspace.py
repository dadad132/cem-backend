import sqlite3

conn = sqlite3.connect('data.db')
cursor = conn.cursor()
cursor.execute('SELECT id, name, site_title, logo_url, primary_color FROM workspace')
print('Workspace data:')
for row in cursor.fetchall():
    print(f'  ID: {row[0]}')
    print(f'  Name: {row[1]}')
    print(f'  Site Title: {row[2]}')
    print(f'  Logo URL: {row[3]}')
    print(f'  Primary Color: {row[4]}')
    print()
conn.close()
