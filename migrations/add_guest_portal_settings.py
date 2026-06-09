"""Add guest portal branding columns to workspace table."""
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
        added = []
        for col, definition in [
            ('guest_portal_company_name', 'TEXT'),
            ('guest_portal_tagline',      'TEXT'),
            ('guest_portal_phone',        'TEXT'),
            ('guest_portal_email',        'TEXT'),
        ]:
            if col not in columns:
                cursor.execute(f"ALTER TABLE workspace ADD COLUMN {col} {definition}")
                added.append(col)
            else:
                print(f"[SKIP] {col} already exists")
        conn.commit()
        if added:
            print(f"[OK] Added columns: {', '.join(added)}")
        print("[DONE] Guest portal migration complete!")
    except Exception as e:
        print(f"[ERROR] {e}"); conn.rollback(); raise
    finally:
        conn.close()

if __name__ == "__main__":
    migrate()
