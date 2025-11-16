"""
Rebuild user table with correct column order
This will preserve all your data
"""
import sqlite3
import sys

def rebuild_user_table():
    try:
        conn = sqlite3.connect('data.db')
        cursor = conn.cursor()
        
        print("Step 1: Creating backup of user table...")
        cursor.execute("""
            CREATE TABLE user_backup AS 
            SELECT * FROM user
        """)
        
        print("Step 2: Dropping old user table...")
        cursor.execute("DROP TABLE user")
        
        print("Step 3: Creating new user table with correct schema...")
        cursor.execute("""
            CREATE TABLE user (
                username VARCHAR NOT NULL,
                email VARCHAR,
                full_name VARCHAR,
                is_active BOOLEAN NOT NULL DEFAULT 1,
                is_admin BOOLEAN NOT NULL DEFAULT 0,
                preferred_meeting_platform VARCHAR,
                profile_completed BOOLEAN NOT NULL DEFAULT 0,
                profile_picture VARCHAR,
                calendar_color VARCHAR DEFAULT '#3b82f6',
                can_see_all_tickets BOOLEAN NOT NULL DEFAULT 0,
                id INTEGER NOT NULL PRIMARY KEY,
                hashed_password VARCHAR NOT NULL,
                workspace_id INTEGER,
                email_verified BOOLEAN NOT NULL DEFAULT 0,
                verification_code VARCHAR,
                verification_expires_at DATETIME,
                google_id VARCHAR,
                google_access_token VARCHAR,
                google_refresh_token VARCHAR,
                google_token_expiry DATETIME,
                FOREIGN KEY(workspace_id) REFERENCES workspace (id)
            )
        """)
        
        print("Step 4: Creating indexes...")
        cursor.execute("CREATE UNIQUE INDEX ix_user_username ON user (username)")
        cursor.execute("CREATE INDEX ix_user_email ON user (email)")
        cursor.execute("CREATE INDEX ix_user_google_id ON user (google_id)")
        
        print("Step 5: Copying data back...")
        # Get column names from backup
        cursor.execute("PRAGMA table_info(user_backup)")
        backup_columns = [row[1] for row in cursor.fetchall()]
        
        # Only copy columns that exist in both tables
        columns_to_copy = [
            'username', 'email', 'full_name', 'is_active', 'is_admin',
            'preferred_meeting_platform', 'profile_completed', 'id', 
            'hashed_password', 'workspace_id', 'email_verified',
            'verification_code', 'verification_expires_at', 'google_id',
            'google_access_token', 'google_refresh_token', 'google_token_expiry'
        ]
        
        # Add optional columns if they exist
        if 'profile_picture' in backup_columns:
            columns_to_copy.append('profile_picture')
        if 'calendar_color' in backup_columns:
            columns_to_copy.append('calendar_color')
        if 'can_see_all_tickets' in backup_columns:
            columns_to_copy.append('can_see_all_tickets')
        
        columns_str = ', '.join(columns_to_copy)
        cursor.execute(f"""
            INSERT INTO user ({columns_str})
            SELECT {columns_str} FROM user_backup
        """)
        
        rows_copied = cursor.rowcount
        print(f"✅ Copied {rows_copied} users")
        
        print("Step 6: Dropping backup table...")
        cursor.execute("DROP TABLE user_backup")
        
        conn.commit()
        
        print("\n✅ User table rebuilt successfully!")
        
        # Verify
        cursor.execute("SELECT COUNT(*) FROM user")
        count = cursor.fetchone()[0]
        print(f"Total users in database: {count}")
        
        cursor.execute("PRAGMA table_info(user)")
        columns = [f"{row[1]} ({row[2]})" for row in cursor.fetchall()]
        print(f"\nFinal schema:")
        for col in columns:
            print(f"  - {col}")
        
        conn.close()
        return True
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        
        # Try to restore from backup
        try:
            print("\nAttempting to restore from backup...")
            cursor.execute("DROP TABLE IF EXISTS user")
            cursor.execute("ALTER TABLE user_backup RENAME TO user")
            conn.commit()
            print("✅ Restored from backup")
        except:
            print("❌ Could not restore - please restore data.db from a backup!")
        
        conn.close()
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("  USER TABLE REBUILD")
    print("=" * 60)
    print("\nThis will rebuild the user table with correct column order.")
    print("Your data will be preserved.\n")
    
    response = input("Continue? (yes/no): ")
    if response.lower() != 'yes':
        print("Cancelled.")
        sys.exit(0)
    
    success = rebuild_user_table()
    sys.exit(0 if success else 1)
