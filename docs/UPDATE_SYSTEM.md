# Update System Documentation

The CRM Backend includes a comprehensive update notification system that allows deployed servers to check for and apply updates automatically.

## Architecture

### Components

1. **Version Tracking** (`app/core/version.py`)
   - Maintains current version number (semantic versioning)
   - Build date tracking
   - Update check configuration

2. **Update Manager** (`app/core/updates.py`)
   - Checks remote server for new versions
   - Compares version numbers
   - Caches update information locally
   - Registers server instances with central server

3. **System API** (`app/api/routes/system.py`)
   - `/api/system/version` - Returns current version info
   - `/api/system/updates/check` - Checks for available updates (admin only)
   - `/api/system/health` - Health monitoring endpoint

4. **Admin UI** (`app/templates/admin/updates.html`)
   - Web interface to view current version
   - Check for updates button
   - Display update availability
   - Instructions for applying updates

5. **Update Scripts**
   - `auto_update.sh` - Linux/Ubuntu automated update script
   - `auto_update.bat` - Windows update script

## Setup

### 1. Configure Update Server URL

Edit `app/core/version.py`:

```python
# Option A: Use GitHub Releases
UPDATE_CHECK_URL = "https://api.github.com/repos/yourusername/crm-backend/releases/latest"

# Option B: Use custom update server
UPDATE_CHECK_URL = "https://your-update-server.com/api/latest-version"

# Option C: Use GitLab
UPDATE_CHECK_URL = "https://gitlab.com/api/v4/projects/YOUR_PROJECT_ID/releases/latest"
```

### 2. Update Server Response Format

Your update server should return JSON in this format:

```json
{
  "version": "1.1.0",
  "release_date": "2025-11-15",
  "release_notes": "Bug fixes and performance improvements",
  "download_url": "https://github.com/yourusername/crm-backend/archive/refs/tags/v1.1.0.zip"
}
```

### 3. Enable/Disable Auto-Check

In `app/core/version.py`:

```python
ENABLE_AUTO_UPDATE_CHECK = True  # Set to False to disable
UPDATE_CHECK_INTERVAL_HOURS = 24  # How often to check
```

## Using GitHub Releases as Update Server

### Step 1: Create a GitHub Release

1. Go to your repository on GitHub
2. Click "Releases" → "Create a new release"
3. Tag version: `v1.1.0`
4. Release title: `Version 1.1.0`
5. Description: Release notes
6. Publish release

### Step 2: Configure Version File

```python
UPDATE_CHECK_URL = "https://api.github.com/repos/yourusername/crm-backend/releases/latest"
```

### Step 3: Update Version Number

When releasing a new version:

```python
# app/core/version.py
VERSION = "1.1.0"
BUILD_DATE = "2025-11-15"
```

### GitHub API Response Format

GitHub automatically provides the correct JSON format:

```json
{
  "tag_name": "v1.1.0",
  "name": "Version 1.1.0",
  "body": "Release notes here...",
  "published_at": "2025-11-15T10:00:00Z",
  "zipball_url": "https://api.github.com/repos/.../zipball/v1.1.0"
}
```

The update checker automatically parses this format.

## Usage

### For Administrators

#### Via Web Interface

1. Log in as admin
2. Navigate to **Admin** → **Updates**
3. Click "Check for Updates Now"
4. If an update is available, follow the instructions

#### Via API

```bash
# Check current version
curl http://localhost:8000/api/system/version

# Check for updates (requires admin auth)
curl -H "Cookie: session=..." http://localhost:8000/api/system/updates/check
```

### Applying Updates

#### Linux/Ubuntu (with systemd service)

```bash
cd ~/crm-backend
chmod +x auto_update.sh
./auto_update.sh
```

The script will:
1. ✅ Stop the systemd service
2. ✅ Create database backup
3. ✅ Pull latest code from git
4. ✅ Update Python dependencies
5. ✅ Run database migrations
6. ✅ Restart the service
7. ✅ Rollback on failure

#### Windows

```batch
cd C:\path\to\crm-backend
auto_update.bat
```

After the script completes:
```batch
python3 start_server.py
```

#### Manual Update

```bash
# Backup database
cp data.db data.db.backup

# Pull latest code
git pull origin main

# Update dependencies
pip install -r requirements.txt --upgrade

# Run migrations (if any)
alembic upgrade head

# Restart server
sudo systemctl restart crm-backend  # Linux
# or manually restart on Windows
```

## Update Script Details

### auto_update.sh (Linux)

