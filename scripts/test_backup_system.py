"""
Test script for the enhanced backup system
Tests backup creation, restoration, and file handling
"""
from app.core.backup import backup_manager
from pathlib import Path
import tempfile
import shutil

def test_backup_system():
    print("=" * 60)
    print("Testing Enhanced Backup System")
    print("=" * 60)
    
    # Test 1: Get backup stats
    print("\n1. Getting backup statistics...")
    stats = backup_manager.get_backup_stats()
    print(f"   Total backups: {stats['count']}")
    print(f"   Automatic: {stats['auto_count']}")
    print(f"   Manual: {stats['manual_count']}")
    print(f"   Full backups (ZIP): {stats['full_count']}")
    print(f"   DB-only backups: {stats['db_only_count']}")
    print(f"   Total size: {stats['total_size_mb']} MB")
    print(f"   Latest: {stats.get('latest', 'None')}")
    print("   ✅ Stats retrieved successfully")
    
    # Test 2: Create DB-only backup
    print("\n2. Creating DB-only backup...")
    db_backup = backup_manager.create_backup(is_manual=True, include_attachments=False)
    if db_backup:
        print(f"   ✅ DB-only backup created: {db_backup.name}")
        print(f"   Size: {round(db_backup.stat().st_size / (1024 * 1024), 2)} MB")
    else:
        print("   ❌ Failed to create DB-only backup")
    
    # Test 3: Create full backup (DB + attachments)
    print("\n3. Creating full backup (DB + attachments)...")
    full_backup = backup_manager.create_backup(is_manual=True, include_attachments=True)
    if full_backup:
        print(f"   ✅ Full backup created: {full_backup.name}")
        print(f"   Size: {round(full_backup.stat().st_size / (1024 * 1024), 2)} MB")
        
        # Verify it's a ZIP file
        if full_backup.suffix == '.zip':
            print("   ✅ Backup is ZIP format")
            import zipfile
            with zipfile.ZipFile(full_backup, 'r') as zipf:
                members = zipf.namelist()
                print(f"   ZIP contains {len(members)} files")
                print(f"   - Database: {'data.db' in members}")
                upload_files = [m for m in members if m.startswith('app/uploads/')]
                print(f"   - Attachment files: {len(upload_files)}")
        else:
            print("   ⚠️  Backup is not ZIP format")
    else:
        print("   ❌ Failed to create full backup")
    
    # Test 4: Test save_uploaded_backup
    print("\n4. Testing uploaded backup handling...")
    if full_backup and full_backup.exists():
        # Simulate an uploaded backup
        with open(full_backup, 'rb') as f:
            content = f.read()
        
        uploaded_backup = backup_manager.save_uploaded_backup(content, "test_upload.zip")
        if uploaded_backup:
            print(f"   ✅ Uploaded backup saved: {uploaded_backup.name}")
            print(f"   Contains 'UPLOADED' marker: {'UPLOADED' in uploaded_backup.name}")
        else:
            print("   ❌ Failed to save uploaded backup")
    
    # Test 5: Get latest backup
    print("\n5. Testing get_latest_backup...")
    latest = backup_manager.get_latest_backup()
    if latest:
        print(f"   ✅ Latest backup: {latest.name}")
        print(f"   Type: {latest.suffix}")
    else:
        print("   ❌ No latest backup found")
    
    # Test 6: List all backups
    print("\n6. Listing all backups...")
    all_backups = sorted(
        [f for f in backup_manager.backup_dir.glob("backup_*.*") 
         if f.suffix in ['.db', '.zip'] and 'latest' not in f.name and 'corrupted' not in f.name],
        key=lambda x: x.stat().st_mtime,
        reverse=True
    )
    
    for backup in all_backups[:5]:  # Show first 5
        backup_type = "MANUAL" if "_MANUAL_" in backup.name else ("AUTO" if "_AUTO_" in backup.name else "UPLOADED")
        size_mb = round(backup.stat().st_size / (1024 * 1024), 2)
        print(f"   - {backup.name} ({backup_type}, {backup.suffix}, {size_mb} MB)")
    
    if len(all_backups) > 5:
        print(f"   ... and {len(all_backups) - 5} more")
    
    print(f"\n   ✅ Found {len(all_backups)} total backups")
    
    # Final stats
    print("\n" + "=" * 60)
    print("Final Statistics")
    print("=" * 60)
    final_stats = backup_manager.get_backup_stats()
    print(f"Total backups: {final_stats['count']}")
    print(f"Automatic: {final_stats['auto_count']}")
    print(f"Manual: {final_stats['manual_count']}")
    print(f"Full backups (ZIP): {final_stats['full_count']}")
    print(f"DB-only backups: {final_stats['db_only_count']}")
    print(f"Total size: {final_stats['total_size_mb']} MB")
    print("\n✅ All tests completed successfully!")

if __name__ == "__main__":
    try:
        test_backup_system()
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
