# Task Archiving System

## Overview
The task archiving system automatically archives completed tasks, removing them from the kanban board while preserving full history in the list view. Non-admin users have read-only access to archived tasks, while admins can reopen and modify them.

## Features Implemented

### 1. Database Schema
**New Fields in Task Model** (`app/models/task.py`):
- `is_archived: bool = Field(default=False)` - Indicates if task is archived
- `archived_at: Optional[datetime] = None` - Timestamp when task was archived

**Migration Script**: `migrate_task_archive.py`
- Adds columns with default values (is_archived=0, archived_at=NULL)
- Creates index on `is_archived` for query performance
- Safe to run multiple times (checks for existing columns)

### 2. Automatic Archiving
**Trigger**: When task status changes to 'done'

**Location**: `app/web/routes.py` - POST `/tasks/{task_id}/status` (line ~1850)

**Logic**:
```python
if status_value == 'done' and not task.is_archived:
    task.is_archived = True
    task.archived_at = datetime.utcnow()
```

### 3. Board View Behavior
**Route**: GET `/projects/{project_id}` (line ~1240)

**Filter**: Archived tasks excluded from kanban board
```python
select(Task).where(Task.project_id == project_id, Task.is_archived == False)
```

**Result**: Only active tasks (todo, in_progress, blocked, done but not yet archived) appear on the board.

### 4. List View Enhancement
**Route**: GET `/tasks/list` (line ~1340)

**Features**:
- **Active Tab**: Shows non-archived tasks (todo, in_progress, blocked)
- **Done Tab**: Shows completed tasks including archived ones
- **Visual Indicator**: Yellow "ARCHIVED" badge appears on archived tasks
- **Full Access**: All task history and comments remain visible

**Template**: `app/templates/tasks/list.html`
```html
{% if task.is_archived %}
<span style="background: #fbbf24; color: #78350f;">🗄️ ARCHIVED</span>
{% endif %}
```

### 5. Task Detail Page Changes
**Template**: `app/templates/tasks/detail.html`

**Non-Admin Users (Archived Task)**:
- Header shows yellow "ARCHIVED" badge
- Warning message: "This task is archived and read-only. Only admins can modify or reopen it."
- Edit Task section: **HIDDEN**
- Comment form: **HIDDEN** with message "Only admins can add comments"
- All existing comments and history: **VISIBLE** (read-only)

**Admin Users (Archived Task)**:
- Header shows yellow "ARCHIVED" badge
- Blue info box with "Reopen Task" button
- Edit Task section: **ENABLED** (admins can edit archived tasks)
- Comment form: **ENABLED** (admins can add comments)
- Full modification access

### 6. Access Control Enforcement

#### Comment Creation
**Route**: POST `/tasks/{task_id}/comment` (line ~1650)

**Check**:
```python
if task.is_archived and not user.is_admin:
    raise HTTPException(status_code=403, detail='This task is archived. Only admins can add comments.')
```

#### Task Update
**Route**: POST `/tasks/{task_id}/update` (line ~1575)

**Check**:
```python
if task.is_archived and not user.is_admin:
    raise HTTPException(status_code=403, detail='This task is archived. Only admins can modify it.')
```

### 7. Reopen Functionality
**Route**: POST `/tasks/{task_id}/reopen` (line ~1985)

**Requirements**:
- **Admin Only**: Non-admins receive 403 Forbidden error
- **Workspace Validation**: Task must belong to user's workspace

**Actions**:
1. Sets `is_archived = False`
2. Sets `archived_at = None`
3. If status is 'done', changes to 'in_progress'
4. Redirects to task detail page

**Result**: Task reappears on kanban board and becomes fully editable by assigned users.

## User Workflows

### For Regular Users
1. **Complete Task**: Change status to 'done'
2. **Auto-Archive**: Task disappears from board after status update
3. **View History**: Access task from list view Done tab
4. **Read-Only**: Can view all details, comments, attachments but cannot edit

### For Admin Users
1. **Same as Regular**: Tasks auto-archive on completion
2. **View Archived**: See tasks in Done tab with ARCHIVED badge
3. **Reopen Option**: Click "Reopen Task" button in task detail
4. **Full Edit**: Can modify archived tasks without reopening
5. **Comment Access**: Can add comments to archived tasks

## Benefits

### Organizational
- **Clean Boards**: Active work remains visible, completed work doesn't clutter the view
- **Audit Trail**: Full history preserved for compliance and review
- **Flexible Workflow**: Admins can reactivate tasks if needed (e.g., issues discovered later)

### Performance
- **Faster Queries**: Board view filters indexed `is_archived` column
- **Reduced Load**: Board view processes fewer records

