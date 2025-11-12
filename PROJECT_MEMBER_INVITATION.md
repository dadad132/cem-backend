# Project Member Invitation System

## Overview

Admins can now invite users to projects by entering their **username** or **email address**, making it easier to add team members who have already created accounts but aren't yet assigned to specific projects.

## How It Works

### For Admin Users

1. **Navigate to Project Members**
   - Go to Projects page (`/web/projects`)
   - Click "Manage Members" on any project
   - Or open a project and click "Manage Members" in the header

2. **Invite a User**
   - Enter the user's **username** or **email address** in the "Username or Email" field
   - Click "Add to Project"
   - The system will:
     - Search for the user in your workspace
     - Verify they're not already assigned
     - Add them to the project immediately
     - Show a success/error message

3. **Quick Select**
   - Below the invite form, you'll see a list of available users
   - Click any user to auto-fill their username
   - Makes it faster to add users you can see in the list

### User Search

The system searches for users by:
- **Username**: Exact match (case-sensitive)
- **Email**: Exact match (case-insensitive)
- **Workspace**: Only users in the same workspace

### Feedback Messages

**Success**: "Successfully added [User Name] to the project"  
**Already Assigned**: "User [User Name] is already assigned to this project"  
**Not Found**: "User with username or email '[identifier]' not found in your workspace"

## Features

✅ **Username or Email Search**: Flexible user identification  
✅ **Instant Feedback**: Success/error messages after each action  
✅ **Quick Select**: Click to auto-fill from available users  
✅ **Visual Preview**: Shows user info before adding  
✅ **Duplicate Prevention**: Won't add users twice  
✅ **Workspace Isolation**: Only finds users in your workspace

## User Requirements

For a user to be invited, they must:
1. Have an active account in the system
2. Be part of the same workspace as the admin
3. Have `is_active = True`
4. Not already be assigned to the project

## Example Workflows

### Scenario 1: New Employee Joins
1. Employee creates account and logs in once
2. Admin receives notification or checks user list
3. Admin goes to relevant projects
4. Enters employee's email: `john.doe@company.com`
5. Employee immediately gets access to assigned projects

### Scenario 2: Cross-Team Assignment
1. Developer from Frontend team needs to help Backend
2. Admin opens Backend project members page
3. Types developer's username: `john_dev`
4. Developer now sees Backend project in their project list

### Scenario 3: Bulk Assignment
1. Admin opens project members page
2. Uses quick select list to rapidly add multiple team members
3. Each click auto-fills the username
4. Quick succession of "Add to Project" clicks

## Technical Details

### Backend Changes

**Route**: `POST /web/projects/{project_id}/members/add`

**Parameters**:
- `user_identifier` (string): Username or email of user to invite

**Logic**:
```python
# Search by username OR email in same workspace
target_user = await db.execute(
    select(User).where(
        User.workspace_id == current_user.workspace_id,
        (User.username == user_identifier) | (User.email == user_identifier)
    )
)
```

**Response**:
- Redirect to members page with flash message
- Success/error/info stored in session

### Frontend Changes

**Template**: `app/templates/projects/members.html`

**Features**:
- Text input for username/email
- JavaScript helper to auto-fill from quick select
- Visual preview of selected user
- Flash message display (success/error/info)

### Database

No schema changes required - uses existing:
- `User` table: username, email, workspace_id
- `ProjectMember` table: project_id, user_id, assigned_by

## Security

✅ **Admin Only**: Only users with `is_admin=True` can invite  
✅ **Workspace Isolation**: Can only invite users from same workspace  
✅ **Authentication Required**: Must be logged in  
✅ **Project Verification**: Validates project exists in workspace  
✅ **SQL Injection**: Protected via SQLAlchemy ORM

## Error Handling

| Scenario | Response |
|----------|----------|
| User not found | Error message with identifier shown |
| User already assigned | Info message, no database change |
| Invalid project | 404 error |
| Non-admin access | 403 Forbidden |
| Empty identifier | Form validation error |

## Testing

### Manual Test Cases

1. **Valid Username**
   ```
   Input: "admin"
   Expected: User added successfully
   ```

2. **Valid Email**
   ```
   Input: "user@example.com"
   Expected: User added successfully
   ```

3. **Non-existent User**
   ```
   Input: "nonexistent@fake.com"
   Expected: Error message shown
   ```

4. **Already Assigned**
   ```
   Input: Username of already assigned user
   Expected: Info message, no duplicate
   ```

5. **Quick Select**
   ```
   Action: Click user from quick select list
   Expected: Input auto-filled, can submit
   ```

## Best Practices

1. **Use Email for New Users**: More recognizable than usernames
2. **Check Quick Select First**: Faster than typing
3. **Watch for Feedback**: Always check success/error messages
4. **Bulk Operations**: Use quick select for multiple adds
5. **Communication**: Inform users when you add them to projects

## Future Enhancements

Potential improvements:
- [ ] Autocomplete dropdown while typing
- [ ] Fuzzy search (partial matches)
- [ ] Bulk invite (multiple users at once)
- [ ] Email notification to invited users
- [ ] Recently added users list
- [ ] Import users from CSV

## Support

If users can't be found:
1. Verify user has an account (check admin users list)
2. Confirm user is in the same workspace
3. Check username/email spelling (case matters for username)
4. Ensure user account is active (`is_active = True`)

## Related Documentation

- `PROJECT_ASSIGNMENT_SYSTEM.md` - Overall project assignment system
- `TASK_PERMISSIONS.md` - Task-level permissions
- User account creation and management

---

**Last Updated**: November 1, 2025  
**Feature Status**: ✅ Active  
**Admin Only**: Yes
