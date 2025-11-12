"""
Database backup and restore functionality with automatic crash recovery
Includes database + attachments (comments and chat messages)
"""
import os
import shutil
import asyncio
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional, List
import logging

logger = logging.getLogger(__name__)


class DatabaseBackup:
    """Handles automatic database backup and restore operations with attachments"""
    
    def __init__(self, db_path: str = "data.db", backup_dir: str = "backups"):
        self.db_path = Path(db_path)
        self.backup_dir = Path(backup_dir)
        self.backup_dir.mkdir(exist_ok=True)
        self.uploads_dir = Path("app/uploads")  # Attachments directory
        self.max_backups = 10  # Keep last 10 backups
        self.backup_interval = 43200  # Backup every 12 hours (43200 seconds)
        self._backup_task: Optional[asyncio.Task] = None
        
    def get_backup_filename(self, is_manual: bool = False, include_attachments: bool = True) -> str:
        """Generate timestamped backup filename"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_type = "MANUAL" if is_manual else "AUTO"
        extension = ".zip" if include_attachments else ".db"
        return f"backup_{backup_type}_{timestamp}{extension}"
    
    def create_backup(self, is_manual: bool = False, include_attachments: bool = True) -> Optional[Path]:
        """Create a backup of the database and optionally attachments
        
        Args:
            is_manual: If True, marks as manual backup (won't be auto-deleted)
            include_attachments: If True, includes uploads folder in a zip archive
        """
        if not self.db_path.exists():
            logger.warning(f"Database {self.db_path} does not exist, skipping backup")
            return None
        
        try:
            backup_file = self.backup_dir / self.get_backup_filename(is_manual=is_manual, include_attachments=include_attachments)
            backup_type = "MANUAL" if is_manual else "AUTO"
            
            if include_attachments:
                # Create ZIP archive with database + attachments
                with zipfile.ZipFile(backup_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    # Add database
                    zipf.write(self.db_path, arcname='data.db')
                    
                    # Add all attachments
                    if self.uploads_dir.exists():
                        for file_path in self.uploads_dir.rglob('*'):
                            if file_path.is_file():
                                arcname = str(file_path.relative_to(self.uploads_dir.parent))
                                zipf.write(file_path, arcname=arcname)
                
                logger.info(f"✅ Full backup (DB + attachments) created: {backup_file} ({backup_type})")
            else:
                # Simple database-only backup
                shutil.copy2(self.db_path, backup_file)
                logger.info(f"✅ Database backup created: {backup_file} ({backup_type})")
            
            # Create a "latest" backup link for easy restore
            latest_backup = self.backup_dir / ("backup_latest.zip" if include_attachments else "data_latest.db")
            if latest_backup.exists():
                latest_backup.unlink()
            shutil.copy2(backup_file, latest_backup)
            
            # Cleanup old automatic backups only
            if not is_manual:
                self._cleanup_old_backups()
            
            return backup_file
        except Exception as e:
            logger.error(f"❌ Failed to create backup: {e}")
            return None
    
    def _cleanup_old_backups(self):
        """Remove old AUTOMATIC backup files only, keeping manual backups forever"""
        try:
            # Only get automatic backups (both .db and .zip)
            auto_backups = sorted(
                [f for f in self.backup_dir.glob("backup_AUTO_*.*") if f.suffix in ['.db', '.zip']],
                key=lambda x: x.stat().st_mtime,
                reverse=True
            )
            
            # Remove automatic backups beyond max_backups
            for old_backup in auto_backups[self.max_backups:]:
                old_backup.unlink()
                logger.info(f"🗑️  Removed old automatic backup: {old_backup.name}")
        except Exception as e:
            logger.error(f"Error cleaning up old backups: {e}")
    
    def get_latest_backup(self) -> Optional[Path]:
        """Get the most recent backup file (automatic or manual)"""
        # Check for latest links
        latest_zip = self.backup_dir / "backup_latest.zip"
        latest_db = self.backup_dir / "data_latest.db"
        
        if latest_zip.exists():
            return latest_zip
        if latest_db.exists():
            return latest_db
        
        # Fallback to finding most recent timestamped backup (both AUTO and MANUAL, both .db and .zip)
        backups = sorted(
            [f for f in self.backup_dir.glob("backup_*.*") 
             if f.suffix in ['.db', '.zip'] and 'latest' not in f.name],
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )
        
        return backups[0] if backups else None
    
    def restore_from_backup(self, backup_file: Optional[Path] = None) -> bool:
        """Restore database from backup file (supports .db or .zip)"""
        if backup_file is None:
            backup_file = self.get_latest_backup()
        
        if backup_file is None or not backup_file.exists():
            logger.error("❌ No backup file found for restore")
            return False
        
        try:
            # Create a backup of the current (possibly corrupted) database
            if self.db_path.exists():
                corrupted_backup = self.backup_dir / f"corrupted_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
                shutil.copy2(self.db_path, corrupted_backup)
                logger.info(f"💾 Saved corrupted database to {corrupted_backup}")
            
            # Backup current uploads directory
            if self.uploads_dir.exists():
                corrupted_uploads = self.backup_dir / f"corrupted_uploads_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                shutil.copytree(self.uploads_dir, corrupted_uploads)
                logger.info(f"💾 Saved current uploads to {corrupted_uploads}")
            
            # Restore based on file type
            if backup_file.suffix == '.zip':
                # Extract ZIP archive (contains database + attachments)
                with zipfile.ZipFile(backup_file, 'r') as zipf:
                    # Extract database
                    zipf.extract('data.db', path=self.db_path.parent)
                    
                    # Extract attachments
                    for member in zipf.namelist():
                        if member.startswith('app/uploads/'):
                            zipf.extract(member, path='.')
                
                logger.info(f"✅ Full backup restored (DB + attachments) from {backup_file}")
            else:
                # Simple database file restore
                shutil.copy2(backup_file, self.db_path)
                logger.info(f"✅ Database restored from {backup_file}")
            
            return True
        except Exception as e:
            logger.error(f"❌ Failed to restore database: {e}")
            return False
    
    def check_and_restore_on_startup(self) -> bool:
        """Check database integrity on startup and restore ONLY if corrupted or missing"""
        # Only restore if database doesn't exist AND backup is available
        if not self.db_path.exists():
            latest_backup = self.get_latest_backup()
            if latest_backup:
                logger.warning("⚠️  Database does not exist, attempting restore from backup")
                return self.restore_from_backup()
            else:
                # No backup available - this is a fresh install, return True to allow init
                logger.info("ℹ️  Database does not exist and no backup available (fresh install)")
                return True
        
        # Check if database is accessible and has tables
        try:
            import sqlite3
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()
            
            # Check if database has tables (not empty)
            cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'")
            table_count = cursor.fetchone()[0]
            
            if table_count == 0:
                conn.close()
                logger.warning("⚠️  Database is empty, attempting restore from backup")
                return self.restore_from_backup()
            
            # Try to query a critical table to ensure it's not corrupted
            cursor.execute("SELECT COUNT(*) FROM user")
            cursor.fetchone()
            
            conn.close()
            logger.info("✅ Database integrity check passed")
            return True
        except sqlite3.DatabaseError as e:
            logger.error(f"⚠️  Database is corrupted: {e}")
            logger.info("🔄 Attempting to restore from backup...")
            return self.restore_from_backup()
        except Exception as e:
            # If table doesn't exist or other error, database might be incomplete but not corrupted
            logger.warning(f"⚠️  Database check warning: {e}")
            # Don't restore automatically - let the app handle schema creation
            return True
    
    async def start_auto_backup(self):
        """Start automatic periodic backup in background"""
        if self._backup_task is not None:
            logger.warning("Auto-backup already running")
            return
        
        self._backup_task = asyncio.create_task(self._backup_loop())
        logger.info(f"🔄 Auto-backup started (interval: {self.backup_interval}s)")
    
    async def stop_auto_backup(self):
        """Stop automatic backup"""
        if self._backup_task is not None:
            self._backup_task.cancel()
            try:
                await self._backup_task
            except asyncio.CancelledError:
                pass
            self._backup_task = None
            logger.info("⏸️  Auto-backup stopped")
    
    async def _backup_loop(self):
        """Background task for periodic backups"""
        while True:
            try:
                await asyncio.sleep(self.backup_interval)
                self.create_backup()
            except asyncio.CancelledError:
                logger.info("Backup loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in backup loop: {e}")
    
    def save_uploaded_backup(self, file_content: bytes, filename: str) -> Optional[Path]:
        """Save an uploaded backup file from local machine
        
        Args:
            file_content: The backup file content
            filename: Original filename (will be prefixed with timestamp)
        
        Returns:
            Path to saved backup file or None if failed
        """
        try:
            # Validate file extension
            if not filename.endswith(('.db', '.zip')):
                logger.error(f"Invalid backup file extension: {filename}")
                return None
            
            # Create unique filename with timestamp
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            extension = '.zip' if filename.endswith('.zip') else '.db'
            new_filename = f"backup_UPLOADED_{timestamp}{extension}"
            
            backup_path = self.backup_dir / new_filename
            
            # Save the file
            with open(backup_path, 'wb') as f:
                f.write(file_content)
            
            logger.info(f"✅ Uploaded backup saved: {backup_path}")
            return backup_path
            
        except Exception as e:
            logger.error(f"❌ Failed to save uploaded backup: {e}")
            return None
    
    def get_backup_stats(self) -> dict:
        """Get statistics about backups (automatic and manual)"""
        all_backups = [f for f in self.backup_dir.glob("backup_*.*") 
                      if f.suffix in ['.db', '.zip'] and 'latest' not in f.name and 'corrupted' not in f.name]
        auto_backups = [f for f in all_backups if "_AUTO_" in f.name]
        manual_backups = [f for f in all_backups if "_MANUAL_" in f.name]
        full_backups = [f for f in all_backups if f.suffix == '.zip']
        db_only_backups = [f for f in all_backups if f.suffix == '.db']
        
        if not all_backups:
            return {
                "count": 0,
                "auto_count": 0,
                "manual_count": 0,
                "full_count": 0,
                "db_only_count": 0,
                "total_size": 0,
                "total_size_mb": 0,
                "latest": None,
                "latest_time": None,
                "oldest": None,
                "oldest_time": None
            }
        
        backups_sorted = sorted(all_backups, key=lambda x: x.stat().st_mtime, reverse=True)
        total_size = sum(b.stat().st_size for b in all_backups)
        
        return {
            "count": len(all_backups),
            "auto_count": len(auto_backups),
            "manual_count": len(manual_backups),
            "full_count": len(full_backups),
            "db_only_count": len(db_only_backups),
            "total_size": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "latest": backups_sorted[0].name if backups_sorted else None,
            "latest_time": datetime.fromtimestamp(backups_sorted[0].stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S') if backups_sorted else None,
            "oldest": backups_sorted[-1].name if backups_sorted else None,
            "oldest_time": datetime.fromtimestamp(backups_sorted[-1].stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S') if backups_sorted else None,
        }


# Global backup instance
backup_manager = DatabaseBackup()
