"""Add comment_time_format column to workspace table."""
import sqlite3, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def migrate():
    db_path = 'data.db'
    if not os.path.exists(db_path):
        print(f"Database not found at {db_path}"); return
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("PRAGMA table_info(workspace)")
        columns = {row[1] for row in cursor.fetchall()}
        if 'comment_time_format' not in columns:
            cursor.execute("ALTER TABLE workspace ADD COLUMN comment_time_format TEXT DEFAULT '12hr'")
            print("[OK] Added comment_time_format")
        else:
            print("[SKIP] comment_time_format already exists")
        conn.commit()
        print("\n[DONE] Workspace comment_time_format migration completed!")
    except Exception as e:
        print(f"[ERROR] {e}"); conn.rollback(); raise
    finally:
        conn.close()

if __name__ == "__main__":
    migrate()
