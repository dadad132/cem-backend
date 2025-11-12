"""
Test script to create sample notifications for testing the interactive popup system.
Run this to create test notifications in the database.
"""

import sqlite3
from datetime import datetime

def create_test_notifications():
    conn = sqlite3.connect('data.db')
    cursor = conn.cursor()
    
    # Get the first user
    cursor.execute("SELECT id FROM user LIMIT 1")
    user_result = cursor.fetchone()
    
    if not user_result:
        print("❌ No users found in database. Please create a user first.")
        conn.close()
        return
    
    user_id = user_result[0]
    print(f"Creating test notifications for user ID: {user_id}")
    
    # Create test notifications
    test_notifications = [
        {
            'type': 'meeting',
            'message': 'Meeting "Project Kickoff" starts in 15 minutes',
            'related_id': 1,
            'url': '/web/meetings'
        },
        {
            'type': 'task',
            'message': 'You have been assigned to task "Complete Documentation"',
            'related_id': 1,
            'url': '/web/tasks/my'
        },
        {
            'type': 'message',
            'message': 'John Doe: Hey, can you review this? [2 images, 1 document]',
            'related_id': 1,
            'url': '/web/chats/1'
        },
        {
            'type': 'message',
            'message': 'Sarah Smith in Project Team: The deadline has been move...',
            'related_id': 2,
            'url': '/web/chats/2'
        },
        {
            'type': 'message',
            'message': 'Mike Johnson sent 1 video in Design Chat',
            'related_id': 3,
            'url': '/web/chats/3'
        },
        {
            'type': 'comment',
            'message': 'John Doe commented on your task',
            'related_id': 1,
            'url': '/web/tasks/1'
        }
    ]
    
    try:
        for notif in test_notifications:
            cursor.execute("""
                INSERT INTO notification 
                (user_id, type, message, related_id, url, created_at, read_at, dismissed_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)
            """, (
                user_id,
                notif['type'],
                notif['message'],
                notif['related_id'],
                notif['url'],
                datetime.utcnow().isoformat()
            ))
        
        conn.commit()
        print(f"✅ Created {len(test_notifications)} test notifications!")
        print("\n📋 Test notifications:")
        for i, notif in enumerate(test_notifications, 1):
            print(f"  {i}. [{notif['type']}] {notif['message']}")
        
        print("\n💡 Refresh your browser to see the popup notifications!")
        print("   They will auto-dismiss after 1 minute if not acknowledged.")
        
    except Exception as e:
        print(f"❌ Error creating notifications: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    create_test_notifications()
