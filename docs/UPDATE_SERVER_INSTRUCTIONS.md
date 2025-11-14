# Server Update Instructions

## Error: `no such column: user.profile_picture`

This error occurs because the database schema needs to be updated with the new `profile_picture` column.

## Quick Fix - Run These Commands on Your Server:

```bash
# 1. Navigate to your project directory
cd /home/eugene/crm-backend

# 2. Pull the latest changes from GitHub
git pull origin main

# 3. Activate your virtual environment
source .venv/bin/activate

# 4. Run the profile picture migration
python migrate_profile_picture.py

# 5. Restart the server
# If using systemd:
sudo systemctl restart crm-backend

# Or if running manually, stop the current process and start again:
python start_server.py
```

## Alternative: Manual Migration

If the migration script doesn't work, run this SQL command directly:

```bash
# Connect to your database
sqlite3 data.db

# Run the migration
ALTER TABLE user ADD COLUMN profile_picture VARCHAR;

# Exit sqlite
.exit
```

## Verify the Migration

After running the migration, verify it worked:

```bash
python -c "from app.models.user import User; from app.core.database import engine; print('✓ Migration successful')"
```

## What Changed

The latest update adds:
- ✅ Profile picture upload functionality
- ✅ Search functionality for tasks and projects
- ✅ Security improvements (path traversal protection, file size limits)
- ✅ Theme updates (professional gray sidebar)
- ✅ Bug fixes (date format, timezone corrections)

## If You Still Get Errors

1. Make sure you pulled the latest code: `git pull origin main`
2. Check that the migration ran: `sqlite3 data.db "PRAGMA table_info(user);" | grep profile_picture`
3. If the column exists but you still get errors, restart the server completely
4. Check logs: `journalctl -u crm-backend -f` (if using systemd)

## Server Restart Commands

### If using systemd service:
```bash
sudo systemctl stop crm-backend
sudo systemctl start crm-backend
sudo systemctl status crm-backend
```

### If using screen/tmux:
```bash
# Find the screen session
screen -ls

# Attach to it
screen -r crm-backend

# Stop the server (Ctrl+C)
# Start it again
python start_server.py

# Detach (Ctrl+A then D)
```

### If running directly:
```bash
# Find and kill the process
pkill -f "python.*start_server"
# Or
ps aux | grep start_server
kill -9 <PID>

# Start again
python start_server.py
```

## Need Help?

If you encounter any issues:
1. Check the error logs
2. Verify the database has the new column
3. Make sure all code is updated from GitHub
4. Try restarting the entire server (not just the app)
