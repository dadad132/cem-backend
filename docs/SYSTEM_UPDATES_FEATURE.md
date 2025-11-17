# System Update & Rollback Feature

## Overview
New system update management feature added to the admin panel that allows you to:
- View current system version (Git commit)
- See version history (last 30 commits from GitHub)
- Update to the latest version from GitHub
- Rollback to any previous version
- Automatic backups before updates/rollbacks
- Automatic service restart after changes

## How to Use

### Access the Updates Page
1. Login as admin
2. Click "System Updates" in the left sidebar
3. You'll see:
   - Current version information (commit hash, message, date, branch)
   - Update to latest button
   - Version history with all recent commits

### Update to Latest Version
1. Click "🔄 Update to Latest" button
2. Confirm the update
3. System will:
   - Create a full backup (database + attachments)
   - Pull latest code from GitHub
   - Update Python dependencies
   - Restart the service automatically
4. Refresh your browser after the update completes

### Rollback to Previous Version
1. Find the version you want in the version history
2. Click the "↩️ Rollback" button next to that commit
3. Confirm the rollback
4. System will:
   - Create a full backup (database + attachments)
   - Reset code to that specific commit
   - Update Python dependencies
   - Restart the service automatically
5. Refresh your browser

## Features

### Version History
- Shows last 30 commits from GitHub
- Each commit shows:
  - Commit hash (short version)
  - Commit message
  - Author name
  - Date and time
  - Link to view on GitHub
- Current version is highlighted in blue
- Can rollback to any commit except the current one

### Safety Features
- **Automatic Backups**: Full backup (DB + uploads) created before every update/rollback
- **Manual Backups**: All update backups are marked as "MANUAL" so they won't be auto-deleted
- **Dependency Management**: Python packages automatically updated when changing versions
- **Service Restart**: Systemd service automatically restarts after updates

### Requirements
- Git installed and repository initialized
- Internet connection to fetch from GitHub
- Systemd service configured (crm-backend.service)
- Admin privileges

## Technical Details

### Files Added
- `app/core/update_manager.py` - Core update management logic
- Admin routes in `app/web/routes.py`:
  - `/web/admin/updates` - View updates page
  - `/web/admin/updates/latest` - Update to latest
  - `/web/admin/updates/rollback` - Rollback to specific commit

### Update Process
1. Fetch latest changes: `git fetch origin`
2. Check what's new: `git log HEAD..origin/main`
3. Create backup
4. Reset to target: `git reset --hard <commit>`
5. Update dependencies (if venv exists)
6. Restart service: `sudo systemctl restart crm-backend`

### Error Handling
- Git command failures are caught and reported
- Backup failures prevent updates from proceeding
- Invalid commit hashes are validated before rollback
- Service restart failures are logged

## Notes
- Updates require sudo privileges for service restart
- Browser refresh may be needed after updates
- All backups are stored in `backups/` directory
- Check GitHub connection if version history doesn't load
