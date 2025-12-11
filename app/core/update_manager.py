"""
System update manager - handles updates and rollbacks from GitHub
"""
import os
import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Optional, List, Dict
import httpx
from datetime import datetime

logger = logging.getLogger(__name__)


class UpdateManager:
    """Manages system updates and rollbacks via git"""
    
    def __init__(self, repo_owner: str = "dadad132", repo_name: str = "cem-backend"):
        self.repo_owner = repo_owner
        self.repo_name = repo_name
        self.github_api = f"https://api.github.com/repos/{repo_owner}/{repo_name}"
        self.app_dir = Path.cwd()
        
    async def get_current_version(self) -> Dict[str, str]:
        """Get current git commit info"""
        try:
            # Get current commit hash
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=self.app_dir,
                capture_output=True,
                text=True,
                check=True
            )
            commit_hash = result.stdout.strip()
            
            # Get commit message
            result = subprocess.run(
                ["git", "log", "-1", "--pretty=%B"],
                cwd=self.app_dir,
                capture_output=True,
                text=True,
                check=True
            )
            commit_message = result.stdout.strip()
            
            # Get commit date
            result = subprocess.run(
                ["git", "log", "-1", "--pretty=%ci"],
                cwd=self.app_dir,
                capture_output=True,
                text=True,
                check=True
            )
            commit_date = result.stdout.strip()
            
            # Get branch
            result = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=self.app_dir,
                capture_output=True,
                text=True,
                check=True
            )
            branch = result.stdout.strip()
            
            return {
                "hash": commit_hash,
                "message": commit_message,
                "date": commit_date,
                "branch": branch
            }
        except Exception as e:
            logger.error(f"Failed to get current version: {e}")
            return {
                "hash": "unknown",
                "message": "Unable to determine version",
                "date": "unknown",
                "branch": "unknown"
            }
    
    async def get_commit_history(self, limit: int = 20) -> List[Dict[str, str]]:
        """Get recent commit history from GitHub"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.github_api}/commits",
                    params={"per_page": limit, "sha": "main"},
                    timeout=10.0
                )
                
                if response.status_code != 200:
                    logger.error(f"GitHub API error: {response.status_code}")
                    return []
                
                commits = response.json()
                history = []
                
                for commit in commits:
                    history.append({
                        "hash": commit["sha"][:7],
                        "full_hash": commit["sha"],
                        "message": commit["commit"]["message"].split('\n')[0],  # First line only
                        "author": commit["commit"]["author"]["name"],
                        "date": commit["commit"]["author"]["date"],
                        "url": commit["html_url"]
                    })
                
                return history
        except Exception as e:
            logger.error(f"Failed to fetch commit history: {e}")
            return []
    
    async def update_to_latest(self) -> Dict[str, any]:
        """Update to latest version from GitHub"""
        try:
            # Backup current state
            from app.core.backup import backup_manager
            backup_file = backup_manager.create_backup(is_manual=True, include_attachments=True)
            
            if not backup_file:
                return {"success": False, "error": "Failed to create backup before update"}
            
            # Fetch latest changes
            result = subprocess.run(
                ["git", "fetch", "origin"],
                cwd=self.app_dir,
                capture_output=True,
                text=True,
                check=True
            )
            
            # Get info about what will be updated
            result = subprocess.run(
                ["git", "log", "HEAD..origin/main", "--oneline"],
                cwd=self.app_dir,
                capture_output=True,
                text=True,
                check=True
            )
            changes = result.stdout.strip()
            
            if not changes:
                return {"success": True, "message": "Already up to date", "changes": []}
            
            # Pull changes
            result = subprocess.run(
                ["git", "reset", "--hard", "origin/main"],
                cwd=self.app_dir,
                capture_output=True,
                text=True,
                check=True
            )
            
            # Update dependencies if venv exists
            venv_python = self.app_dir / "venv" / "bin" / "python"
            if venv_python.exists():
                result = subprocess.run(
                    [str(venv_python), "-m", "pip", "install", "-r", "requirements.txt", "--upgrade"],
                    cwd=self.app_dir,
                    capture_output=True,
                    text=True,
                    check=True
                )
            
            # Run Alembic migrations
            alembic_ini = self.app_dir / "alembic.ini"
            if alembic_ini.exists():
                logger.info("Running Alembic migrations...")
                if venv_python.exists():
                    result = subprocess.run(
                        [str(venv_python), "-m", "alembic", "upgrade", "head"],
                        cwd=self.app_dir,
                        capture_output=True,
                        text=True
                    )
                    if result.returncode != 0:
                        logger.warning(f"Alembic migration warning: {result.stderr}")
                else:
                    result = subprocess.run(
                        ["python3", "-m", "alembic", "upgrade", "head"],
                        cwd=self.app_dir,
                        capture_output=True,
                        text=True
                    )
                    if result.returncode != 0:
                        logger.warning(f"Alembic migration warning: {result.stderr}")
            
            # Run any migration scripts in migrations/ folder
            migrations_dir = self.app_dir / "migrations"
            if migrations_dir.exists():
                logger.info("Running migration scripts...")
                # Run all Python scripts in migrations folder that start with common patterns
                migration_patterns = ["migrate_*.py", "add_*.py", "*_migration.py"]
                migration_scripts = set()
                for pattern in migration_patterns:
                    migration_scripts.update(migrations_dir.glob(pattern))
                
                for migration_script in sorted(migration_scripts):
                    logger.info(f"Running {migration_script.name}...")
                    try:
                        if venv_python.exists():
                            result = subprocess.run(
                                [str(venv_python), str(migration_script)],
                                cwd=self.app_dir,
                                capture_output=True,
                                text=True,
                                timeout=30
                            )
                        else:
                            result = subprocess.run(
                                ["python3", str(migration_script)],
                                cwd=self.app_dir,
                                capture_output=True,
                                text=True,
                                timeout=30
                            )
                        
                        if result.returncode == 0:
                            logger.info(f"✓ {migration_script.name} completed")
                        else:
                            logger.warning(f"⚠ {migration_script.name} returned code {result.returncode}")
                            if result.stderr:
                                logger.warning(f"Error: {result.stderr}")
                    except Exception as e:
                        logger.error(f"Failed to run {migration_script.name}: {e}")
            
            # Run standalone migration scripts in root directory (multiple patterns)
            root_migration_patterns = ["add_*.py", "migrate_*.py", "fix_*.py", "rebuild_*.py"]
            root_migrations = set()
            for pattern in root_migration_patterns:
                root_migrations.update(self.app_dir.glob(pattern))
            
            for migration_script in sorted(root_migrations):
                logger.info(f"Running {migration_script.name}...")
                try:
                    if venv_python.exists():
                        result = subprocess.run(
                            [str(venv_python), str(migration_script)],
                            cwd=self.app_dir,
                            capture_output=True,
                            text=True,
                            timeout=30
                        )
                    else:
                        result = subprocess.run(
                            ["python3", str(migration_script)],
                            cwd=self.app_dir,
                            capture_output=True,
                            text=True,
                            timeout=30
                        )
                    
                    if result.returncode == 0:
                        logger.info(f"✓ {migration_script.name} completed successfully")
                        if result.stdout:
                            logger.info(f"Output: {result.stdout}")
                    else:
                        logger.warning(f"⚠ {migration_script.name} returned code {result.returncode}")
                        if result.stderr:
                            logger.warning(f"Error: {result.stderr}")
                except Exception as e:
                    logger.error(f"Failed to run {migration_script.name}: {e}")
            
            # Ensure database schema is up to date
            logger.info("Updating database schema...")
            if venv_python.exists():
                subprocess.run(
                    [str(venv_python), "-c", 
                     "from app.core.database import init_db; import asyncio; asyncio.run(init_db())"],
                    cwd=self.app_dir,
                    capture_output=True,
                    text=True
                )
            
            return {
                "success": True,
                "message": "Updated successfully",
                "changes": changes.split('\n'),
                "backup": str(backup_file)
            }
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Git command failed: {e.stderr}")
            return {"success": False, "error": f"Git error: {e.stderr}"}
        except Exception as e:
            logger.error(f"Update failed: {e}")
            return {"success": False, "error": str(e)}
    
    async def rollback_to_commit(self, commit_hash: str) -> Dict[str, any]:
        """Rollback to a specific commit"""
        try:
            # Backup current state
            from app.core.backup import backup_manager
            backup_file = backup_manager.create_backup(is_manual=True, include_attachments=True)
            
            if not backup_file:
                return {"success": False, "error": "Failed to create backup before rollback"}
            
            # Verify commit exists
            result = subprocess.run(
                ["git", "cat-file", "-t", commit_hash],
                cwd=self.app_dir,
                capture_output=True,
                text=True,
                check=True
            )
            
            if result.stdout.strip() != "commit":
                return {"success": False, "error": "Invalid commit hash"}
            
            # Reset to commit
            result = subprocess.run(
                ["git", "reset", "--hard", commit_hash],
                cwd=self.app_dir,
                capture_output=True,
                text=True,
                check=True
            )
            
            # Update dependencies if venv exists
            venv_python = self.app_dir / "venv" / "bin" / "python"
            if venv_python.exists():
                result = subprocess.run(
                    [str(venv_python), "-m", "pip", "install", "-r", "requirements.txt"],
                    cwd=self.app_dir,
                    capture_output=True,
                    text=True,
                    check=True
                )
            
            # Run Alembic migrations after rollback
            alembic_ini = self.app_dir / "alembic.ini"
            if alembic_ini.exists():
                logger.info("Running Alembic migrations...")
                if venv_python.exists():
                    subprocess.run(
                        [str(venv_python), "-m", "alembic", "upgrade", "head"],
                        cwd=self.app_dir,
                        capture_output=True,
                        text=True
                    )
            
            # Ensure database schema is up to date
            logger.info("Updating database schema...")
            if venv_python.exists():
                subprocess.run(
                    [str(venv_python), "-c", 
                     "from app.core.database import init_db; import asyncio; asyncio.run(init_db())"],
                    cwd=self.app_dir,
                    capture_output=True,
                    text=True
                )
            
            return {
                "success": True,
                "message": f"Rolled back to commit {commit_hash}",
                "backup": str(backup_file)
            }
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Rollback failed: {e.stderr}")
            return {"success": False, "error": f"Git error: {e.stderr}"}
        except Exception as e:
            logger.error(f"Rollback failed: {e}")
            return {"success": False, "error": str(e)}
    
    async def restart_service(self) -> bool:
        """Restart the systemd service"""
        try:
            result = subprocess.run(
                ["sudo", "systemctl", "restart", "crm-backend"],
                capture_output=True,
                text=True,
                check=True
            )
            return True
        except Exception as e:
            logger.error(f"Failed to restart service: {e}")
            return False


# Global update manager instance
update_manager = UpdateManager()
