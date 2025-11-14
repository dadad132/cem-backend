# Calendar User Colors & Project Date Range Feature

## Overview
This feature adds user-specific colors to the calendar and enables projects to be displayed based on their start and due dates.

## Features Implemented

### 1. User Calendar Colors
- Each user has a unique color (`calendar_color`) stored in the database
- Users can select their calendar color from their profile settings
- Tasks and projects assigned to users are displayed in their color on the calendar
- Admins see a color legend showing which color belongs to which user

### 2. Project Date Ranges
- Projects now have `start_date` and `due_date` fields
- Projects are displayed on the calendar spanning their entire date range
- Projects appear on all days between their start and due dates
- Non-admin users see only projects they're members of

### 3. Color Legend for Admins
- Admins see a user color legend at the top of the calendar
- Shows which user is assigned which color
- Helps identify who is assigned to which tasks/projects at a glance

## Database Changes

### User Table
- Added `calendar_color` field (TEXT, default: '#3B82F6')
  - Stores hex color code for the user's calendar items

### Project Table
- Added `start_date` field (DATE, nullable)
  - When the project starts
- Added `due_date` field (DATE, nullable)
  - When the project is due
  - Projects are displayed on calendar between start_date and due_date

## Files Modified

### Models
1. **app/models/user.py**
   - Added `calendar_color` field to UserBase
   - Added `calendar_color` to UserUpdate schema

2. **app/models/project.py**
   - Added `start_date` and `due_date` fields to ProjectBase
   - Added these fields to ProjectCreate and ProjectUpdate schemas

### Backend Routes
3. **app/web/routes.py**
   - Updated calendar route to fetch projects with date ranges
   - Added logic to fetch user assignments for tasks and projects
   - Added workspace users list for admin color legend
   - Updated profile route to accept and save calendar_color

### Templates
4. **app/templates/calendar/index.html**
   - Added user color legend for admins
   - Added project icon to legend
   - Modified day/week/month views to display projects
   - Applied user colors to tasks and projects
   - Projects span their entire date range in the calendar

5. **app/templates/auth/profile.html**
   - Added color picker input for calendar color selection
   - Users can now choose their personal calendar color

### Migration
6. **migrate_calendar_features.py**
   - New migration script to add database columns
   - Assigns unique colors to existing users automatically

## Usage

### For Users
1. **Set Your Calendar Color:**
   - Go to Profile (click your name in top-right)
   - Scroll to "Calendar Color" field
   - Pick your preferred color
   - Click "Save Changes"

2. **View Calendar:**
   - Your tasks and projects will appear in your chosen color
   - See tasks on their due dates
   - See projects spanning their start to due dates

### For Admins
1. **Manage Projects:**
   - When creating/editing a project, set start_date and due_date
   - Projects will appear on calendar for the entire date range

2. **View All Users:**
   - See the user color legend at top of calendar
   - Quickly identify which user owns which tasks/projects
   - View all workspace items across all users

### For Project Managers
- When creating projects, specify start_date and due_date
- Projects appear as colored bars across the date range
- Click on any project to view details

## Color System

### Default Colors Assigned
The migration automatically assigns these colors to existing users:
- Blue (#3B82F6)
- Red (#EF4444)
- Green (#10B981)
- Amber (#F59E0B)
- Violet (#8B5CF6)
- Pink (#EC4899)
- Teal (#14B8A6)
- Orange (#F97316)
- Indigo (#6366F1)
- Lime (#84CC16)
- Cyan (#06B6D4)
- Rose (#F43F5E)
- Purple (#A855F7)
- And more...

### Visual Indicators
- Tasks: Use user's color with 30% opacity background
- Projects: Use project owner's color with 20% opacity background
- Priority still shown via emoji icons (🔥⬆️➡️⬇️)
- Meetings: Purple color (unchanged)
- Overdue items: Gray (overrides user color)

## Installation

Run the migration script to add the new database fields:

```bash
python migrate_calendar_features.py
```

This will:
1. Add `calendar_color` column to user table
2. Add `start_date` and `due_date` columns to project table
3. Assign unique colors to all existing users

## Technical Details

### Calendar Display Logic
- **Day View:** Shows all tasks, meetings, and projects for a single day
- **Week View:** Shows 7 days with items in compact form
- **Month View:** Shows entire month with truncated item display

### Project Date Range Display
Projects appear on every day between `start_date` and `due_date` (inclusive):
```python
# In template logic
if day >= project.start_date and day <= project.due_date:
    display_project(day)
```

### User Color Resolution
- Tasks: Use color of first assigned user
- Projects: Use color of project owner
- If no user found: Use default green (#10B981)

## Benefits

1. **Visual Organization:** Quickly identify who's working on what
2. **Project Timeline:** See entire project duration at a glance
3. **Personalization:** Each user can choose their preferred color
4. **Admin Oversight:** Color legend helps admins track team workload
5. **Better Planning:** Project date ranges help with resource planning

## Future Enhancements

Potential improvements:
- Color schemes/themes for better accessibility
- Multiple assignees with gradient colors
- Color categories for different project types
- Export calendar with color coding
- Mobile-optimized color legend

## Support

For issues or questions:
- Check that migration ran successfully
- Ensure users have set their calendar colors
- Verify projects have both start_date and due_date set
- Admin users should see the color legend
