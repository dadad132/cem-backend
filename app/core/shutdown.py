"""
Graceful shutdown handler for the CRM application.
Ensures database backup before shutdown and handles cleanup properly.
"""
import signal
import sys
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class GracefulShutdown:
    """Handles graceful shutdown of the application"""
    
    def __init__(self):
        self.shutdown_event = asyncio.Event()
        self.is_shutting_down = False
        
    def signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        signal_name = signal.Signals(signum).name
        logger.info(f"Received {signal_name} signal. Initiating graceful shutdown...")
        
        if self.is_shutting_down:
            logger.warning("Shutdown already in progress. Please wait...")
            return
        
        self.is_shutting_down = True
        self.shutdown_event.set()
        
    async def shutdown_sequence(self):
        """Execute shutdown sequence"""
        logger.info("=" * 60)
        logger.info("GRACEFUL SHUTDOWN INITIATED")
        logger.info("=" * 60)
        
        try:
            # Step 1: Create final backup
            logger.info("Step 1: Creating final database backup...")
            from app.core.backup import backup_manager
            backup_file = backup_manager.create_backup(is_manual=False)
            if backup_file:
                logger.info(f"✅ Final backup created: {backup_file}")
            else:
                logger.warning("⚠️  Failed to create final backup")
            
            # Step 2: Stop auto-backup system
            logger.info("Step 2: Stopping automatic backup system...")
            await backup_manager.stop_auto_backup()
            logger.info("✅ Backup system stopped")
            
            # Step 3: Close database connections
            logger.info("Step 3: Closing database connections...")
            from app.core.database import engine
            await engine.dispose()
            logger.info("✅ Database connections closed")
            
            logger.info("=" * 60)
            logger.info("SHUTDOWN COMPLETE - All data saved safely")
            logger.info("=" * 60)
            
        except Exception as e:
            logger.error(f"❌ Error during shutdown: {e}")
            logger.error("Some data may not have been saved properly")
        
    def setup_handlers(self):
        """Setup signal handlers for graceful shutdown"""
        # Handle SIGTERM (systemd stop, kill command)
        signal.signal(signal.SIGTERM, self.signal_handler)
        # Handle SIGINT (Ctrl+C)
        signal.signal(signal.SIGINT, self.signal_handler)
        logger.info("✅ Graceful shutdown handlers registered")


# Global instance
shutdown_handler = GracefulShutdown()
