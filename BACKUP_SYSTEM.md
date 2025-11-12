# Database Backup & Recovery System

## Overview

The CRM system now includes a comprehensive automatic backup and recovery system that protects your data from loss due to crashes, corruption, or accidental changes.

## Features

### 🔄 Automatic Backups
- **Periodic Backups**: Database is automatically backed up every 12 hours while the server is running
- **Startup Backup**: A backup is created when the server starts (regardless of errors)
- **Shutdown Backup**: A backup is created when the server shuts down gracefully
- **Manual Backups**: Create backups on-demand through admin interface (independent of schedule)
- **Background Operation**: Backups run in the background without affecting performance

### 🛡️ Automatic Recovery
- **Startup Check**: Database integrity is verified every time the server starts
- **Auto-Restore**: If corruption is detected, the system automatically restores from the latest backup
- **Seamless Recovery**: Recovery happens transparently - users may not even notice
- **Corrupted Database Preservation**: The corrupted database is saved for forensic analysis

### 💾 Backup Management
- **Automatic Retention**: Keeps the last 10 automatic backups (configurable)
- **Manual Backups**: Created through admin interface are kept forever
- **Smart Cleanup**: Only automatic backups are deleted; manual backups preserved
- **Space Management**: Old automatic backups deleted to save disk space
- **Backup Statistics**: View automatic/manual counts, total size, and timestamps

### 🔍 Web Interface
- **Admin Dashboard**: Access backup management at `/web/admin/backups`
- **Backup List**: View all available backups with creation time and size
- **Manual Restore**: Restore from any previous backup with confirmation prompts
- **Status Indicators**: See backup statistics and system status at a glance

## How It Works

### Automatic Backup Flow

1. **Server Startup**:
   - System checks database integrity
   - If corrupted, restores from latest backup
   - Creates a startup backup
   - Starts automatic backup timer

2. **During Operation**:
   - Every 12 hours, creates a timestamped backup
   - Manual backups can be created anytime through admin interface
   - Keeps the 10 most recent backups
   - Deletes older backups automatically

3. **Server Shutdown**:
   - Creates a final backup
   - Stops the backup timer gracefully

### Backup File Structure

```
backups/
├── data_latest.db                         # Latest backup (quick restore link)
├── data_backup_AUTO_20251031_120000.db   # Automatic backups (deleted after 10)
├── data_backup_AUTO_20251031_120500.db
├── data_backup_AUTO_20251031_121000.db
├── data_backup_MANUAL_20251031_150000.db # Manual backups (kept forever)
├── data_backup_MANUAL_20251031_163000.db
└── ...
```

**Filename Convention:**
- `data_backup_AUTO_[timestamp].db` - Automatic backups (12-hour schedule)
- `data_backup_MANUAL_[timestamp].db` - Manual backups (admin-created)
- Only AUTO backups are subject to retention limits

### Recovery Process

If the database becomes corrupted:

1. System detects corruption on startup
2. Saves corrupted database as `corrupted_[timestamp].db`
3. Restores from `data_latest.db`
4. Server starts normally with restored data
5. User sees no interruption in service

## Configuration

### Backup Settings

Located in `app/core/backup.py`:

```python
class DatabaseBackup:
    def __init__(self, db_path: str = "data.db", backup_dir: str = "backups"):
        self.db_path = Path(db_path)
        self.backup_dir = Path(backup_dir)
        self.max_backups = 10           # Keep last 10 backups
        self.backup_interval = 43200    # Backup every 12 hours (43200 seconds)
```

To change settings:
- `max_backups`: Number of backups to keep (default: 10)
- `backup_interval`: Time between backups in seconds (default: 43200 = 12 hours)

## Usage

### Accessing Backup Management

1. Log in as an admin user
2. Navigate to **Admin → Database Backups** in the sidebar
3. View backup statistics and available backups

### Creating a Manual Backup

1. Go to the Backup Management page
2. Click **"💾 Create Backup Now"**
3. Wait for confirmation message

### Restoring from Backup

⚠️ **WARNING**: Restoring will replace all current data with the backup data!

