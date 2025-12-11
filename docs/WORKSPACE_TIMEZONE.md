# Workspace Timezone Feature

## Overview

The workspace timezone feature allows administrators to set a default timezone for their workspace. All datetime displays throughout the application will be converted from UTC to the selected timezone.

## Features

1. **Timezone Setting**: Admins can select a timezone from Site Settings
2. **Automatic Conversion**: All datetime displays are converted to workspace timezone
3. **Template Filter**: `format_datetime_tz` filter for easy timezone formatting

## Configuration

### Setting the Timezone

1. Navigate to **Admin** → **Site Settings**
2. Find the **Timezone** dropdown under Branding section
3. Select your desired timezone from the list
4. Click **Save Settings**

### Available Timezones

- UTC (Default)
- Africa/Johannesburg (SAST)
- America/New_York (EST/EDT)
- America/Chicago (CST/CDT)
- America/Denver (MST/MDT)
- America/Los_Angeles (PST/PDT)
- Europe/London (GMT/BST)
- Europe/Paris (CET/CEST)
- Asia/Dubai (GST)
- Asia/Kolkata (IST)
- Asia/Shanghai (CST)
- Asia/Tokyo (JST)
- Australia/Sydney (AEDT/AEST)

## Usage in Templates

### Using the Filter

The `format_datetime_tz` filter converts UTC datetime to workspace timezone:

```html
<!-- Basic usage (uses workspace.timezone) -->
{{ ticket.created_at | format_datetime_tz(workspace.timezone) }}

<!-- Custom format -->
{{ ticket.created_at | format_datetime_tz(workspace.timezone, '%b %d, %Y %I:%M %p') }}

<!-- Example outputs -->
{{ ticket.created_at | format_datetime_tz(workspace.timezone, '%Y-%m-%d %H:%M') }}
<!-- Output: 2025-01-04 14:30 -->

{{ ticket.created_at | format_datetime_tz(workspace.timezone, '%b %d, %I:%M %p') }}
<!-- Output: Jan 04, 02:30 PM -->
```

### Before and After

**Before (hardcoded UTC):**
```html
{{ ticket.created_at.strftime('%b %d, %Y %I:%M %p') }}
<!-- Displays: Jan 04, 2025 12:30 PM (UTC) -->
```

**After (workspace timezone):**
```html
{{ ticket.created_at | format_datetime_tz(workspace.timezone, '%b %d, %Y %I:%M %p') }}
<!-- Displays: Jan 04, 2025 02:30 PM (SAST, UTC+2) -->
```

## Deployment

### Database Migration

Run the migration to add the timezone column:

```bash
python migrations/add_workspace_timezone.py
```

### Update Code

1. Pull latest code from repository
2. Run the migration script
3. Restart the server

### Files Modified

- `app/models/workspace.py`: Added `timezone` field
- `app/web/routes.py`: Added timezone parameter to settings route and `format_datetime_tz` filter
- `app/templates/admin/site_settings.html`: Added timezone dropdown
- `migrations/add_workspace_timezone.py`: Migration script

## Technical Details

### Timezone Storage

- Timezone is stored in the `workspace` table
- Default value: `UTC`
- Type: `TEXT` (timezone name like 'Africa/Johannesburg')

### Timezone Conversion

The `format_datetime_tz` function:
1. Takes a UTC datetime
2. Converts it to the specified timezone
3. Formats it according to the format string
4. Returns formatted string

### Python Version Support

- **Python 3.9+**: Uses built-in `zoneinfo` module
- **Python < 3.9**: Falls back to `pytz` library

## Troubleshooting

### "No such column: timezone" Error

Run the migration:
```bash
python migrations/add_workspace_timezone.py
```

### Times Still Showing in UTC

1. Check if timezone is set in Site Settings
2. Ensure templates are using `format_datetime_tz` filter
3. Verify workspace object is passed to template context

### Incorrect Time Display

1. Verify timezone name is correct (e.g., 'Africa/Johannesburg')
2. Check that datetime is stored as UTC in database
3. Ensure `workspace.timezone` is not None

## Example Implementation

### Ticket List Template

```html
<!-- OLD -->
<td>{{ ticket.created_at.strftime('%b %d, %Y %I:%M %p') }}</td>

<!-- NEW -->
<td>{{ ticket.created_at | format_datetime_tz(workspace.timezone, '%b %d, %Y %I:%M %p') }}</td>
```

### Task Detail Template

```html
<!-- OLD -->
<span>Due: {{ task.due_date.strftime('%d/%m/%Y') }}</span>

<!-- NEW -->
<span>Due: {{ task.due_date | format_datetime_tz(workspace.timezone, '%d/%m/%Y') }}</span>
```

## Future Enhancements

Potential improvements:
- Per-user timezone preferences
- Automatic timezone detection based on browser
- Timezone abbreviation display (e.g., "SAST", "EST")
- Calendar integration with timezone support