### Security
- **Protected History**: Non-admins cannot alter completed work
- **Admin Control**: Only authorized users can modify or reopen archived tasks
- **Permission Checks**: Backend enforces access control at API level

## Testing Checklist

### Database
- [x] Migration adds columns successfully
- [x] Indexes created properly
- [x] Default values applied (is_archived=False)

### Board View
- [x] Archived tasks don't appear
- [x] Non-archived done tasks still visible
- [x] Status updates work correctly

### List View
- [x] Active tab excludes archived tasks
- [x] Done tab includes archived tasks
- [x] ARCHIVED badge displays correctly

### Task Detail - Non-Admin
- [x] Warning message shows for archived tasks
- [x] Edit form hidden on archived tasks
- [x] Comment form hidden on archived tasks
- [x] Existing comments visible
- [x] 403 error if trying to edit/comment via API

### Task Detail - Admin
- [x] Reopen button appears for archived tasks
- [x] Edit form enabled for archived tasks
- [x] Comment form enabled for archived tasks

### Reopen Functionality
- [x] Admin can reopen archived tasks
- [x] Non-admin receives 403 error
- [x] Task status changes from done to in_progress
- [x] is_archived and archived_at reset
- [x] Task reappears on board

## Migration Guide

### For Existing Installations

1. **Backup Database**:
   ```powershell
   Copy-Item data.db data.db.backup
   ```

2. **Pull Latest Code**:
   ```bash
   git pull origin main
   ```

3. **Run Migration**:
   ```powershell
   python migrate_task_archive.py
   ```

4. **Restart Server**:
   ```powershell
   python start_server.py
   ```

5. **Verify**:
   - Check board view loads without errors
   - Complete a task and verify it archives
   - Check Done tab shows archived tasks
   - Test reopen functionality as admin

### Rollback (If Needed)

If issues occur, restore backup:
```powershell
Stop-Process -Name python -Force  # Stop server
Remove-Item data.db
Rename-Item data.db.backup data.db
python start_server.py
```

## Technical Notes

### SQLite Boolean Storage
- SQLite stores booleans as INTEGER (0 or 1)
- SQLModel/SQLAlchemy handles type conversion automatically
- Queries use `is_archived == False` or `is_archived == True`

### Cascade Behavior
- Archiving does NOT delete data
- All related records (comments, assignments, attachments) remain intact
- Only visibility changes based on `is_archived` flag

### Index Performance
```sql
CREATE INDEX ix_task_is_archived ON task (is_archived)
```
- Speeds up board view filtering
- Minimal storage overhead (single boolean column)

### Backward Compatibility
- Existing tasks have `is_archived=False` by default
- No changes needed to existing code except board filtering
- Migration is additive (no data loss)

## Future Enhancements

### Potential Additions
1. **Bulk Archive**: Archive multiple completed tasks at once
2. **Auto-Archive Delay**: Wait X days before auto-archiving done tasks
3. **Archive Dashboard**: Admin view of all archived tasks with statistics
4. **Restore History**: Log who reopened tasks and when
5. **Permanent Delete**: Admin option to delete archived tasks after retention period

### Configuration Options (Not Implemented)
```python
# app/core/config.py
AUTO_ARCHIVE_DONE_TASKS: bool = True
ARCHIVE_DELAY_DAYS: int = 0  # 0 = immediate
SHOW_ARCHIVED_TO_NON_ADMINS: bool = True
ALLOW_TASK_REOPEN: bool = True  # Admin only
```

## Related Files

### Models
- `app/models/task.py` - Task model with archiving fields

### Routes
- `app/web/routes.py`:
  - GET `/projects/{project_id}` - Board view with filtering
  - POST `/tasks/{task_id}/status` - Auto-archive trigger
  - POST `/tasks/{task_id}/update` - Edit permission check
  - POST `/tasks/{task_id}/comment` - Comment permission check
  - POST `/tasks/{task_id}/reopen` - Reopen functionality
  - GET `/tasks/list` - List view with tab filtering

### Templates
- `app/templates/tasks/detail.html` - Task detail with read-only enforcement
- `app/templates/tasks/list.html` - List view with ARCHIVED badge
- `app/templates/projects/detail.html` - Kanban board (filters archived)

### Migration
- `migrate_task_archive.py` - Database migration script

## Support

For issues or questions:
1. Check server logs for error messages
2. Verify migration completed successfully
3. Ensure user has appropriate admin permissions
4. Check browser console for JavaScript errors
5. Verify database has new columns: `SELECT is_archived, archived_at FROM task LIMIT 1;`
