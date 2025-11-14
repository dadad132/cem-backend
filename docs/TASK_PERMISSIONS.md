# Task Permissions System

## Overview

Task editing permissions ensure that only authorized users can modify tasks. The system enforces two levels of access:

1. **Admins** - Full control over all tasks
2. **Assigned Users** - Can only edit tasks they're assigned to

## Permission Rules

### What Admins Can Do
✅ Edit any task (title, description, status, priority, dates/times)  
✅ Update task status  
✅ Assign users to tasks  
✅ Unassign users from tasks  
✅ Delete tasks (API only)  
✅ Create tasks in any project  

### What Assigned Users Can Do
✅ Edit tasks assigned to them (title, description, status, priority, dates/times)  
✅ Update status of their assigned tasks  
❌ Assign/unassign other users  
❌ Delete tasks  
❌ Edit tasks not assigned to them  

### What Unassigned Users Cannot Do
❌ Edit any aspect of tasks not assigned to them  
❌ Update status of unassigned tasks  
❌ View tasks from projects they're not members of  

## Protected Routes

### Web Routes (app/web/routes.py)

#### POST /web/tasks/{task_id}/update
**Permission:** Admin OR Assigned User  
**Action:** Update task details (title, description, status, priority, dates)  
**Error:** 403 if user is not admin and not assigned  

#### POST /web/tasks/{task_id}/status
**Permission:** Admin OR Assigned User  
**Action:** Update task status only  
**Error:** 403 if user is not admin and not assigned  

#### POST /web/tasks/{task_id}/assign
**Permission:** Admin ONLY  
**Action:** Assign a user to a task  
**Error:** 403 if user is not admin  

#### POST /web/tasks/{task_id}/unassign
**Permission:** Admin ONLY  
**Action:** Remove user assignment from task  
**Error:** 403 if user is not admin  

#### POST /web/tasks/create
**Permission:** Admin OR Project Member  
**Action:** Create new task in a project  
**Error:** 403 if user is not admin and not assigned to project  

### API Routes (app/api/routes/tasks.py)

#### PATCH /api/tasks/{task_id}
**Permission:** Admin OR Assigned User  
**Action:** Update task fields  
**Error:** 403 if user is not admin and not assigned  

#### DELETE /api/tasks/{task_id}
**Permission:** Admin ONLY  
**Action:** Delete a task  
**Error:** 403 if user is not admin  

## Permission Checks

### Check for Assigned User
```python
if not user.is_admin:
    from app.models.assignment import Assignment
    assignment = (await db.execute(
        select(Assignment).where(
            Assignment.task_id == task_id,
            Assignment.assignee_id == user_id
        )
    )).scalar_one_or_none()
    if not assignment:
        raise HTTPException(status_code=403, detail='You can only edit tasks assigned to you')
```

### Check for Admin Only
```python
if not user.is_admin:
    raise HTTPException(status_code=403, detail='Only admins can assign tasks')
```

### Check for Project Access
```python
if not user.is_admin:
    from app.models.project_member import ProjectMember
    member = (await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id
        )
    )).scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=403, detail='You do not have access to this project')
```

## User Experience

### For Assigned Users
1. Can see tasks assigned to them in task lists
2. Can click into task details
3. Can edit task fields (update form shows)
4. Can change task status (via dropdown or drag-drop)
5. Cannot assign/unassign other users (no buttons visible)
6. Gets 403 error if trying to edit unassigned tasks

### For Admins
1. See all tasks in workspace
2. Full edit capabilities on all tasks
3. Can assign/unassign any user to any task
4. Can manage project memberships
5. Can delete tasks
6. No restrictions

### Error Messages

**403 Forbidden Errors:**
- "You can only edit tasks assigned to you" - Non-admin trying to edit unassigned task
- "You can only update tasks assigned to you" - Non-admin trying to change status of unassigned task
- "Only admins can assign tasks" - Non-admin trying to assign users
- "Only admins can unassign tasks" - Non-admin trying to unassign users
- "Only admins can delete tasks" - Non-admin trying to delete task
- "You do not have access to this project" - Non-admin trying to access unassigned project

## Integration with Project Assignment System

The task permission system works in conjunction with the project assignment system:

1. **Project Access** → User must be assigned to project to see its tasks
2. **Task Assignment** → User must be assigned to task to edit it
3. **Admin Override** → Admins bypass both checks

### Permission Hierarchy
```
Admin
  ├─ Can access ALL projects
  ├─ Can edit ALL tasks
  └─ Can manage ALL assignments

Project Member (Non-Admin)
  ├─ Can access assigned projects
  ├─ Can see all tasks in assigned projects
  ├─ Can only EDIT tasks assigned to them
  └─ Cannot manage assignments

Non-Member
  └─ Cannot access project or its tasks (404/403)
```

## Testing Checklist

### As Admin
- [ ] Can edit any task
- [ ] Can change status of any task
- [ ] Can assign users to tasks
- [ ] Can unassign users from tasks
- [ ] Can create tasks in any project
- [ ] Can delete tasks

### As Assigned User
- [ ] Can edit own assigned tasks
- [ ] Can change status of assigned tasks
- [ ] Cannot edit unassigned tasks (gets 403)
- [ ] Cannot assign other users (gets 403)
- [ ] Cannot unassign users (gets 403)
- [ ] Cannot delete tasks (gets 403)

### As Project Member (Not Assigned to Task)
- [ ] Can see task in project view
- [ ] Cannot edit task (gets 403)
- [ ] Cannot change task status (gets 403)

### As Non-Project Member
- [ ] Cannot see project (filtered from list)
- [ ] Cannot access project details (gets 403)
- [ ] Cannot see tasks from project

## Files Modified

### Route Files
- `app/web/routes.py` - Added permission checks to 4 task modification routes
- `app/api/routes/tasks.py` - Added permission checks to PATCH and DELETE routes

### No Template Changes Needed
- Permission checks happen server-side
- Existing UI works correctly
- 403 errors displayed to user if they try unauthorized actions

## Security Notes

1. **Server-Side Enforcement** - All checks done on backend, cannot be bypassed
2. **Database-Level Checks** - Queries verify assignment before allowing updates
3. **Multi-Layer Security** - Project access + task assignment + admin checks
4. **Audit Trail** - TaskHistory tracks who made changes
5. **Workspace Isolation** - All checks include workspace_id verification

## Future Enhancements

Possible additions:
- Task ownership (creator has special permissions)
- Read-only task viewers
- Custom permission roles (editor, reviewer, etc.)
- Team-level permissions
- Time-based access (task only editable during certain hours)
- Approval workflows (changes require admin approval)