**Features:**
- Color-coded output (green=success, red=error, yellow=warning)
- Automatic backup before update
- Service management (stop/start)
- Error handling with rollback
- Migration support
- Git pull verification

**Requirements:**
- systemd service named `crm-backend`
- Git repository
- Python 3.9+

**Rollback:**
If the update fails, the script automatically:
1. Restores database from backup
2. Reverts git changes
3. Restarts service with old code

### auto_update.bat (Windows)

**Features:**
- Similar functionality to Linux script
- Manual service restart (no systemd)
- Color-coded console output
- Backup and restore

**Limitations:**
- No automatic service restart
- Requires manual server restart after update

## Testing the Update System

### 1. Test Local Update Check

```bash
# Start server
python3 start_server.py

# In another terminal
curl http://localhost:8000/api/system/version
```

Expected response:
```json
{
  "version": "1.0.0",
  "build_date": "2025-11-01",
  "status": "running"
}
```

### 2. Test Update Detection

1. Change `UPDATE_CHECK_URL` to a test endpoint
2. Log in as admin
3. Go to Admin → Updates
4. Click "Check for Updates Now"

### 3. Test Update Script

```bash
# Create a test branch
git checkout -b test-update

# Make a small change
echo "# Test update" >> README.md
git add README.md
git commit -m "Test update"

# Run update script in dry-run mode
./auto_update.sh
```

## Troubleshooting

### Update Check Fails

**Problem:** "Update Check Failed - Unable to connect to update server"

**Solutions:**
1. Check `UPDATE_CHECK_URL` in `version.py`
2. Verify internet connection
3. Check firewall rules
4. Test URL manually: `curl $UPDATE_CHECK_URL`

### Update Script Fails

**Problem:** Git pull fails

**Solutions:**
```bash
# Check git status
git status

# Discard local changes
git reset --hard HEAD

# Re-run update
./auto_update.sh
```

**Problem:** Service won't restart

**Solutions:**
```bash
# Check service status
sudo systemctl status crm-backend

# View logs
sudo journalctl -u crm-backend -n 50

# Manually restart
sudo systemctl restart crm-backend
```

**Problem:** Database migration fails

**Solutions:**
```bash
# Restore backup
cp backups/data_YYYYMMDD_HHMMSS.db data.db

# Check migration status
alembic current

# Try manual migration
alembic upgrade head
```

### Permission Denied

**Problem:** `./auto_update.sh: Permission denied`

**Solution:**
```bash
chmod +x auto_update.sh
```

## Security Considerations

1. **HTTPS Only**: Always use HTTPS for `UPDATE_CHECK_URL`
2. **Authentication**: Update check endpoint requires admin authentication
3. **Signature Verification**: Consider implementing update signature verification
4. **Backups**: Always backup before updating (script does this automatically)
5. **Rate Limiting**: Update checks are cached to prevent excessive requests

## Central Update Server Setup

### Option 1: Simple Flask Server

```python
# update_server.py
from flask import Flask, jsonify

app = Flask(__name__)

@app.route('/api/latest-version')
def latest_version():
    return jsonify({
        "version": "1.1.0",
        "release_date": "2025-11-15",
        "release_notes": "Bug fixes and performance improvements",
        "download_url": "https://github.com/yourusername/crm-backend/archive/v1.1.0.zip"
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
```

### Option 2: Static JSON File

Host a static JSON file on your web server:

```json
{
  "version": "1.1.0",
  "release_date": "2025-11-15",
  "release_notes": "Bug fixes and performance improvements",
  "download_url": "https://example.com/crm-backend-1.1.0.zip"
}
```

Set `UPDATE_CHECK_URL` to the JSON file URL.

### Option 3: Use GitHub/GitLab

No additional server needed - just create releases on GitHub/GitLab.

## Best Practices

1. **Version Numbering**: Use semantic versioning (MAJOR.MINOR.PATCH)
2. **Release Notes**: Always provide clear release notes
3. **Testing**: Test updates on staging environment first
4. **Backups**: Keep multiple backup copies
5. **Monitoring**: Monitor update success rate across deployments
6. **Notifications**: Consider adding email notifications for failed updates
7. **Rollback Plan**: Always have a rollback strategy ready

## Future Enhancements

- [ ] Automatic update application (optional setting)
- [ ] Email notifications when updates available
- [ ] Update scheduling (apply updates at specific times)
- [ ] Update signature verification
- [ ] Differential updates (only changed files)
- [ ] Multi-instance update coordination
- [ ] Update metrics and reporting
- [ ] Webhook notifications to central server
