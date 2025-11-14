import sqlite3

conn = sqlite3.connect('data.db')
cursor = conn.cursor()
tables = cursor.execute('SELECT name FROM sqlite_master WHERE type="table"').fetchall()
print("Existing tables:")
for table in tables:
    print(f"  - {table[0]}")
conn.close()
