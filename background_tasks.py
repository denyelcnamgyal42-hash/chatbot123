"""Background tasks for auto checkout and vectorstore refresh."""
import time
import threading
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import logging
from google_sheets import sheets_manager
from dense_retrieval import get_dense_retrieval
import config

logger = logging.getLogger(__name__)

class BackgroundTaskManager:
    """Manages background tasks like auto checkout and vectorstore refresh."""
    
    def __init__(self):
        self.running = False
        self.thread = None
        self.last_vectorstore_check = time.time()
        self.vectorstore_check_interval = 300  # Check every 5 minutes
        self.auto_checkout_interval = 3600  # Check every hour for checkout dates
        
    def start(self):
        """Start background tasks."""
        if self.running:
            logger.warning("⚠️ Background tasks already running")
            return
        
        self.running = True
        self.thread = threading.Thread(target=self._run_tasks, daemon=True)
        self.thread.start()
        logger.info("✅ Background tasks started (auto checkout & vectorstore refresh)")
    
    def stop(self):
        """Stop background tasks."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        logger.info("🛑 Background tasks stopped")
    
    def _run_tasks(self):
        """Main loop for background tasks."""
        while self.running:
            try:
                # Run auto checkout
                self._process_auto_checkout()
                
                # Check for sheet changes and refresh vectorstore if needed
                self._check_and_refresh_vectorstore()
                
                # Sleep for 1 minute before next check
                time.sleep(60)
                
            except Exception as e:
                logger.error(f"❌ Error in background task: {e}")
                import traceback
                traceback.print_exc()
                time.sleep(60)  # Continue after error
    
    def _process_auto_checkout(self):
        """Process auto checkout - make rooms available when checkout date arrives."""
        try:
            current_time = time.time()
            
            # Only check once per hour
            if not hasattr(self, '_last_checkout_check'):
                self._last_checkout_check = 0
            
            if current_time - self._last_checkout_check < self.auto_checkout_interval:
                return
            
            self._last_checkout_check = current_time
            
            logger.info("🔄 Checking for checkout dates...")
            
            # Get all monthly booking sheets
            all_sheets = sheets_manager.discover_sheets()
            booking_sheets = [
                s for s in all_sheets 
                if s.lower().startswith('bookings ') and s.lower() != 'pending bookings'
            ]
            
            today = datetime.now().date()
            checkouts_processed = 0
            
            for booking_sheet in booking_sheets:
                try:
                    bookings_data = sheets_manager.read_all_data(booking_sheet)
                    if not bookings_data or len(bookings_data) < 2:
                        continue
                    
                    headers = bookings_data[0]
                    
                    # Find column indices
                    check_out_idx = None
                    room_id_idx = None
                    status_idx = None
                    room_name_idx = None
                    
                    for idx, header in enumerate(headers):
                        header_lower = str(header).lower()
                        if 'check-out' in header_lower or 'check_out' in header_lower:
                            check_out_idx = idx
                        elif 'room id' in header_lower or 'room_id' in header_lower:
                            room_id_idx = idx
                        elif 'status' in header_lower:
                            status_idx = idx
                        elif 'room name' in header_lower or 'room_name' in header_lower:
                            room_name_idx = idx
                    
                    if check_out_idx is None or room_id_idx is None:
                        continue
                    
                    # Process each booking
                    for row in bookings_data[1:]:
                        if len(row) <= max(check_out_idx, room_id_idx):
                            continue
                        
                        check_out_str = str(row[check_out_idx]).strip()
                        room_id = str(row[room_id_idx]).strip()
                        status = str(row[status_idx]).strip().lower() if status_idx is not None and status_idx < len(row) else ''
                        
                        # Only process approved/confirmed bookings
                        if status not in ['approved', 'confirmed']:
                            continue
                        
                        if not check_out_str or not room_id:
                            continue
                        
                        # Parse checkout date
                        checkout_date = None
                        try:
                            # Try YYYY-MM-DD format
                            checkout_date = datetime.strptime(check_out_str, "%Y-%m-%d").date()
                        except:
                            try:
                                # Try other formats
                                for fmt in ["%B %d, %Y", "%b %d, %Y", "%d/%m/%Y", "%m/%d/%Y", "%d %B %Y"]:
                                    checkout_date = datetime.strptime(check_out_str, fmt).date()
                                    break
                            except:
                                continue
                        
                        if checkout_date is None:
                            continue
                        
                        # If checkout date has passed, make room available again
                        if checkout_date < today:
                            # Make room available
                            if self._make_room_available(room_id):
                                checkouts_processed += 1
                                logger.info(f"✅ Auto checkout: Room {room_id} is now available (checkout date: {check_out_str})")
                        
                        # Also handle checkout happening today (after checkout time, e.g., 11 AM)
                        elif checkout_date == today:
                            current_hour = datetime.now().hour
                            # If it's past checkout time (11 AM), make room available
                            if current_hour >= 11:
                                if self._make_room_available(room_id):
                                    checkouts_processed += 1
                                    logger.info(f"✅ Auto checkout: Room {room_id} is now available (checkout date: {check_out_str})")
                
                except Exception as e:
                    logger.error(f"❌ Error processing booking sheet {booking_sheet}: {e}")
                    continue
            
            if checkouts_processed > 0:
                logger.info(f"✅ Processed {checkouts_processed} auto checkouts")
            else:
                logger.debug("📅 No checkouts to process")
                
        except Exception as e:
            logger.error(f"❌ Error in auto checkout process: {e}")
            import traceback
            traceback.print_exc()
    
    def _make_room_available(self, room_id: str) -> bool:
        """Make a room available again by setting Current Available to 'Yes'."""
        try:
            # Find all room sheets
            all_sheets = sheets_manager.discover_sheets()
            room_sheets = [s for s in all_sheets if sheets_manager.detect_sheet_type(s) == 'hotel']
            
            for sheet_name in room_sheets:
                try:
                    worksheet = sheets_manager.get_worksheet(sheet_name)
                    data = worksheet.get_all_values()
                    
                    if not data or len(data) < 2:
                        continue
                    
                    headers = data[0]
                    
                    # Find Room ID and Current Available columns
                    room_id_col = None
                    available_col = None
                    
                    for idx, header in enumerate(headers):
                        header_lower = str(header).lower()
                        if 'room id' in header_lower or 'room_id' in header_lower:
                            room_id_col = idx
                        elif 'current available' in header_lower or 'available' in header_lower:
                            available_col = idx
                    
                    if room_id_col is None or available_col is None:
                        continue
                    
                    # Find matching room
                    for row_idx, row in enumerate(data[1:], start=2):
                        if room_id_col < len(row) and str(row[room_id_col]).strip() == str(room_id).strip():
                            # Make room available
                            worksheet.update_cell(row_idx, available_col + 1, 'Yes')
                            sheets_manager._invalidate_sheet_cache(sheet_name)
                            logger.info(f"📊 Made room {room_id} available in {sheet_name}")
                            return True
                
                except Exception as e:
                    logger.warning(f"⚠️ Error processing sheet {sheet_name}: {e}")
                    continue
            
            logger.warning(f"❌ Room ID '{room_id}' not found in any room sheet")
            return False
            
        except Exception as e:
            logger.error(f"❌ Error making room available: {e}")
            return False
    
    def _check_and_refresh_vectorstore(self):
        """Check for sheet changes and refresh vectorstore if needed."""
        try:
            current_time = time.time()
            
            # Only check every 5 minutes
            if current_time - self.last_vectorstore_check < self.vectorstore_check_interval:
                return
            
            self.last_vectorstore_check = current_time
            
            # Check if sheets have been modified
            # We'll check the modification time of cached data
            if self._should_refresh_vectorstore():
                logger.info("🔄 Detected sheet changes, refreshing vectorstore...")
                try:
                    retriever = get_dense_retrieval()
                    retriever.refresh_index(force=True)
                    logger.info("✅ Vectorstore refreshed successfully")
                except Exception as e:
                    logger.error(f"❌ Error refreshing vectorstore: {e}")
            
        except Exception as e:
            logger.error(f"❌ Error checking for sheet changes: {e}")
    
    def _should_refresh_vectorstore(self) -> bool:
        """Check if vectorstore needs to be refreshed based on sheet modifications."""
        try:
            # Check modification time of cached sheet data
            if hasattr(sheets_manager, '_sheet_data_cache'):
                cache = sheets_manager._sheet_data_cache
                
                # If cache was recently invalidated, refresh vectorstore
                if hasattr(sheets_manager, '_last_cache_invalidation'):
                    invalidation_time = sheets_manager._last_cache_invalidation
                    if invalidation_time and (time.time() - invalidation_time) < 300:  # Within 5 minutes
                        return True
                
                # Check if any cached data is old (older than 10 minutes means sheet might have changed)
                current_time = time.time()
                for sheet_name, (data, timestamp) in cache.items():
                    # Skip booking sheets from check
                    if 'booking' in str(sheet_name).lower() or 'pending' in str(sheet_name).lower():
                        continue
                    
                    # If cached data is old and it's a hotel/room sheet, refresh
                    if (current_time - timestamp) > 600:  # 10 minutes
                        return True
                
                return False
            
            return False
            
        except Exception as e:
            logger.warning(f"⚠️ Error checking if refresh needed: {e}")
            return False

# Global instance
_task_manager = None

def get_task_manager() -> BackgroundTaskManager:
    """Get or create the global task manager instance."""
    global _task_manager
    if _task_manager is None:
        _task_manager = BackgroundTaskManager()
    return _task_manager
