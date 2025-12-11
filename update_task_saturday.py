import sqlite3

conn = sqlite3.connect('data.db')
conn.execute("UPDATE task SET working_days = '0,1,2,3,4,5' WHERE id = 14")
conn.commit()

c = conn.cursor()
c.execute('SELECT id, title, working_days, start_date, due_date FROM task WHERE id = 14')
print("Updated task:")
print(c.fetchone())

conn.close()
