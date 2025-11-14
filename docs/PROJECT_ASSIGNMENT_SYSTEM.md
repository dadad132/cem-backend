# Project Assignment System

## Overview

The project assignment system allows admins to control which users can access which projects. This is perfect for scenarios where:
- Multiple companies/clients have separate projects
- You want to isolate work between different teams
- Users should only see projects relevant to them

## How It Works

### Database Structure

**ProjectMember Table** (`project_member`)
- Links users to projects in a many-to-many relationship
- One user can be assigned to multiple projects
- One project can have multiple users assigned
- Tracks when and by whom the assignment was made

### Access Control Rules

1. **Admins**
   - See ALL projects in their workspace
   - Can access any project
   - Can manage project members (assign/remove users)
   - Auto-assigned to projects they create

2. **Regular Users**
   - Only see projects they're assigned to
   - Can only access tasks within their assigned projects
   - Cannot see or access projects they're not assigned to
   - Cannot manage project members

### Key Features

#### 1. Project Listing (`/web/projects`)
- **Admin view**: Shows all workspace projects
- **User view**: Shows only assigned projects
- Admin users see "Manage Members" link on each project card

#### 2. Project Details (`/web/projects/{id}`)
- Users can only view projects they're assigned to
- 403 error if non-admin tries to access unassigned project
- Admin users see "Manage Members" button in header

#### 3. Project Member Management (`/web/projects/{id}/members`)
- **Admin only** - regular users get 403 error
- Shows list of currently assigned members
- Dropdown to add new members from workspace
- Remove button to unassign users

#### 4. Task Views
- Task lists only show tasks from projects user has access to
- Project filter dropdown only shows accessible projects
- Users cannot create tasks in projects they're not assigned to

#### 5. Auto-Assignment
- When a user creates a project, they're automatically assigned to it
- Project creator can access their project even if not admin

## Usage Examples

### Scenario 1: Multiple Company Projects

**Setup:**
```
Company A Project → Assign: Alice, Bob
Company B Project → Assign: Charlie, Diana
Company C Project → Assign: Eve
```

**Result:**
- Alice and Bob only see Company A tasks
- Charlie and Diana only see Company B tasks
- Eve only sees Company C tasks
- Admin sees everything and manages assignments

### Scenario 2: Team-Based Projects

**Setup:**
```
Frontend Project → Assign: All frontend developers
Backend Project → Assign: All backend developers
DevOps Project → Assign: DevOps team
```

**Result:**
- Developers only see projects relevant to their specialty
- Clear separation of concerns
- Admins can reassign people between teams as needed

## Admin Workflow

### Assigning Users to a Project

1. Navigate to Projects page (`/web/projects`)
2. Click "Manage Members" on desired project (or open project and click button in header)
3. Select user from dropdown
4. Click "Add to Project"
5. User immediately gains access to the project

### Removing Users from a Project

1. Go to project members page
2. Click "Remove" next to user's name
3. Confirm removal
4. User immediately loses access to the project and its tasks

### Creating a Project

1. Click "New Project" button
2. Fill in name and description
3. Submit - you're automatically assigned as a member
4. Assign additional users via "Manage Members"

## Database Migration

### On Server Start

The system automatically detects if the `project_member` table is missing and rebuilds the database schema if needed. This is handled in `app/core/database.py`.

### Existing Projects

**Important:** When you first deploy this feature:
- Existing projects have NO members assigned
- Admins need to manually assign users to existing projects
- Until assigned, regular users won't see existing projects

**Migration Steps:**
1. Restart server to create `project_member` table
2. As admin, visit each existing project
3. Click "Manage Members" and assign appropriate users
4. Verify users can now see their projects

## API Integration

The project member system is integrated throughout:

### Web Routes
- `/web/projects` - Filtered project listing
- `/web/projects/{id}` - Access control check
- `/web/projects/{id}/members` - Member management UI
- `/web/projects/{id}/members/add` - Add member endpoint
- `/web/projects/{id}/members/{user_id}/remove` - Remove member endpoint
- `/web/tasks/list` - Only shows tasks from accessible projects
- `/web/tasks/create` - Validates project access before creating task

### Database Queries
All project-related queries now include:
- Workspace filtering (existing)
- Project member filtering (new for non-admins)
- Admin bypass (admins see everything)

## Security Notes

- All routes verify user is logged in
- Member management requires admin role
- Project access is checked before viewing/modifying
- SQL injection prevented by using SQLAlchemy ORM
- No direct user input in queries

## Files Modified

### New Files
- `app/models/project_member.py` - ProjectMember model
- `app/templates/projects/members.html` - Member management UI
- `PROJECT_ASSIGNMENT_SYSTEM.md` - This documentation

### Modified Files
- `app/models/__init__.py` - Added ProjectMember import
- `app/core/database.py` - Added schema drift check for project_member table
- `app/web/routes.py` - Updated all project/task routes with access control
- `app/templates/projects/index.html` - Added "Manage Members" link for admins
- `app/templates/projects/detail.html` - Added "Manage Members" button for admins

## Troubleshooting

### Users can't see any projects
- Check if users are assigned to projects
- Admin can assign them via "Manage Members"

### Admin can't access member management
- Verify user has `is_admin = 1` in database
- Check session is valid

### Database errors on startup
- Server auto-rebuilds schema if `project_member` table missing
- Backup data before first restart with this feature

### Tasks not showing in list view
- Regular users only see tasks from assigned projects
- Admins see all tasks
- Check project assignments if tasks are missing

## Future Enhancements

Possible additions:
- Bulk user assignment (assign multiple users at once)
- Project roles (viewer, contributor, manager)
- Assignment notifications (email when assigned to project)
- Project templates with default member assignments
- Analytics on project membership
