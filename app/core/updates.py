"""
Update management system for CRM Backend
Handles version checking and update notifications
"""
import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any
import httpx
from app.core.version import VERSION, UPDATE_CHECK_URL, ENABLE_AUTO_UPDATE_CHECK, UPDATE_CHECK_INTERVAL_HOURS

# Store update check results
_update_cache: Dict[str, Any] = {
    "last_check": None,
    "latest_version": None,
    "update_available": False,
    "release_notes": None,
    "download_url": None
}


async def check_for_updates() -> Dict[str, Any]:
    """
    Check for available updates from the update server.
    
    Returns:
        Dict with update information:
        {
            "current_version": "1.0.0",
            "latest_version": "1.1.0",
            "update_available": True,
            "release_notes": "Bug fixes and improvements",
            "download_url": "https://...",
            "last_check": datetime
        }
    """
    global _update_cache
    
    # Check cache validity
    if _update_cache["last_check"]:
        time_since_check = datetime.utcnow() - _update_cache["last_check"]
        if time_since_check < timedelta(hours=UPDATE_CHECK_INTERVAL_HOURS):
            return {
                "current_version": VERSION,
                **_update_cache
            }
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                UPDATE_CHECK_URL,
                params={"current_version": VERSION}
            )
            
            if response.status_code == 200:
                data = response.json()
                
                _update_cache.update({
                    "last_check": datetime.utcnow(),
                    "latest_version": data.get("latest_version"),
                    "update_available": data.get("update_available", False),
                    "release_notes": data.get("release_notes"),
                    "download_url": data.get("download_url")
                })
            else:
                # Server unreachable or error
                _update_cache["last_check"] = datetime.utcnow()
                
    except Exception as e:
        print(f"Update check failed: {e}")
        _update_cache["last_check"] = datetime.utcnow()
    
    return {
        "current_version": VERSION,
        **_update_cache
    }


def get_current_version() -> str:
    """Get the current version of the application."""
    return VERSION


def parse_version(version_str: str) -> tuple:
    """Parse version string to tuple for comparison."""
    try:
        return tuple(map(int, version_str.split('.')))
    except:
        return (0, 0, 0)


def is_newer_version(current: str, latest: str) -> bool:
    """Check if latest version is newer than current."""
    return parse_version(latest) > parse_version(current)


async def register_server(server_id: str, callback_url: Optional[str] = None):
    """
    Register this server instance with the update server.
    Allows central tracking of deployed instances.
    
    Args:
        server_id: Unique identifier for this server
        callback_url: Optional webhook URL for update notifications
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                UPDATE_CHECK_URL.replace("/latest-version", "/register"),
                json={
                    "server_id": server_id,
                    "version": VERSION,
                    "callback_url": callback_url,
                    "timestamp": datetime.utcnow().isoformat()
                }
            )
    except Exception as e:
        print(f"Server registration failed: {e}")


def save_update_info(info: Dict[str, Any]):
    """Save update information to local file for persistence."""
    update_file = Path("data") / "update_info.json"
    update_file.parent.mkdir(exist_ok=True)
    
    with open(update_file, 'w') as f:
        json.dump({
            **info,
            "last_check": info["last_check"].isoformat() if info.get("last_check") else None
        }, f, indent=2)


def load_update_info() -> Optional[Dict[str, Any]]:
    """Load update information from local file."""
    update_file = Path("data") / "update_info.json"
    
    if update_file.exists():
        try:
            with open(update_file, 'r') as f:
                data = json.load(f)
                if data.get("last_check"):
                    data["last_check"] = datetime.fromisoformat(data["last_check"])
                return data
        except Exception as e:
            print(f"Failed to load update info: {e}")
    
    return None


def check_github_updates() -> Optional[Dict[str, Any]]:
    """
    Check for updates from GitHub repository.
    Returns info about available updates.
    """
    import subprocess
    import os
    
    try:
        # Check if we're in a git repository
        if not os.path.exists('.git'):
            return None
        
        # Fetch latest from GitHub
        subprocess.run(['git', 'fetch', 'origin', 'main'], 
                      capture_output=True, timeout=30, check=False)
        
        # Get current commit
        local_result = subprocess.run(['git', 'rev-parse', 'HEAD'],
                                     capture_output=True, text=True, timeout=5)
        local_commit = local_result.stdout.strip()[:7]
        
        # Get remote commit
        remote_result = subprocess.run(['git', 'rev-parse', 'origin/main'],
                                       capture_output=True, text=True, timeout=5)
        remote_commit = remote_result.stdout.strip()[:7]
        
        # Check if behind
        update_available = local_commit != remote_commit
        
        # Count commits behind
        commits_behind = 0
        if update_available:
            count_result = subprocess.run(
                ['git', 'rev-list', '--count', f'{local_commit}..origin/main'],
                capture_output=True, text=True, timeout=5
            )
            if count_result.returncode == 0:
                commits_behind = int(count_result.stdout.strip())
        
        return {
            'local_commit': local_commit,
            'remote_commit': remote_commit,
            'update_available': update_available,
            'commits_behind': commits_behind,
            'checked_at': datetime.utcnow().isoformat()
        }
    except Exception as e:
        print(f"GitHub update check failed: {e}")
        return None


def trigger_github_update() -> bool:
    """
    Trigger the GitHub update script.
    Returns True if update was started successfully.
    """
    import subprocess
    import os
    import sys
    
    try:
        # Determine script path
        if os.name == 'nt':  # Windows
            return False  # Not supported on Windows (manual update only)
        
        script_path = os.path.expanduser('~/crm-backend/update_from_github.sh')
        
        if not os.path.exists(script_path):
            print(f"Update script not found: {script_path}")
            return False
        
        # Run update script in background with auto flag
        subprocess.Popen(
            ['/bin/bash', script_path, '--auto'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        
        return True
    except Exception as e:
        print(f"Failed to trigger update: {e}")
        return False
