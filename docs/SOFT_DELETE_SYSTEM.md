# Soft Delete System for Users

## Overview
Instead of permanently deleting users from the database, the system now uses **soft delete** to maintain data integrity and preserve audit trails.

## How It Works

### When an Admin Deletes a User:

1. **User Record is Preserved**
   - The user row remains in the database
   - `deleted_at` field is set to the current timestamp
   - `deleted_by` field records which admin performed the deletion
   - `is_active` is set to `false`
   - Username is anonymized to prevent conflicts: `deleted_user_{id}_{timestamp}`
   - Email is cleared for privacy

2. **Data Integrity is Maintained**
   - All comments, tasks, assignments, and activity logs created by the user remain intact
   - Foreign key relationships stay valid
   - Audit trail is preserved (who created what, when)

3. **Access is Revoked**
   - User cannot log in (checked during authentication)
   - User doesn't appear in user lists
   - User cannot be assigned to new projects or tasks

### What Gets Preserved:

✅ **Comments** - All comments remain with original authorship  
✅ **Tasks** - Tasks created by the user remain  
✅ **Task Assignments** - Historical task assignments  
✅ **Project Membership** - Historical project membership records  
✅ **Activity Logs** - All actions performed by the user  
✅ **Task History** - Edit history and changes  
✅ **Attachments** - Files uploaded by the user  
✅ **Time Logs** - Time tracking entries  
✅ **Chat Messages** - Messages sent by the user  
✅ **Meeting Records** - Meetings organized/attended  

### Security & Privacy:

- Deleted users are filtered from:
  - User list views
  - Project member dropdowns
  - Task assignment dropdowns
  - Active user counts
  
- Login attempts show: "Your account has been deleted. Please contact your administrator."

- Username is anonymized but user ID is preserved for data relationships

## Database Schema

### User Table Fields:
```sql
deleted_at DATETIME DEFAULT NULL      -- When the user was deleted
deleted_by INTEGER DEFAULT NULL       -- Which admin deleted the user (references user.id)
```

### Indexes:
- `ix_user_deleted_at` - For fast filtering of active vs deleted users

## Implementation Details

### Filtering Deleted Users:
```python
# Exclude soft-deleted users in queries
users = await db.execute(
    select(User)
    .where(User.workspace_id == workspace_id)
    .where(User.deleted_at == None)  # Only active users
)
```

### Authentication Check:
```python
# Prevent deleted users from logging in
if user.deleted_at is not None:
    return error("Your account has been deleted")
```

### Soft Delete Operation:
```python
# Mark user as deleted (not removing from database)
target_user.deleted_at = datetime.utcnow()
target_user.deleted_by = current_admin_id
target_user.is_active = False
target_user.username = f"deleted_user_{user_id}_{int(datetime.utcnow().timestamp())}"
target_user.email = None
```

## Benefits Over Hard Delete

1. **Data Integrity**
   - No broken foreign key relationships
   - No orphaned records
   - Complete audit trail

2. **Recoverability**
   - Can restore deleted users if needed
   - Historical data remains queryable
   - Easier to investigate issues

3. **Compliance**
   - Maintains record of who did what
   - Preserves audit logs
   - Better for regulatory requirements

4. **Simplicity**
   - No complex cascade deletes
   - No need for "deleted user" placeholders
   - Easier backup/restore

## Comparison to Other Approaches

### ❌ Separate User Database:
- **Complexity**: Two databases to manage, sync, backup
- **Queries**: Cross-database joins are slow/complex
- **Integrity**: Hard to maintain referential integrity
- **Backups**: Must coordinate backups across databases

### ❌ Hard Delete with Cascades:
- **Data Loss**: Permanently loses audit trail
- **History**: Can't see who created what
- **Forensics**: Impossible to investigate past issues
- **Compliance**: May violate audit requirements

### ✅ Soft Delete (Current Implementation):
- **Simple**: Single database, simple queries
- **Safe**: Data is never lost
- **Fast**: No cascade operations
- **Compliant**: Full audit trail

## Migration

The soft delete fields were added via migration:
```bash
python migrate_soft_delete.py
```

This adds:
- `deleted_at` column (DATETIME, nullable, indexed)
- `deleted_by` column (INTEGER, nullable, foreign key to user.id)

## Future Enhancements

Possible additions:
1. **Restore functionality** - Allow admins to undelete users
2. **Automatic cleanup** - Archive deleted users after X months
3. **Anonymization** - Replace user data with generic values after deletion
4. **Deleted user view** - Show admins list of deleted users with restore option