1. Go to the Backup Management page
2. Find the backup you want to restore
3. Click **"🔄 Restore"** button
4. Confirm the action (you'll get TWO confirmation prompts)
5. Wait for the restore to complete
6. You may need to log in again

### Checking Backup Status

View the backup statistics card on the management page:
- **Total Backups**: Number of backup files available
- **Total Size**: Combined size of all backups
- **Latest Backup**: Timestamp of most recent backup
- **Oldest Backup**: Timestamp of oldest retained backup

## File Locations

- **Database**: `data.db` (project root)
- **Backups**: `backups/` directory (project root)
- **Backup Code**: `app/core/backup.py`
- **Integration**: `app/core/database.py` (lifespan function)
- **Startup**: `start_server.py` (integrity check)
- **Admin UI**: `app/templates/admin/backups.html`
- **Routes**: `app/web/routes.py` (admin backup routes)

## Monitoring

### Log Messages

The system logs all backup operations:

```
✅ Database integrity check passed
✅ Database backed up to backups/data_backup_20251031_120000.db
🔄 Auto-backup started (interval: 43200s)
💾 Saved corrupted database to backups/corrupted_20251031_120500.db
✅ Database restored from backups/data_latest.db
🗑️ Removed old backup: data_backup_20251031_110000.db
```

### Backup Statistics

Access detailed statistics programmatically:

```python
from app.core.backup import backup_manager

stats = backup_manager.get_backup_stats()
# Returns:
# {
#     'count': 10,
#     'total_size': 1048576,
#     'total_size_mb': 1.0,
#     'latest': 'data_backup_20251031_120000.db',
#     'latest_time': '2025-10-31 12:00:00',
#     'oldest': 'data_backup_20251031_110500.db',
#     'oldest_time': '2025-10-31 11:05:00'
# }
```

## Best Practices

1. **Regular Monitoring**: Check the backup page occasionally to ensure backups are being created
2. **Before Major Changes**: Always create a manual backup before making significant data changes (these are kept forever)
3. **Manual vs Automatic**: 
   - Use manual backups for important milestones or before risky operations
   - Let automatic backups handle routine protection
4. **Test Restores**: Periodically test the restore process to ensure backups are valid
5. **Disk Space**: Monitor disk space - manual backups accumulate since they're never deleted
6. **Cleanup Manual Backups**: Periodically delete old manual backups you no longer need
7. **External Backups**: Consider copying important manual backups to external storage for additional safety

## Troubleshooting

### Backup Not Running

Check the logs for error messages:
```bash
# Look for backup-related messages in server logs
grep -i backup server.log
```

### Database Corruption

If you see corruption warnings:
1. Don't panic - the system auto-restores
2. Check `backups/` for `corrupted_*.db` files
3. Review what operations happened before corruption
4. Contact support if corruption is recurring

### Restore Failed

If restore fails:
1. Check that the backup file exists and is readable
2. Verify you have write permissions on `data.db`
3. Try restoring from a different backup
4. Check server logs for detailed error messages

### Out of Disk Space

If backups fill up disk space:
1. Reduce `max_backups` in `app/core/backup.py`
2. Manually delete old backup files from `backups/` directory
3. Increase `backup_interval` to backup less frequently

## Technical Details

### Database Integrity Check

The system uses SQLite's internal checks to verify database integrity:

```python
conn = sqlite3.connect('data.db')
cursor = conn.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' LIMIT 1")
cursor.fetchone()  # If this fails, database is corrupted
```

### Backup Process

1. Copy database file using `shutil.copy2()` (preserves timestamps)
2. Create timestamped filename: `data_backup_YYYYMMDD_HHMMSS.db`
3. Create/update `data_latest.db` symlink for quick restore
4. Remove backups beyond retention limit

### AsyncIO Integration

Backups run in a background asyncio task that doesn't block the main application:

```python
async def _backup_loop(self):
    while True:
        await asyncio.sleep(self.backup_interval)
        self.create_backup()
```

## Security Considerations

- Backup files contain full database including passwords (hashed) and sensitive data
- Store `backups/` directory with appropriate file permissions
- Consider encrypting backups if storing externally
- Don't expose backup files through web server

## Support

For issues or questions about the backup system:
1. Check server logs for error messages
2. Review this documentation
3. Contact system administrator
