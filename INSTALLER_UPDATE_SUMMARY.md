# Ubuntu Installer Update Summary

## Changes Made

The Ubuntu installer has been updated to include the comprehensive update system for the CRM Backend application.

## New Features Added

### 1. Update System Integration

The installer now includes full support for the update notification and management system:

- **Version Tracking**: Application version stored in `app/__init__.py` and `app/core/version.py`
- **Update API**: RESTful endpoints for checking version and updates
- **Admin UI**: Web interface at `/admin/updates` for update management
- **Automatic Scripts**: Pre-configured update scripts for easy deployment

### 2. New Files Included

The installer package now includes:

- `auto_update.sh` - Automatic update script for Ubuntu/Linux servers
- `auto_update.bat` - Automatic update script for Windows development
- `update_ubuntu.sh` - Alternative update script for Ubuntu
- `UPDATE_SYSTEM.md` - Comprehensive update system documentation

### 3. New Directories

The installer creates an additional directory:

- `updates/` - Directory for storing update files and logs

### 4. Environment Configuration

New environment variables added to `.env`:

```env
# Update System Configuration
UPDATE_CHECK_ENABLED=true
UPDATE_CHECK_URL=https://api.github.com/repos/yourusername/crm-backend/releases/latest
UPDATE_CHECK_INTERVAL=86400
```

## Installation Process Updates

### Updated install_ubuntu.sh

The installation script now:

1. Creates the `updates/` directory
2. Configures update system environment variables
3. Displays update check command in completion message
4. Shows the updates directory location

### Updated Package Creators

Both `create_installer_package.ps1` (PowerShell) and `create_installer_package.bat` have been updated to:

1. Include update scripts in the package
2. Create the `updates/` directory structure
3. Copy all necessary update system files

## Usage Instructions

### For End Users

After installation, users can:

1. **Check for Updates via Web UI**:
   - Log in as administrator
   - Navigate to Admin → System Updates
   - Click "Check for Updates Now"

2. **Update via Command Line**:
   ```bash
   cd ~/crm-backend
   ./auto_update.sh
   ```

3. **Manual Update**:
   ```bash
   ./update_ubuntu.sh
   ```

### For Developers

When creating a new installer package:

```powershell
# PowerShell (Windows)
.\create_installer_package.ps1
```

```bash
# Batch (Windows)
create_installer_package.bat
```

The package will include all update system files automatically.

## API Endpoints

### Public Endpoints

- `GET /api/system/version` - Get current version
- `GET /api/system/health` - Health check

### Admin Endpoints

- `GET /api/system/updates/check` - Check for available updates

## Configuration Options

### Enable/Disable Update Checks

Edit `.env` file:

```env
UPDATE_CHECK_ENABLED=true  # Set to false to disable
```

### Custom Update Server

Point to your own update server:

```env
UPDATE_CHECK_URL=https://your-server.com/api/latest-version
```

### Check Interval

Adjust automatic check frequency (in seconds):

```env
UPDATE_CHECK_INTERVAL=86400  # 24 hours
```

## Update Process

The automatic update script (`auto_update.sh`):

1. ✅ Displays current version
2. ✅ Creates automatic database backup
3. ✅ Backs up `.env` configuration file
4. ✅ Stops the service gracefully
5. ✅ Stashes local changes (if any)
6. ✅ Pulls latest code from Git repository
7. ✅ Activates virtual environment
8. ✅ Updates Python dependencies
9. ✅ Runs database migrations (if available)
10. ✅ Starts the service
11. ✅ Verifies service is running
12. ✅ Displays version update summary

## Rollback Support

If an update fails:

1. Database backup is available in `backups/data_pre_update_TIMESTAMP.db`
2. Configuration backup in `backups/.env_backup_TIMESTAMP`
3. Git allows reverting to previous version
4. Service can be restored using backups

## Security Features

- Update check requires admin authentication
- Only administrators can view update page
- HTTPS recommended for update checks
- Database backups created before updates
- Graceful service restart prevents data loss

## Testing

To test the update system:

1. **Test Version Display**:
   ```bash
   python3 -c "from app import VERSION; print(VERSION)"
   ```

2. **Test Update Check** (requires admin login):
   ```bash
   curl http://localhost:8000/api/system/updates/check \
     -H "Cookie: session=YOUR_SESSION_COOKIE"
   ```

3. **Test Update Script** (dry run):
   ```bash
   # Review the script without executing
   cat auto_update.sh
   ```

## Documentation

Complete documentation available in:

- `UPDATE_SYSTEM.md` - Detailed update system guide
- `INSTALLER_README.md` - Installation instructions
- `QUICK_INSTALL.md` - Quick start guide
- Admin web UI - Built-in help and instructions

## Benefits

### For Administrators

- Easy update management through web UI
- Automated update scripts reduce manual work
- Automatic backups protect against data loss
- Clear version tracking and history
- Rollback support for failed updates

### For Users

- Minimal downtime during updates
- Transparent version information
- Improved stability and features
- Regular security updates

### For Developers

- Standardized update process
- Automated deployment workflow
- Version control integration
- Easy to test and validate

## Future Enhancements

Potential improvements for future versions:

- [ ] Automatic update downloads
- [ ] One-click updates from web UI
- [ ] Update scheduling
- [ ] Email notifications for new versions
- [ ] Differential updates (only changed files)
- [ ] Update signing and verification
- [ ] Automatic rollback on failure
- [ ] Multi-server update coordination

## Support

For issues or questions:

1. Check `UPDATE_SYSTEM.md` for detailed guidance
2. Review system logs: `sudo journalctl -u crm-backend -n 50`
3. Test manually: `./auto_update.sh`
4. Contact support with version information

## Version History

- **v1.2.0** (2025-01-15) - Added comprehensive update system
- **v1.1.0** (2024-11-01) - Added project member invitations
- **v1.0.0** (2024-10-01) - Initial release

---

**Last Updated**: January 15, 2025  
**Installer Version**: 1.2.0  
**Status**: Production Ready ✅
