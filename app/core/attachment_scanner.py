"""
Attachment Scanner - Automatically finds and fixes broken attachment paths.

This module scans for uploaded files and updates database records if paths are incorrect.
Runs automatically on server startup.
"""

import os
import logging
from pathlib import Path
from typing import Dict, List, Set, Optional
import sqlite3

logger = logging.getLogger(__name__)


class AttachmentScanner:
    """Scans for attachments and fixes broken paths in the database."""
    
    # Known upload directories to scan
    UPLOAD_DIRS = [
        'app/uploads/comments',
        'app/uploads/chat_messages', 
        'app/uploads/profile_pictures',
        'app/uploads/branding',
        'app/uploads/tickets',
        'uploads/comments',
        'uploads/chat_messages',
        'uploads/profile_pictures',
        'uploads/branding',
        'uploads/tickets',
    ]
    
    # Table configurations: (table_name, path_column, filename_column or None)
    ATTACHMENT_TABLES = [
        ('comment_attachment', 'file_path', 'filename'),
        ('chatmessageattachment', 'file_path', 'filename'),
        ('ticketattachment', 'file_path', 'filename'),
    ]
    
    def __init__(self, db_path: str = 'data.db', base_dir: Optional[Path] = None):
        self.db_path = db_path
        self.base_dir = base_dir or Path.cwd()
        self.found_files: Dict[str, str] = {}  # filename -> full_path
        
    def scan_upload_directories(self) -> Dict[str, str]:
        """Scan all upload directories and build a map of filename -> path."""
        logger.info("Scanning upload directories for attachments...")
        
        for upload_dir in self.UPLOAD_DIRS:
            dir_path = self.base_dir / upload_dir
            if dir_path.exists() and dir_path.is_dir():
                logger.debug(f"Scanning: {dir_path}")
                for file_path in dir_path.iterdir():
                    if file_path.is_file():
                        filename = file_path.name
                        # Store relative path from base_dir
                        relative_path = str(file_path.relative_to(self.base_dir))
                        # Use forward slashes for consistency
                        relative_path = relative_path.replace('\\', '/')
                        self.found_files[filename] = relative_path
                        logger.debug(f"  Found: {filename} -> {relative_path}")
        
        logger.info(f"Found {len(self.found_files)} attachment files")
        return self.found_files
    
    def fix_attachment_paths(self) -> Dict[str, int]:
        """Fix broken attachment paths in the database."""
        results = {
            'scanned': 0,
            'fixed': 0,
            'missing': 0,
            'ok': 0,
        }
        
        if not self.found_files:
            self.scan_upload_directories()
        
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            for table_name, path_col, filename_col in self.ATTACHMENT_TABLES:
                # Check if table exists
                cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (table_name,)
                )
                if not cursor.fetchone():
                    logger.debug(f"Table {table_name} does not exist, skipping")
                    continue
                
                # Get all attachments from this table
                try:
                    cursor.execute(f"SELECT id, {path_col}, {filename_col} FROM {table_name}")
                    attachments = cursor.fetchall()
                except sqlite3.OperationalError as e:
                    logger.warning(f"Error reading {table_name}: {e}")
                    continue
                
                for att_id, current_path, filename in attachments:
                    results['scanned'] += 1
                    
                    # Check if current path exists
                    full_path = self.base_dir / current_path if current_path else None
                    if full_path and full_path.exists():
                        results['ok'] += 1
                        continue
                    
                    # Try to find the file by its stored filename
                    found_path = None
                    
                    # First, try to find by the original filename in current_path
                    if current_path:
                        stored_filename = Path(current_path).name
                        if stored_filename in self.found_files:
                            found_path = self.found_files[stored_filename]
                    
                    # If not found, try by the filename column
                    if not found_path and filename:
                        # The stored filename might be original name, search by UUID part
                        for found_name, found_full_path in self.found_files.items():
                            if found_name == filename:
                                found_path = found_full_path
                                break
                    
                    if found_path:
                        # Update the database
                        try:
                            cursor.execute(
                                f"UPDATE {table_name} SET {path_col} = ? WHERE id = ?",
                                (found_path, att_id)
                            )
                            results['fixed'] += 1
                            logger.info(f"Fixed {table_name}[{att_id}]: {current_path} -> {found_path}")
                        except sqlite3.Error as e:
                            logger.error(f"Failed to update {table_name}[{att_id}]: {e}")
                    else:
                        results['missing'] += 1
                        logger.warning(f"Cannot find file for {table_name}[{att_id}]: {filename or current_path}")
            
            conn.commit()
            conn.close()
            
        except sqlite3.Error as e:
            logger.error(f"Database error during attachment scan: {e}")
        
        return results
    
    def get_attachment_report(self) -> str:
        """Generate a report of attachment status."""
        if not self.found_files:
            self.scan_upload_directories()
        
        report_lines = [
            "=" * 50,
            "ATTACHMENT SCANNER REPORT",
            "=" * 50,
            f"Base Directory: {self.base_dir}",
            f"Database: {self.db_path}",
            "",
            f"Files found on disk: {len(self.found_files)}",
            "",
        ]
        
        # Group files by directory
        by_dir: Dict[str, List[str]] = {}
        for filename, path in self.found_files.items():
            dir_name = str(Path(path).parent)
            if dir_name not in by_dir:
                by_dir[dir_name] = []
            by_dir[dir_name].append(filename)
        
        for dir_name, files in sorted(by_dir.items()):
            report_lines.append(f"  {dir_name}/: {len(files)} files")
        
        return "\n".join(report_lines)


def run_attachment_scan(db_path: str = 'data.db', fix_paths: bool = True) -> Dict[str, int]:
    """
    Run the attachment scanner.
    
    Args:
        db_path: Path to the SQLite database
        fix_paths: Whether to automatically fix broken paths
        
    Returns:
        Dictionary with scan results
    """
    scanner = AttachmentScanner(db_path=db_path)
    scanner.scan_upload_directories()
    
    logger.info(scanner.get_attachment_report())
    
    if fix_paths:
        results = scanner.fix_attachment_paths()
        logger.info(
            f"Attachment scan complete: "
            f"{results['scanned']} scanned, "
            f"{results['ok']} OK, "
            f"{results['fixed']} fixed, "
            f"{results['missing']} missing"
        )
        return results
    
    return {'scanned': 0, 'fixed': 0, 'missing': 0, 'ok': 0}


if __name__ == '__main__':
    # Allow running directly for testing
    logging.basicConfig(level=logging.DEBUG)
    results = run_attachment_scan()
    print(f"\nResults: {results}")
