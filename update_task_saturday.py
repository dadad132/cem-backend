import sqlite3

conn = sqlite3.connect('data.db')
# Update task to include Saturday in working_days AND extend due_date to include a Saturday
# Nov 29, 2025 is a Saturday
conn.execute("UPDATE task SET working_days = '0,1,2,3,4,5', due_date = '2025-11-29' WHERE id = 14")
conn.commit()

c = conn.cursor()
c.execute('SELECT id, title, working_days, start_date, due_date FROM task WHERE id = 14')
print("Updated task:")
print(c.fetchone())

conn.close()
