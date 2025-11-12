# GitHub Auto-Update System

## Overview

Your CRM backend can now automatically fetch and install updates from GitHub (`dadad132/cem-backend`).

## Quick Setup

**On your live Ubuntu server:**

1. **Configure Git credentials (REQUIRED for private repo):**
   ```bash
   cd ~/crm-backend
   git config credential.helper store
   ```
   
2. **Create GitHub Personal Access Token:**
   - Go to: https://github.com/settings/tokens
   - Click "Generate new token" → "Tokens (classic)"
   - Give it a name: "CRM Backend Auto-Update"
   - Select scope: **repo** (full control of private repositories)
   - Click "Generate token"
   - **COPY THE TOKEN** (you won't see it again!)

3. **Authenticate Git with PAT:**
   ```bash
   git pull
   ```
   When prompted:
   - Username: `dadad132`
   - Password: `paste_your_PAT_here`
   
   Git will save these credentials for future use.

4. **Install the update scripts:**
   ```bash
   chmod +x update_from_github.sh
   chmod +x setup_auto_update.sh
   ```

5. **Test manual update:**
   ```bash
   ./update_from_github.sh
   ```

6. **Setup automatic updates:**
   ```bash
   ./setup_auto_update.sh
   ```
   Choose your preferred schedule (hourly, daily, weekly, etc.)

## How It Works

### Manual Update
```bash
cd ~/crm-backend
./update_from_github.sh
```

**What it does:**
1. ✅ Fetches latest code from GitHub
2. ✅ Checks if updates are available
3. ✅ Stops the service
4. ✅ Backs up database
5. ✅ Backs up .env file
6. ✅ Pulls new code
7. ✅ Updates Python dependencies
8. ✅ Runs database migrations
9. ✅ Restarts service
10. ✅ Auto-rollback if update fails

### Automatic Updates

The `setup_auto_update.sh` script configures a cron job to check for updates automatically.

**Available schedules:**
- Every hour
- Every 6 hours
- Daily at 2 AM (recommended)
- Daily at custom time
- Weekly (Sunday at 2 AM)
- Custom cron expression

**Logs:**
```bash
tail -f ~/crm-backend/logs/auto_update.log
```

## Development Workflow

### On Windows (Development):

1. **Make changes to your code**
2. **Commit and push:**
   ```powershell
   git add .
   git commit -m "Add new feature"
   git push
   ```

### On Ubuntu (Production):

**Option A: Wait for auto-update** (if configured)
- Updates will be pulled automatically on schedule
- Check logs: `tail -f ~/crm-backend/logs/auto_update.log`

**Option B: Update immediately**
```bash
cd ~/crm-backend
./update_from_github.sh
```

## Safety Features

### Automatic Backups
Every update creates:
- Database backup: `backups/data_backup_update_TIMESTAMP.db`
- Config backup: `.env.backup.TIMESTAMP`
- Code backup: Git stash

### Automatic Rollback
If the service fails to start after update:
- Code reverts to previous commit
- .env file restored
- Service restarted with old version

### Manual Rollback
```bash
cd ~/crm-backend
sudo systemctl stop crm-backend

# Restore database
cp backups/data_backup_update_*.db data.db

# Restore code (go back one commit)
git reset --hard HEAD~1

# Restore .env
cp .env.backup.* .env

sudo systemctl start crm-backend
```

## Monitoring

### Check Update Status
```bash
cd ~/crm-backend
git log --oneline -5  # Show last 5 commits
git status            # Check current state
```

### Check Service Status
```bash
sudo systemctl status crm-backend
sudo journalctl -u crm-backend -f
```

### Check API Version
```bash
curl http://localhost:8000/api/system/version
```

### View Update Logs
```bash
tail -f ~/crm-backend/logs/auto_update.log
```

## Managing Auto-Update

### View Current Cron Jobs
```bash
crontab -l
```

### Edit Cron Jobs
```bash
crontab -e
```

### Disable Auto-Update
```bash
crontab -l | grep -v "update_from_github.sh" | crontab -
```

### Re-enable Auto-Update
```bash
cd ~/crm-backend
./setup_auto_update.sh
```

## Troubleshooting

### Updates Not Pulling

**Check Git status:**
```bash
cd ~/crm-backend
git status
git fetch origin main
git log --oneline origin/main -5
```

**Force update (careful!):**
```bash
cd ~/crm-backend
git fetch origin main
git reset --hard origin/main
./update_from_github.sh --auto
```

### Service Fails After Update

**View error logs:**
```bash
sudo journalctl -u crm-backend -n 50
```

**Rollback manually:**
```bash
cd ~/crm-backend
sudo systemctl stop crm-backend
git reset --hard HEAD~1
sudo systemctl start crm-backend
```

### Dependency Conflicts

**Rebuild virtual environment:**
```bash
cd ~/crm-backend
sudo systemctl stop crm-backend
rm -rf .venv
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
sudo systemctl start crm-backend
```

## Best Practices

### Before Pushing to GitHub

1. **Test locally first:**
   ```powershell
   python start_server.py --local-only
   # Test your changes
   ```

2. **Check for errors:**
   - No syntax errors
   - Database migrations work
   - All features tested

3. **Use meaningful commit messages:**
   ```bash
   git commit -m "Fix: Resolve project deletion bug"
   git commit -m "Feature: Add email notifications"
   git commit -m "Update: Improve task filtering performance"
   ```

### Update Timing

**Don't update during:**
- Peak usage hours
- Active user sessions
- Important demos/presentations

**Best time to update:**
- Late night (2-4 AM)
- Low traffic periods
- Scheduled maintenance windows

### Testing Updates

**Use a staging server:**
1. Set up a second server for testing
2. Update staging first
3. Test thoroughly
4. Then update production

## Configuration

### Change GitHub Repository

Edit `update_from_github.sh`:
```bash
GITHUB_REPO="your-username/your-repo"
GITHUB_BRANCH="main"
```

### Change Update Schedule

```bash
./setup_auto_update.sh
# Select new schedule
```

### Skip Confirmation

Update without prompting:
```bash
./update_from_github.sh --auto
```

## Security

### Private Repository Access

Your repository (`dadad132/cem-backend`) is **private**, so authentication is required:

**Step 1: Create Personal Access Token (PAT)**
```
1. Visit: https://github.com/settings/tokens
2. Click "Generate new token" → "Tokens (classic)"
3. Name: "CRM Backend Server"
4. Scopes: Select "repo" (full control of private repositories)
5. Generate and COPY the token (you won't see it again!)
```

**Step 2: Configure Git Credentials**
```bash
cd ~/crm-backend
git config credential.helper store  # Save credentials
git pull  # Enter username and PAT when prompted
```

**Step 3: Test Authentication**
```bash
git fetch  # Should work without prompting
```

**Troubleshooting Authentication:**
- Error: "Authentication failed" → PAT expired or wrong scope
- Error: "could not read Username" → Run `git config credential.helper store` again
- To reset credentials: `rm ~/.git-credentials` and repeat Step 2

### Webhook Integration (Advanced)

For instant updates when you push to GitHub:

1. **Install webhook listener:**
   ```bash
   sudo apt install webhook
   ```

2. **Create webhook config** (see GitHub webhook docs)

3. **Webhook calls** `update_from_github.sh --auto`

## Status Check Script

Create `check_update_status.sh`:
```bash
#!/bin/bash
cd ~/crm-backend
echo "Current commit: $(git rev-parse --short HEAD)"
echo "Latest remote:  $(git rev-parse --short origin/main)"
echo "Service status: $(systemctl is-active crm-backend)"
echo "Last update:    $(stat -c %y logs/auto_update.log 2>/dev/null || echo 'Never')"
```

## Summary

**To set up auto-updates:**
```bash
cd ~/crm-backend
chmod +x update_from_github.sh setup_auto_update.sh
./setup_auto_update.sh
```

**To push updates from Windows:**
```powershell
git add .
git commit -m "Your changes"
git push
```

**To verify updates applied:**
```bash
curl http://localhost:8000/api/system/version
tail -f ~/crm-backend/logs/auto_update.log
```

That's it! Your server will now automatically stay up to date with your GitHub repository. 🚀
