"""Google Sheets integration for reading and writing data."""
import gspread
from google.oauth2.service_account import Credentials
from typing import List, Dict, Optional, Any, Tuple
import config
import os
import time
import json
import tempfile
from datetime import datetime, timedelta, date
from functools import lru_cache
import logging

logger = logging.getLogger(__name__)

class GoogleSheetsManager:
    """Manages Google Sheets operations with caching, rate limiting, and error handling."""
    
    def __init__(self, cache_ttl: int = 60, max_cache_size: int = 100):
        """Initialize Google Sheets client (lazy connection)."""
        self._client = None
        self._sheet = None
        self._initialized = False
        self.cache_ttl = cache_ttl
        self.max_cache_size = max_cache_size
        self._last_connection_attempt = 0
        self._connection_cooldown = 5  # seconds between connection attempts
        
        # Rate limiting for Google Sheets API (60 requests per minute)
        self._request_times = []  # Track request timestamps
        self._max_requests_per_minute = 50  # Keep it under 60 to be safe
        self._min_request_interval = 1.2  # Minimum seconds between requests (50/min = 1.2s)
        self._last_request_time = 0
        
        # Cache for sheet data
        self._sheet_data_cache = {}  # {sheet_name: (data, timestamp)}
        self._product_cache = {}
        self._last_cache_invalidation = None  # Track when cache was last invalidated
        
        # Retry configuration
        self._max_retries = 3
        self._base_retry_delay = 2  # seconds
        
        logger.info("GoogleSheetsManager initialized")
    
    def _ensure_connected(self, force_reconnect: bool = False):
        """Ensure connection to Google Sheets is established."""
        current_time = time.time()
        
        # Check if we should attempt reconnection
        if (not force_reconnect and 
            self._initialized and 
            (current_time - self._last_connection_attempt) < self._connection_cooldown):
            return
        
        self._last_connection_attempt = current_time
        
        try:
            # Check if credentials are provided as environment variable (for Render deployment)
            credentials_json = os.getenv("GOOGLE_SHEETS_CREDENTIALS_JSON")
            credentials_path = None
            
            if credentials_json:
                # Create temporary file from environment variable
                try:
                    creds_data = json.loads(credentials_json)
                    temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
                    json.dump(creds_data, temp_file)
                    temp_file.close()
                    credentials_path = temp_file.name
                    logger.info("✅ Using credentials from GOOGLE_SHEETS_CREDENTIALS_JSON environment variable")
                except json.JSONDecodeError as e:
                    raise ValueError(f"Invalid JSON in GOOGLE_SHEETS_CREDENTIALS_JSON: {e}")
            elif os.path.exists(config.GOOGLE_SHEETS_CREDENTIALS_PATH):
                # Use local file if it exists
                credentials_path = config.GOOGLE_SHEETS_CREDENTIALS_PATH
                logger.info(f"✅ Using credentials from file: {credentials_path}")
            else:
                raise FileNotFoundError(
                    f"Google Sheets credentials not found. Set GOOGLE_SHEETS_CREDENTIALS_JSON environment variable or provide file at: {config.GOOGLE_SHEETS_CREDENTIALS_PATH}"
                )
            
            # Check if sheet ID is configured
            if not config.GOOGLE_SHEET_ID:
                raise ValueError("GOOGLE_SHEET_ID is not set in your .env file")
            
            # Configure scopes
            scope = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive.file"
            ]
            
            # Load credentials
            creds = Credentials.from_service_account_file(
                credentials_path, 
                scopes=scope
            )
            
            # Create client
            self._client = gspread.authorize(creds)
            
            # Open spreadsheet
            self._sheet = self._client.open_by_key(config.GOOGLE_SHEET_ID)
            
            # Test connection
            self._sheet.title  # This will raise if connection fails
            
            self._initialized = True
            logger.info(f"✅ Connected to Google Sheet: {self._sheet.title}")
            
        except gspread.exceptions.SpreadsheetNotFound:
            logger.error(f"❌ Google Sheet with ID '{config.GOOGLE_SHEET_ID}' not found")
            raise ValueError(
                f"Google Sheet not found. Please check:\n"
                f"1. Sheet ID is correct\n"
                f"2. Sheet is shared with service account\n"
                f"3. Service account email: {self._get_service_account_email()}"
            )
        except Exception as e:
            logger.error(f"❌ Failed to connect to Google Sheets: {str(e)}")
            import traceback
            traceback.print_exc()
            raise
    
    def _get_service_account_email(self) -> str:
        """Get service account email from credentials file or environment variable."""
        try:
            credentials_json = os.getenv("GOOGLE_SHEETS_CREDENTIALS_JSON")
            if credentials_json:
                creds_data = json.loads(credentials_json)
                return creds_data.get('client_email', 'Not found')
            elif os.path.exists(config.GOOGLE_SHEETS_CREDENTIALS_PATH):
                with open(config.GOOGLE_SHEETS_CREDENTIALS_PATH, 'r') as f:
                    creds_data = json.load(f)
                    return creds_data.get('client_email', 'Not found')
            else:
                return 'Not found'
        except:
            return 'Not found'
    
    def _rate_limit(self):
        """Rate limiting to avoid exceeding API limits."""
        current_time = time.time()
        time_since_last_request = current_time - self._last_request_time
        
        if time_since_last_request < self._min_request_interval:
            sleep_time = self._min_request_interval - time_since_last_request
            time.sleep(sleep_time)
        
        self._last_request_time = time.time()
        
        # Clean old request times (older than 1 minute)
        self._request_times = [t for t in self._request_times if current_time - t < 60]
        
        # Check if we're at the limit
        if len(self._request_times) >= self._max_requests_per_minute:
            oldest_request = min(self._request_times)
            sleep_time = 60 - (current_time - oldest_request) + 1
            if sleep_time > 0:
                time.sleep(sleep_time)
                self._request_times = []
        
        self._request_times.append(time.time())
    
    def get_worksheet(self, sheet_name: str):
        """Get a worksheet by name."""
        self._ensure_connected()
        self._rate_limit()
        return self._sheet.worksheet(sheet_name)
    
    def discover_sheets(self) -> List[str]:
        """Discover all sheets in the spreadsheet."""
        try:
            self._ensure_connected()
            worksheets = self._sheet.worksheets()
            return [ws.title for ws in worksheets]
        except Exception as e:
            logger.error(f"❌ Error discovering sheets: {e}")
            return []
    
    def detect_sheet_type(self, sheet_name: str) -> str:
        """Detect the type of sheet based on its name and structure."""
        sheet_name_lower = sheet_name.lower()
        
        # Check for booking/reservation sheets
        if any(word in sheet_name_lower for word in ['booking', 'reservation', 'order']):
            return 'booking'
        
        # Check for hotel/room sheets
        if any(word in sheet_name_lower for word in ['hotel', 'room', 'accommodation', 'villa', 'suite', 'allocation']):
            return 'hotel'
        
        # Try to detect by structure
        try:
            worksheet = self.get_worksheet(sheet_name)
            data = worksheet.get_all_values()
            
            if not data or len(data) < 2:
                return 'unknown'
            
            headers = [h.lower() for h in data[0]]
            
            # Check for booking indicators
            if any(word in ' '.join(headers) for word in ['booking id', 'check-in', 'check-out', 'customer name']):
                return 'booking'
            
            # Check for hotel/room indicators
            if any(word in ' '.join(headers) for word in ['room id', 'room name', 'room type', 'price', 'available']):
                return 'hotel'
            
        except:
            pass
        
        return 'unknown'
    
    def read_all_data(self, sheet_name: str, use_cache: bool = True) -> Optional[List[List[str]]]:
        """Read all data from a sheet with caching."""
        cache_key = f"{sheet_name}_data"
        
        if use_cache:
            cached = self._sheet_data_cache.get(cache_key)
            if cached:
                data, timestamp = cached
                if time.time() - timestamp < self.cache_ttl:
                    return data
        
        try:
            self._rate_limit()
            worksheet = self.get_worksheet(sheet_name)
            data = worksheet.get_all_values()
            
            if use_cache:
                self._sheet_data_cache[cache_key] = (data, time.time())
            
            return data
        except Exception as e:
            logger.error(f"❌ Error reading sheet '{sheet_name}': {e}")
            return None
    
    def _invalidate_sheet_cache(self, sheet_name: str = None):
        """Invalidate cache for a specific sheet or all sheets."""
        if sheet_name:
            cache_key = f"{sheet_name}_data"
            self._sheet_data_cache.pop(cache_key, None)
            # Also track when cache was invalidated
            if not hasattr(self, '_last_sheet_update_time'):
                self._last_sheet_update_time = {}
            self._last_sheet_update_time[sheet_name] = time.time()
        else:
            self._sheet_data_cache.clear()
        
        logger.debug(f"🗑️ Cache invalidated for: {sheet_name or 'all sheets'}")
    
    @property
    def last_sheet_update_time(self):
        """Get the last update time for all sheets."""
        return getattr(self, '_last_sheet_update_time', {})
    
    def get_sheet_structure(self, sheet_name: str) -> Dict[str, Any]:
        """Get structure information about a sheet (headers, column indices)."""
        try:
            data = self.read_all_data(sheet_name, use_cache=True)
            if not data or len(data) < 1:
                return {'headers': [], 'name_column': None, 'price_column': None, 'qty_column': None}
            
            headers = data[0]
            structure = {'headers': headers}
            
            # Find name column
            name_col = None
            for idx, header in enumerate(headers):
                header_lower = str(header).lower()
                if any(word in header_lower for word in ['name', 'room name', 'room_type', 'room type', 'product']):
                    name_col = idx
                    break
            
            # Find price column
            price_col = None
            for idx, header in enumerate(headers):
                header_lower = str(header).lower()
                if any(word in header_lower for word in ['price', 'cost', 'rate', 'amount']):
                    price_col = idx
                    break
            
            # Find quantity/availability column
            qty_col = None
            for idx, header in enumerate(headers):
                header_lower = str(header).lower()
                if any(word in header_lower for word in ['quantity', 'available', 'qty', 'stock', 'inventory']):
                    qty_col = idx
                    break
            
            structure['name_column'] = name_col
            structure['price_column'] = price_col
            structure['qty_column'] = qty_col
            
            return structure
            
        except Exception as e:
            logger.error(f"❌ Error getting sheet structure for '{sheet_name}': {e}")
            return {'headers': [], 'name_column': None, 'price_column': None, 'qty_column': None}
    
    def get_product_info(self, sheet_name: str, product_name: str) -> Optional[Dict[str, str]]:
        """Get product information by name (legacy method, kept for compatibility)."""
        cache_key = f"{sheet_name}_{product_name.lower()}"
        
        # Check cache
        if hasattr(self, '_product_cache'):
            cached = self._product_cache.get(cache_key)
            if cached:
                product_dict, timestamp = cached
                if time.time() - timestamp < self.cache_ttl:
                    return product_dict
        
        try:
            # Use cached sheet data if available
            data = self.read_all_data(sheet_name, use_cache=True)
            if not data or len(data) < 2:
                return None
            
            headers = data[0]
            product_name_lower = product_name.lower()
            
            # Find product
            for row in data[1:]:
                if not any(row):
                    continue
                
                # Check different possible name columns
                for header_idx, header in enumerate(headers):
                    if header.lower() in ['name', 'product', 'product_name', 'item']:
                        if row[header_idx].lower() == product_name_lower:
                            product_dict = dict(zip(headers, row))
                            
                            # Cache the result
                            if not hasattr(self, '_product_cache'):
                                self._product_cache = {}
                            self._product_cache[cache_key] = (product_dict, time.time())
                            
                            # Clean old cache entries
                            self._clean_product_cache()
                            
                            return product_dict
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Error getting product info for '{product_name}': {e}")
            return None
    
    def _clean_product_cache(self):
        """Clean old product cache entries."""
        if not hasattr(self, '_product_cache'):
            return
        
        current_time = time.time()
        keys_to_delete = []
        
        for key, (_, timestamp) in self._product_cache.items():
            if current_time - timestamp > self.cache_ttl:
                keys_to_delete.append(key)
        
        for key in keys_to_delete:
            del self._product_cache[key]
        
        # Limit cache size
        if len(self._product_cache) > self.max_cache_size:
            # Remove oldest entries
            sorted_keys = sorted(
                self._product_cache.keys(),
                key=lambda k: self._product_cache[k][1]
            )
            for key in sorted_keys[:len(self._product_cache) - self.max_cache_size]:
                del self._product_cache[key]
    
    def search_data(self, sheet_name: str, search_term: str, column_name: str = None) -> List[Dict]:
        """Search for data in a worksheet."""
        try:
            data = self.read_all_data(sheet_name, use_cache=True)
            if not data:
                return []
            
            headers = data[0]
            results = []
            search_term_lower = search_term.lower()
            
            # Determine column index if specified
            column_index = None
            if column_name:
                for idx, header in enumerate(headers):
                    if header.lower() == column_name.lower():
                        column_index = idx
                        break
            
            for row_idx, row in enumerate(data[1:], start=2):
                if not any(row):
                    continue
                
                if column_index is not None:
                    # Search in specific column
                    cell_value = str(row[column_index] if column_index < len(row) else "")
                    if search_term_lower in cell_value.lower():
                        results.append({
                            'row': row_idx,
                            'data': dict(zip(headers, row[:len(headers)]))
                        })
                else:
                    # Search in all columns
                    found = False
                    for cell_value in row:
                        if search_term_lower in str(cell_value).lower():
                            found = True
                            break
                    
                    if found:
                        results.append({
                            'row': row_idx,
                            'data': dict(zip(headers, row[:len(headers)]))
                        })
            
            return results
            
        except Exception as e:
            logger.error(f"❌ Error searching data in '{sheet_name}': {e}")
            return []
    
    def create_booking(self, customer_name: str, phone: str, check_in: str, check_out: str, 
                      room_type: str, room_name: str, room_id: str, num_rooms: int = 1, 
                      guests: int = 1, price: float = 0.0, status: str = "pending") -> Optional[str]:
        """Create a booking in the pending bookings sheet."""
        try:
            # IMPORTANT: Check room availability BEFORE creating booking to prevent double bookings
            # Check both confirmed bookings and booked dates column
            is_available_by_bookings, msg1 = self.check_room_availability_by_date(room_id, check_in, check_out)
            is_available_by_dates, msg2 = self.check_room_availability_from_booked_dates_column(room_id, check_in, check_out)
            
            # If the specific room is not available, try to find another available room of the same type
            if not is_available_by_bookings or not is_available_by_dates:
                logger.warning(f"⚠️ Room {room_id} is not available. Trying to find another available {room_type}...")
                
                # Map room_type to search term for get_available_rooms_by_type
                room_type_map = {
                    'Twin Room': 'twin',
                    'Double Room': 'double',
                    'Two Bed Room Villa': 'villa',
                    'Twin': 'twin',
                    'Double': 'double',
                    'Villa': 'villa'
                }
                search_type = room_type_map.get(room_type, room_type.lower())
                
                # Get available rooms of the same type
                available_rooms = self.get_available_rooms_by_type(search_type, check_in, check_out)
                
                if available_rooms and len(available_rooms) > 0:
                    # Use the first available room
                    new_room = available_rooms[0]
                    new_room_id = new_room.get('room_id', '')
                    new_room_name = new_room.get('room_name', room_name)
                    logger.info(f"✅ Found available room: {new_room_id} ({new_room_name})")
                    room_id = new_room_id
                    room_name = new_room_name
                    # Update price if available from the new room
                    if new_room.get('price'):
                        try:
                            price_per_night = float(str(new_room['price']).replace(',', '').replace('Nu.', '').strip())
                            # Recalculate total price based on number of nights
                            check_in_date_obj = datetime.strptime(check_in, "%Y-%m-%d").date()
                            check_out_date_obj = datetime.strptime(check_out, "%Y-%m-%d").date()
                            nights = (check_out_date_obj - check_in_date_obj).days
                            if nights <= 0:
                                nights = 1  # Minimum 1 night
                            price = price_per_night * nights * num_rooms
                            logger.info(f"💰 Updated price: {price_per_night} per night × {nights} nights × {num_rooms} rooms = {price}")
                        except Exception as e:
                            logger.warning(f"⚠️ Error updating price: {e}")
                            pass
                else:
                    # No rooms of this type available
                    logger.warning(f"⚠️ Cannot create booking: No {room_type} rooms available for these dates.")
                    return None
            
            pending_sheet = self._get_or_create_pending_bookings_sheet()
            worksheet = self.get_worksheet(pending_sheet)
            
            # Generate booking ID
            booking_id = f"BK{int(time.time() * 1000) % 1000000}"
            
            # Create booking row
            booking_row = [
                booking_id,
                customer_name,
                phone,
                check_in,  # YYYY-MM-DD format
                check_out,  # YYYY-MM-DD format
                room_type,
                room_name,
                room_id,
                str(num_rooms),
                str(guests),
                str(price),
                status,
                datetime.now().isoformat(),
                ""  # Notes
            ]
            
            # Insert booking in sorted order (by month/date)
            self._insert_booking_sorted(worksheet, booking_row)
            
            self._invalidate_sheet_cache(pending_sheet)
            logger.info(f"✅ Created booking {booking_id} for {customer_name}")
            return booking_id
            
        except Exception as e:
            logger.error(f"❌ Error creating booking: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _insert_booking_sorted(self, worksheet, booking_row: List[str]):
        """Insert booking in sorted order by check-in date (newest first)."""
        try:
            data = worksheet.get_all_values()
            
            # Check if sheet is empty or has no headers
            if not data or len(data) == 0:
                # Sheet is completely empty, add headers first
                standard_headers = ['Booking ID', 'Customer Name', 'Phone', 'Check-in', 'Check-out', 
                                  'Room Type', 'Room Name', 'Room ID', 'Num Rooms', 'Guests', 
                                  'Price', 'Status', 'Created At', 'Notes']
                worksheet.append_row(standard_headers)
                # Format headers (bold)
                worksheet.format('1:1', {'textFormat': {'bold': True}})
                data = worksheet.get_all_values()
            
            # Check if first row is headers
            if len(data) > 0:
                first_row = data[0]
                has_headers = any(h in ' '.join([str(x).strip() for x in first_row[:5]]) for h in ['Booking ID', 'Customer Name'])
                if not has_headers:
                    # Headers are missing, add them at the top
                    standard_headers = ['Booking ID', 'Customer Name', 'Phone', 'Check-in', 'Check-out', 
                                      'Room Type', 'Room Name', 'Room ID', 'Num Rooms', 'Guests', 
                                      'Price', 'Status', 'Created At', 'Notes']
                    worksheet.insert_row(standard_headers, 1)
                    # Format headers (bold)
                    worksheet.format('1:1', {'textFormat': {'bold': True}})
                    data = worksheet.get_all_values()
            
            check_in_str = booking_row[3]  # Check-in is at index 3
            
            # Parse check-in date
            try:
                check_in_date = datetime.strptime(check_in_str, "%Y-%m-%d")
            except:
                check_in_date = datetime.now()
            
            # Find or create month section
            month_section_row = self._find_or_create_month_section(worksheet, check_in_date)
            
            # Find insertion point within month section
            insert_row = self._find_insertion_point_in_month_section(worksheet, month_section_row, check_in_date)
            
            # Insert the booking
            worksheet.insert_row(booking_row, insert_row)
            
        except Exception as e:
            logger.error(f"❌ Error inserting sorted booking: {e}")
            import traceback
            traceback.print_exc()
            # Fallback to appending
            worksheet.append_row(booking_row)
    
    def _find_or_create_month_section(self, worksheet, check_in_date: datetime) -> int:
        """Find or create a month section header for the given date. Returns the row number after the header."""
        try:
            data = worksheet.get_all_values()
            month_name = check_in_date.strftime("%B")  # January, February, etc.
            year = check_in_date.year
            month_header_text = f"{month_name}, {year}"
            
            # Standard headers
            standard_headers = ['Booking ID', 'Customer Name', 'Phone', 'Check-in', 'Check-out', 
                              'Room Type', 'Room Name', 'Room ID', 'Num Rooms', 'Guests', 
                              'Price', 'Status', 'Created At', 'Notes']
            
            # Check if month section exists
            month_row = None
            for idx, row in enumerate(data, start=1):
                if row and len(row) > 0 and str(row[0]).strip() == month_header_text:
                    month_row = idx
                    # Return row after empty row (which is after month header)
                    # Headers are at the top (row 1), not in each section
                    if idx + 1 < len(data):
                        return idx + 2  # Row after empty row
                    else:
                        return idx + 2  # Create empty row if needed
            
            if month_row:
                return month_row + 2  # Row after empty row
            
            # Month section doesn't exist, create it
            # Find where to insert (should be in chronological order, newest first)
            insert_pos = 1  # Default to top
            
            for idx, row in enumerate(data, start=1):
                if row and len(row) > 0:
                    cell_value = str(row[0]).strip()
                    # Check if it's a month header
                    if ',' in cell_value and any(month in cell_value for month in ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']):
                        try:
                            # Parse the month/year from header
                            existing_month_str = cell_value.split(',')[0].strip()
                            existing_year = int(cell_value.split(',')[1].strip())
                            existing_month_num = datetime.strptime(existing_month_str, "%B").month
                            existing_date = datetime(existing_year, existing_month_num, 1)
                            
                            # If current month is newer, insert before this one
                            if check_in_date >= existing_date:
                                insert_pos = idx
                                break
                            insert_pos = idx + 1
                        except:
                            pass
            
            # Insert month header
            month_header_row = [month_header_text] + [''] * (len(standard_headers) - 1)
            worksheet.insert_row(month_header_row, insert_pos)
            
            # Merge cells for month header (merge all columns)
            worksheet.merge_cells(insert_pos, 1, insert_pos, len(standard_headers))
            
            # Format month header (bold, centered)
            worksheet.format(f'{insert_pos}:{insert_pos}', {
                'horizontalAlignment': 'CENTER',
                'textFormat': {'bold': True}
            })
            
            # Check if headers already exist at the top of the sheet
            data = worksheet.get_all_values()
            has_top_headers = False
            if len(data) > 0:
                first_row = data[0]
                has_top_headers = any(h in ' '.join([str(x).strip() for x in first_row[:5]]) for h in ['Booking ID', 'Customer Name'])
            
            # Insert empty row
            empty_row = [''] * len(standard_headers)
            worksheet.insert_row(empty_row, insert_pos + 1)
            
            # Only insert headers if they don't exist at the top
            if not has_top_headers:
                # Insert headers at the very top (row 1)
                worksheet.insert_row(standard_headers, 1)
                # Format headers (bold)
                worksheet.format('1:1', {'textFormat': {'bold': True}})
                # Adjust insert_pos since we added a row at the top
                insert_pos += 1
            
            # Return the row number after empty row (headers are at top, not here)
            return insert_pos + 2
            
        except Exception as e:
            logger.error(f"❌ Error finding/creating month section: {e}")
            # Fallback: return row 1
            return 1
    
    def _find_insertion_point_in_month_section(self, worksheet, section_start_row: int, check_in_date: datetime) -> int:
        """Find the insertion point within a month section (sorted by date, newest first)."""
        try:
            data = worksheet.get_all_values()
            insert_row = section_start_row
            
            # Look through rows in this section (until we hit next month header or end)
            for idx in range(section_start_row - 1, len(data)):
                row = data[idx]
                if not row or len(row) < 4:
                    continue
                
                # Check if this is a month header (starts new section)
                if row[0] and ',' in str(row[0]) and any(month in str(row[0]) for month in ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']):
                    break
                
                # Check if this is a header row
                if any(h in ' '.join([str(x).strip() for x in row[:5]]) for h in ['Booking ID', 'Customer Name']):
                    continue
                
                # Try to parse check-in date from this row
                if len(row) > 3:
                    check_in_str = str(row[3]).strip()
                    try:
                        row_check_in = datetime.strptime(check_in_str, "%Y-%m-%d")
                        # If this row's date is older or equal, insert before it
                        if check_in_date >= row_check_in:
                            insert_row = idx + 1
                            break
                        insert_row = idx + 2
                    except:
                        # Can't parse date, skip this row
                        continue
            
            return insert_row
            
        except Exception as e:
            logger.error(f"❌ Error finding insertion point: {e}")
            return section_start_row
    
    def check_room_availability_by_date(self, room_id: str, check_in: str, check_out: str) -> Tuple[bool, str]:
        """
        Check if a room is available for the given date range.
        Returns (is_available, message).
        
        This function checks all confirmed bookings to see if there's any overlap
        with the requested dates, regardless of the 'Current Available' field.
        """
        try:
            # Parse dates
            try:
                check_in_date = datetime.strptime(check_in, "%Y-%m-%d").date()
                check_out_date = datetime.strptime(check_out, "%Y-%m-%d").date()
            except ValueError:
                return False, "Invalid date format. Please use YYYY-MM-DD format."
            
            # Validate dates
            if check_in_date >= check_out_date:
                return False, "Check-in date must be before check-out date."
            
            if check_in_date < datetime.now().date():
                return False, "Check-in date cannot be in the past."
            
            # Get all bookings for this room (both confirmed and pending)
            all_sheets = self.discover_sheets()
            booking_sheets = [s for s in all_sheets if 'booking' in s.lower()]
            
            # Check each booking sheet (both confirmed monthly sheets and pending bookings)
            for sheet_name in booking_sheets:
                try:
                    data = self.read_all_data(sheet_name, use_cache=True)
                    if not data or len(data) < 2:
                        continue
                    
                    # Find columns
                    headers = data[0]
                    room_id_col = None
                    check_in_col = None
                    check_out_col = None
                    status_col = None
                    
                    for idx, header in enumerate(headers):
                        header_lower = str(header).lower()
                        if 'room id' in header_lower or 'room_id' in header_lower:
                            room_id_col = idx
                        elif 'check-in' in header_lower or 'check_in' in header_lower:
                            check_in_col = idx
                        elif 'check-out' in header_lower or 'check_out' in header_lower:
                            check_out_col = idx
                        elif 'status' in header_lower:
                            status_col = idx
                    
                    if room_id_col is None or check_in_col is None or check_out_col is None:
                        continue
                    
                    # Check each booking
                    for row in data[1:]:
                        if len(row) <= max(room_id_col, check_in_col, check_out_col):
                            continue
                        
                        # Skip month headers and header rows
                        if not row[room_id_col] or str(row[room_id_col]).strip() == '':
                            continue
                        
                        # Check if this booking is for the same room
                        booking_room_id = str(row[room_id_col]).strip()
                        if booking_room_id != str(room_id).strip():
                            continue
                        
                        # Check if booking is confirmed/approved (for monthly sheets)
                        # For pending bookings sheet, check all bookings regardless of status
                        is_pending_sheet = 'pending' in sheet_name.lower()
                        if not is_pending_sheet and status_col is not None and status_col < len(row):
                            status = str(row[status_col]).strip().lower()
                            if status not in ['approved', 'confirmed', 'completed']:
                                continue
                        # For pending bookings sheet, we check all bookings to prevent double bookings
                        
                        # Parse booking dates
                        try:
                            booking_check_in_str = str(row[check_in_col]).strip()
                            booking_check_out_str = str(row[check_out_col]).strip()
                            
                            booking_check_in = datetime.strptime(booking_check_in_str, "%Y-%m-%d").date()
                            booking_check_out = datetime.strptime(booking_check_out_str, "%Y-%m-%d").date()
                            
                            # Check for date overlap
                            # Overlap occurs if: (check_in < booking_check_out) and (check_out > booking_check_in)
                            if check_in_date < booking_check_out and check_out_date > booking_check_in:
                                return False, f"Room is already booked from {booking_check_in_str} to {booking_check_out_str}."
                        
                        except (ValueError, IndexError):
                            # Skip if we can't parse dates
                            continue
                
                except Exception as e:
                    logger.warning(f"⚠️ Error checking sheet {sheet_name}: {e}")
                    continue
            
            # No conflicts found
            return True, "Room is available for these dates."
            
        except Exception as e:
            logger.error(f"❌ Error checking room availability: {e}")
            import traceback
            traceback.print_exc()
            return False, f"Error checking availability: {str(e)}"
    
    def get_room_info(self, room_id: str) -> Optional[Dict[str, Any]]:
        """Get room information by Room ID."""
        try:
            all_sheets = self.discover_sheets()
            room_sheets = [s for s in all_sheets if self.detect_sheet_type(s) == 'hotel']
            
            for sheet_name in room_sheets:
                try:
                    data = self.read_all_data(sheet_name, use_cache=True)
                    if not data or len(data) < 2:
                        continue
                    
                    headers = data[0]
                    
                    # Find Room ID column
                    room_id_col = None
                    for idx, header in enumerate(headers):
                        header_lower = str(header).lower()
                        if 'room id' in header_lower or 'room_id' in header_lower:
                            room_id_col = idx
                            break
                    
                    if room_id_col is None:
                        continue
                    
                    # Find matching room
                    for row in data[1:]:
                        if len(row) > room_id_col and str(row[room_id_col]).strip() == str(room_id).strip():
                            room_dict = dict(zip(headers, row))
                            return room_dict
                
                except Exception as e:
                    logger.warning(f"⚠️ Error processing sheet {sheet_name}: {e}")
                    continue
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Error getting room info: {e}")
            return None
    
    def update_booking_status(self, sheet_name: str, booking_id: str, status: str, notes: str = "") -> bool:
        """Update booking status. If approved, move to monthly booking sheet and decrement availability."""
        try:
            # Find pending bookings sheet
            pending_sheet = self._get_or_create_pending_bookings_sheet()
            worksheet = self.get_worksheet(pending_sheet)
            data = worksheet.get_all_values()
            
            if not data or len(data) < 2:
                return False
            
            # Standard headers for pending bookings sheet
            standard_headers = ['Booking ID', 'Customer Name', 'Phone', 'Check-in', 'Check-out', 
                              'Room Type', 'Room Name', 'Room ID', 'Num Rooms', 'Guests', 
                              'Price', 'Status', 'Created At', 'Notes']
            
            # Find columns using standard headers (they're in the same order as standard_headers)
            id_col = 0  # Booking ID is always first
            status_col = 11  # Status is always at index 11
            notes_col = 13  # Notes is always at index 13
            room_id_col = 7  # Room ID is always at index 7
            room_name_col = 6  # Room Name is always at index 6
            num_rooms_col = 8  # Num Rooms is always at index 8
            check_in_col = 3  # Check-in is always at index 3
            
            # Find booking by parsing month-section structure
            booking_row = None
            row_idx = None
            in_booking_section = False
            
            for idx, row in enumerate(data, start=1):
                if not any(row):
                    continue
                
                # Check if this is a month header
                if row[0] and ',' in str(row[0]) and any(month in str(row[0]) for month in ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']):
                    in_booking_section = True
                    continue
                
                # Check if this is a header row
                if in_booking_section and any(h in ' '.join([str(x).strip() for x in row[:5]]) for h in ['Booking ID', 'Customer Name']):
                    continue
                
                # Check if this is the booking we're looking for
                if len(row) > id_col and str(row[id_col]).strip() == booking_id:
                    booking_row = row
                    row_idx = idx
                    break
            
            if not booking_row or row_idx is None:
                logger.warning(f"❌ Booking {booking_id} not found in pending bookings")
                return False
            
            # If approving, move to monthly booking sheet
            if status.lower() in ['approved', 'confirmed']:
                # Get check-in date to determine which monthly sheet
                check_in_date = None
                if check_in_col < len(booking_row):
                    check_in_str = str(booking_row[check_in_col]).strip()
                    try:
                        # Try parsing YYYY-MM-DD format
                        check_in_date = datetime.strptime(check_in_str, "%Y-%m-%d")
                    except:
                        try:
                            # Try other formats
                            for fmt in ["%B %d, %Y", "%b %d, %Y", "%d/%m/%Y", "%m/%d/%Y"]:
                                check_in_date = datetime.strptime(check_in_str, fmt)
                                break
                        except:
                            check_in_date = datetime.now()  # Default to current month
                
                if check_in_date is None:
                    check_in_date = datetime.now()
                
                # Get or create monthly booking sheet
                monthly_sheet = self._get_or_create_monthly_booking_sheet(check_in_date)
                monthly_worksheet = self.get_worksheet(monthly_sheet)
                
                # Prepare row data for monthly sheet using standard headers order
                # Map booking_row columns (index-based) to monthly sheet columns
                monthly_row = []
                # Booking ID, Customer Name, Phone, Check-in, Check-out, Room Type, Room Name, Room ID, Num Rooms, Guests, Price, Status, Approved At, Notes
                if 0 < len(booking_row): monthly_row.append(booking_row[0])  # Booking ID
                else: monthly_row.append('')
                if 1 < len(booking_row): monthly_row.append(booking_row[1])  # Customer Name
                else: monthly_row.append('')
                if 2 < len(booking_row):
                    monthly_row.append(booking_row[2])  # Phone
                else:
                    monthly_row.append('')
                if 3 < len(booking_row):
                    monthly_row.append(booking_row[3])  # Check-in
                else:
                    monthly_row.append('')
                if 4 < len(booking_row):
                    monthly_row.append(booking_row[4])  # Check-out
                else:
                    monthly_row.append('')
                if 5 < len(booking_row):
                    monthly_row.append(booking_row[5])  # Room Type
                else:
                    monthly_row.append('')
                if 6 < len(booking_row):
                    monthly_row.append(booking_row[6])  # Room Name
                else:
                    monthly_row.append('')
                if 7 < len(booking_row):
                    monthly_row.append(booking_row[7])  # Room ID
                else:
                    monthly_row.append('')
                if 8 < len(booking_row):
                    monthly_row.append(booking_row[8])  # Num Rooms
                else:
                    monthly_row.append('')
                if 9 < len(booking_row):
                    monthly_row.append(booking_row[9])  # Guests
                else:
                    monthly_row.append('')
                if 10 < len(booking_row):
                    monthly_row.append(booking_row[10])  # Price
                else:
                    monthly_row.append('')
                monthly_row.append('confirmed')  # Status
                monthly_row.append(datetime.now().isoformat())  # Approved At
                monthly_row.append(notes if notes else '')  # Notes
                
                # Insert booking in sorted order by check-in date (newest first)
                # Ensure headers exist first
                monthly_data = monthly_worksheet.get_all_values()
                if not monthly_data or len(monthly_data) < 1:
                    # Add headers if sheet is empty
                    headers = ['Booking ID', 'Customer Name', 'Phone', 'Check-in', 'Check-out', 
                              'Room Type', 'Room Name', 'Room ID', 'Num Rooms', 'Guests', 
                              'Price', 'Status', 'Approved At', 'Notes']
                    monthly_worksheet.append_row(headers)
                    # Format headers (bold)
                    monthly_worksheet.format('1:1', {'textFormat': {'bold': True}})
                    monthly_data = monthly_worksheet.get_all_values()
                
                # Parse check-in date for sorting
                check_in_str = monthly_row[3] if len(monthly_row) > 3 else ''  # Check-in is at index 3
                try:
                    check_in_date = datetime.strptime(check_in_str, "%Y-%m-%d")
                except:
                    check_in_date = datetime.now()
                
                # Find insertion point (sorted by check-in date, newest first)
                insert_row = 2  # Start after headers
                if len(monthly_data) > 1:
                    for idx in range(1, len(monthly_data)):
                        row = monthly_data[idx]
                        if len(row) > 3:
                            try:
                                row_check_in_str = str(row[3]).strip()
                                if row_check_in_str:
                                    row_check_in = datetime.strptime(row_check_in_str, "%Y-%m-%d")
                                    # If current booking date is newer or equal, insert before this row
                                    if check_in_date >= row_check_in:
                                        insert_row = idx + 1
                                        break
                                    insert_row = idx + 2
                            except:
                                # Can't parse date, continue to next row
                                continue
                
                # Insert the booking
                monthly_worksheet.insert_row(monthly_row, insert_row)
                
                # Delete from pending sheet
                worksheet.delete_rows(row_idx)
                
                # Decrement availability (room_id_col = 7, num_rooms_col = 8)
                room_id = None
                if room_id_col < len(booking_row):
                    room_id = str(booking_row[room_id_col]).strip()
                    check_in = str(booking_row[check_in_col]).strip() if check_in_col < len(booking_row) else ''
                    check_out_col = 4  # Check-out is always at index 4
                    check_out = str(booking_row[check_out_col]).strip() if check_out_col < len(booking_row) else ''
                    
                    # Update booked dates in room sheet when booking is approved
                    if room_id and check_in and check_out:
                        try:
                            self.update_room_booked_dates(room_id, check_in, check_out, add=True)
                            logger.info(f"✅ Updated booked dates for room {room_id}: {check_in} to {check_out}")
                        except Exception as e:
                            logger.warning(f"⚠️ Failed to update booked dates for room {room_id}: {e}")
                            import traceback
                            traceback.print_exc()
                
                num_rooms = 1
                if num_rooms_col < len(booking_row):
                    try:
                        num_rooms = int(float(str(booking_row[num_rooms_col]).strip()))
                    except:
                        pass
                
                if room_id:
                    self._decrement_room_availability_by_id(room_id, num_rooms)
                
            else:
                # Just update status in pending sheet (status_col = 11, notes_col = 13)
                worksheet.update_cell(row_idx, status_col + 1, status)
                
                if notes:
                    worksheet.update_cell(row_idx, notes_col + 1, notes)
            
            self._invalidate_sheet_cache(pending_sheet)
            logger.info(f"✅ Updated booking {booking_id} status to {status}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error updating booking status: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _decrement_room_availability_by_id(self, room_id: str, num_rooms: int = 1):
        """Decrement availability for a specific room by Room ID."""
        try:
            # Find all sheets that might contain room data
            all_sheets = self.discover_sheets()
            room_sheets = [s for s in all_sheets if self.detect_sheet_type(s) == 'hotel']
            
            for sheet_name in room_sheets:
                try:
                    worksheet = self.get_worksheet(sheet_name)
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
                    
                    for row_idx, row in enumerate(data[1:], start=2):
                        if room_id_col < len(row) and str(row[room_id_col]).strip() == str(room_id).strip():
                            current_avail = str(row[available_col]).strip()
                            

                            if current_avail.lower() in ['yes', 'available', 'true']:
                                if num_rooms >= 1:
                                    worksheet.update_cell(row_idx, available_col + 1, 'No')
                                    logger.info(f"📊 Updated {room_id} availability: Yes → No")
                            else:
                                try:
                                    current_avail_int = int(float(current_avail)) if current_avail else 0
                                    new_avail = max(0, current_avail_int - num_rooms)
                                    worksheet.update_cell(row_idx, available_col + 1, str(new_avail))
                                    logger.info(f"📊 Updated {room_id} availability: {current_avail_int} → {new_avail}")
                                except:
                                    pass
                            
                            self._invalidate_sheet_cache(sheet_name)
                            return True
                            
                except Exception as e:
                    logger.warning(f"⚠️ Error processing sheet {sheet_name}: {e}")
                    continue
            
            logger.warning(f"❌ Room ID '{room_id}' not found in any room sheet")
            return False
            
        except Exception as e:
            logger.error(f"❌ Error decrementing room availability: {e}")
            return False
    
    def _decrement_room_availability(self, room_id: str, num_rooms: int = 1):
        """Alias for _decrement_room_availability_by_id (kept for backward compatibility)."""
        return self._decrement_room_availability_by_id(room_id, num_rooms)
    
    def _increment_room_availability_by_id(self, room_id: str, num_rooms: int = 1):
        """Increment availability for a specific room by Room ID (for auto-checkout)."""
        try:
            all_sheets = self.discover_sheets()
            room_sheets = [s for s in all_sheets if self.detect_sheet_type(s) == 'hotel']
            
            for sheet_name in room_sheets:
                try:
                    worksheet = self.get_worksheet(sheet_name)
                    data = worksheet.get_all_values()
                    
                    if not data or len(data) < 2:
                        continue
                    
                    headers = data[0]
                    
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
                    
                    for row_idx, row in enumerate(data[1:], start=2):
                        if room_id_col < len(row) and str(row[room_id_col]).strip() == str(room_id).strip():
                            current_avail = str(row[available_col]).strip()
                            
        
                            if current_avail.lower() in ['no', 'unavailable', 'false']:
                                # Change to "Yes" when incrementing
                                worksheet.update_cell(row_idx, available_col + 1, 'Yes')
                                logger.info(f"📊 Updated {room_id} availability: No → Yes")
                            else:
                                try:
                                    current_avail_int = int(float(current_avail)) if current_avail else 0
                                    new_avail = current_avail_int + num_rooms
                                    worksheet.update_cell(row_idx, available_col + 1, str(new_avail))
                                    logger.info(f"📊 Updated {room_id} availability: {current_avail_int} → {new_avail}")
                                except:
                                    pass
                            
                            self._invalidate_sheet_cache(sheet_name)
                            return True
                            
                except Exception as e:
                    logger.warning(f"⚠️ Error processing sheet {sheet_name}: {e}")
                    continue
            
            logger.warning(f"❌ Room ID '{room_id}' not found in any room sheet")
            return False
            
        except Exception as e:
            logger.error(f"❌ Error incrementing room availability: {e}")
            return False
    
    def _decrement_hotel_availability(self, room_type: str, num_rooms: int = 1):
        """Decrement availability for a room type in hotels sheet (legacy method)."""
        # This method is kept for backward compatibility but is not used in the new system
        pass
    
    def _get_or_create_pending_bookings_sheet(self) -> str:
        """Get or create the 'Pending Bookings' sheet."""
        sheet_name = "Pending Bookings"
        
        try:
            self.get_worksheet(sheet_name)
            return sheet_name
        except:
            # Create the sheet
            try:
                worksheet = self._sheet.add_worksheet(title=sheet_name, rows=1000, cols=20)
                logger.info(f"✅ Created pending bookings sheet: {sheet_name}")
                return sheet_name
            except Exception as e:
                logger.error(f"❌ Error creating pending bookings sheet: {e}")
                return config.BOOKINGS_SHEET  # Fallback
    
    def _get_or_create_monthly_booking_sheet(self, date: datetime = None) -> str:
        """Get or create monthly booking sheet (e.g., 'Bookings January 2026')."""
        if date is None:
            date = datetime.now()
        
        month_name = date.strftime("%B")  # January, February, etc.
        year = date.year
        sheet_name = f"Bookings {month_name} {year}"
        
        try:
            self.get_worksheet(sheet_name)
            return sheet_name
        except:
            # Create the sheet
            try:
                worksheet = self._sheet.add_worksheet(title=sheet_name, rows=1000, cols=20)
                # Add headers
                headers = ['Booking ID', 'Customer Name', 'Phone', 'Check-in', 'Check-out', 
                          'Room Type', 'Room Name', 'Room ID', 'Num Rooms', 'Guests', 
                          'Price', 'Status', 'Approved At', 'Notes']
                worksheet.append_row(headers)
                # Format headers (bold)
                worksheet.format('1:1', {'textFormat': {'bold': True}})
                logger.info(f"✅ Created monthly booking sheet: {sheet_name}")
                return sheet_name
            except Exception as e:
                logger.error(f"❌ Error creating monthly booking sheet: {e}")
                return sheet_name
    
    def _parse_booked_dates(self, booked_dates_str: str) -> List[Tuple[date, date]]:
        """
        Parse booked dates string into list of (check_in, check_out) tuples.
        Format: "2026-01-25 to 2026-01-27, 2026-02-03 to 2026-02-05" or similar.
        Returns empty list if no valid dates found.
        """
        if not booked_dates_str or not str(booked_dates_str).strip():
            return []
        
        date_ranges = []
        booked_dates_str = str(booked_dates_str).strip()
        
        # Split by comma to get individual date ranges
        ranges = booked_dates_str.split(',')
        
        for date_range in ranges:
            date_range = date_range.strip()
            if not date_range:
                continue
            
            # Try different formats: "2026-01-25 to 2026-01-27", "2026-01-25-2026-01-27", etc.
            if ' to ' in date_range:
                parts = date_range.split(' to ')
            elif '-' in date_range and date_range.count('-') >= 4:
                # Format: "2026-01-25-2026-01-27"
                parts = date_range.split('-', 2)
                if len(parts) == 3:
                    check_in_str = f"{parts[0]}-{parts[1]}"
                    check_out_str = parts[2]
                    parts = [check_in_str, check_out_str]
            else:
                continue
            
            if len(parts) != 2:
                continue
            
            try:
                check_in_date = datetime.strptime(parts[0].strip(), "%Y-%m-%d").date()
                check_out_date = datetime.strptime(parts[1].strip(), "%Y-%m-%d").date()
                date_ranges.append((check_in_date, check_out_date))
            except (ValueError, IndexError):
                continue
        
        return date_ranges
    
    def _dates_overlap(self, check_in: date, check_out: date, booked_check_in: date, booked_check_out: date) -> bool:
        """
        Check if two date ranges overlap.
        Overlap occurs if: (check_in < booked_check_out) and (check_out > booked_check_in)
        """
        return check_in < booked_check_out and check_out > booked_check_in
    
    def check_room_availability_from_booked_dates_column(self, room_id: str, check_in: str, check_out: str) -> Tuple[bool, str]:
        """
        Check if a room is available by checking the 'Booked Dates' column in the room sheet.
        Returns (is_available, message).
        """
        try:
            # Parse dates
            try:
                check_in_date = datetime.strptime(check_in, "%Y-%m-%d").date()
                check_out_date = datetime.strptime(check_out, "%Y-%m-%d").date()
            except ValueError:
                return False, "Invalid date format. Please use YYYY-MM-DD format."
            
            # Validate dates
            if check_in_date >= check_out_date:
                return False, "Check-in date must be before check-out date."
            
            if check_in_date < datetime.now().date():
                return False, "Check-in date cannot be in the past."
            
            # Find the room in all hotel sheets
            all_sheets = self.discover_sheets()
            room_sheets = [s for s in all_sheets if self.detect_sheet_type(s) == 'hotel']
            
            for sheet_name in room_sheets:
                try:
                    data = self.read_all_data(sheet_name, use_cache=True)
                    if not data or len(data) < 2:
                        continue
                    
                    headers = data[0]
                    
                    # Find columns
                    room_id_col = None
                    booked_dates_col = None
                    
                    for idx, header in enumerate(headers):
                        header_lower = str(header).lower()
                        if 'room id' in header_lower or 'room_id' in header_lower:
                            room_id_col = idx
                        elif 'booked dates' in header_lower or 'booked_dates' in header_lower:
                            booked_dates_col = idx
                    
                    if room_id_col is None:
                        continue
                    
                    # Find the room
                    for row in data[1:]:
                        if len(row) <= room_id_col:
                            continue
                        
                        booking_room_id = str(row[room_id_col]).strip()
                        if booking_room_id != str(room_id).strip():
                            continue
                        
                        # Found the room - check booked dates
                        if booked_dates_col is not None and booked_dates_col < len(row):
                            booked_dates_str = str(row[booked_dates_col]).strip()
                            booked_ranges = self._parse_booked_dates(booked_dates_str)
                            
                            # Clean up expired dates before checking
                            booked_ranges = self._cleanup_expired_booked_dates(booked_ranges)
                            
                            # Check for overlaps
                            for booked_check_in, booked_check_out in booked_ranges:
                                if self._dates_overlap(check_in_date, check_out_date, booked_check_in, booked_check_out):
                                    return False, f"Room is already booked from {booked_check_in.strftime('%Y-%m-%d')} to {booked_check_out.strftime('%Y-%m-%d')}."
                        
                        # Room found and no conflicts
                        return True, "Room is available for these dates."
                
                except Exception as e:
                    logger.warning(f"⚠️ Error checking sheet {sheet_name}: {e}")
                    continue
            
            # Room not found
            return False, "Room not found."
            
        except Exception as e:
            logger.error(f"❌ Error checking room availability: {e}")
            import traceback
            traceback.print_exc()
            return False, f"Error checking availability: {str(e)}"
    
    def _cleanup_expired_booked_dates(self, booked_ranges: List[Tuple[date, date]]) -> List[Tuple[date, date]]:
        """
        Remove expired booked date ranges (where checkout date has passed).
        Returns filtered list with only future dates.
        """
        today = datetime.now().date()
        return [
            (check_in, check_out) for check_in, check_out in booked_ranges
            if check_out >= today  # Keep only dates where checkout is today or in the future
        ]
    
    def cleanup_all_expired_booked_dates(self) -> int:
        """
        Clean up expired booked dates across all rooms in all hotel sheets.
        Removes date ranges where checkout date has passed.
        Returns the number of rooms updated.
        """
        updated_count = 0
        try:
            all_sheets = self.discover_sheets()
            room_sheets = [s for s in all_sheets if self.detect_sheet_type(s) == 'hotel']
            
            for sheet_name in room_sheets:
                try:
                    worksheet = self.get_worksheet(sheet_name)
                    data = worksheet.get_all_values()
                    
                    if not data or len(data) < 2:
                        continue
                    
                    headers = data[0]
                    
                    # Find columns
                    booked_dates_col = None
                    
                    for idx, header in enumerate(headers):
                        header_lower = str(header).lower()
                        if 'booked dates' in header_lower or 'booked_dates' in header_lower:
                            booked_dates_col = idx
                            break
                    
                    if booked_dates_col is None:
                        continue  # No booked dates column in this sheet
                    
                    # Process each room row
                    for row_idx, row in enumerate(data[1:], start=2):
                        if booked_dates_col >= len(row):
                            continue
                        
                        booked_dates_str = str(row[booked_dates_col]).strip()
                        if not booked_dates_str:
                            continue
                        
                        # Parse and clean up expired dates
                        booked_ranges = self._parse_booked_dates(booked_dates_str)
                        original_count = len(booked_ranges)
                        booked_ranges = self._cleanup_expired_booked_dates(booked_ranges)
                        
                        # Only update if dates were removed
                        if len(booked_ranges) < original_count:
                            # Format as string
                            if booked_ranges:
                                new_booked_dates = ", ".join([
                                    f"{r[0].strftime('%Y-%m-%d')} to {r[1].strftime('%Y-%m-%d')}"
                                    for r in booked_ranges
                                ])
                            else:
                                new_booked_dates = ""  # Empty if no future dates
                            
                            # Update the cell
                            worksheet.update_cell(row_idx, booked_dates_col + 1, new_booked_dates)
                            updated_count += 1
                    
                    # Invalidate cache for this sheet
                    self._invalidate_sheet_cache(sheet_name)
                
                except Exception as e:
                    logger.warning(f"⚠️ Error cleaning up sheet {sheet_name}: {e}")
                    continue
            
            if updated_count > 0:
                logger.info(f"✅ Cleaned up expired booked dates in {updated_count} rooms")
            
            return updated_count
            
        except Exception as e:
            logger.error(f"❌ Error cleaning up expired booked dates: {e}")
            import traceback
            traceback.print_exc()
            return updated_count
    
    def update_room_booked_dates(self, room_id: str, check_in: str, check_out: str, add: bool = True) -> bool:
        """
        Update the 'Booked Dates' column for a room.
        If add=True, adds the date range. If add=False, removes it.
        Automatically cleans up expired dates (where checkout has passed).
        Returns True if successful.
        """
        try:
            # Parse dates
            try:
                check_in_date = datetime.strptime(check_in, "%Y-%m-%d").date()
                check_out_date = datetime.strptime(check_out, "%Y-%m-%d").date()
            except ValueError:
                logger.error(f"❌ Invalid date format: {check_in}, {check_out}")
                return False
            
            # Find the room in all hotel sheets
            all_sheets = self.discover_sheets()
            room_sheets = [s for s in all_sheets if self.detect_sheet_type(s) == 'hotel']
            
            for sheet_name in room_sheets:
                try:
                    worksheet = self.get_worksheet(sheet_name)
                    data = worksheet.get_all_values()
                    
                    if not data or len(data) < 2:
                        continue
                    
                    headers = data[0]
                    
                    # Find columns
                    room_id_col = None
                    booked_dates_col = None
                    
                    for idx, header in enumerate(headers):
                        header_lower = str(header).lower()
                        if 'room id' in header_lower or 'room_id' in header_lower:
                            room_id_col = idx
                        elif 'booked dates' in header_lower or 'booked_dates' in header_lower:
                            booked_dates_col = idx
                    
                    if room_id_col is None:
                        continue
                    
                    # Find the room
                    for row_idx, row in enumerate(data[1:], start=2):
                        if len(row) <= room_id_col:
                            continue
                        
                        booking_room_id = str(row[room_id_col]).strip()
                        if booking_room_id != str(room_id).strip():
                            continue
                        
                        # Found the room - update booked dates
                        if booked_dates_col is None:
                            # Need to add the column
                            # Find the last column index
                            last_col_idx = len(headers)
                            worksheet.update_cell(1, last_col_idx + 1, 'Booked Dates')
                            booked_dates_col = last_col_idx
                        
                        # Get current booked dates
                        current_booked_dates = ""
                        if booked_dates_col < len(row):
                            current_booked_dates = str(row[booked_dates_col]).strip()
                        
                        # Parse existing booked dates
                        booked_ranges = self._parse_booked_dates(current_booked_dates)
                        
                        # Always clean up expired dates first
                        booked_ranges = self._cleanup_expired_booked_dates(booked_ranges)
                        
                        if add:
                            # Add the new date range
                            new_range = (check_in_date, check_out_date)
                            booked_ranges.append(new_range)
                            
                            # Sort by check-in date
                            booked_ranges.sort(key=lambda x: x[0])
                        else:
                            # Remove the date range
                            booked_ranges = [
                                r for r in booked_ranges
                                if not (r[0] == check_in_date and r[1] == check_out_date)
                            ]
                        
                        # Format as string: "2026-01-25 to 2026-01-27, 2026-02-03 to 2026-02-05"
                        if booked_ranges:
                            new_booked_dates = ", ".join([
                                f"{r[0].strftime('%Y-%m-%d')} to {r[1].strftime('%Y-%m-%d')}"
                                for r in booked_ranges
                            ])
                        else:
                            new_booked_dates = ""  # Empty if no future dates
                        
                        # Update the cell
                        worksheet.update_cell(row_idx, booked_dates_col + 1, new_booked_dates)
                        self._invalidate_sheet_cache(sheet_name)
                        logger.info(f"✅ Updated booked dates for room {room_id}: {new_booked_dates}")
                        return True
                
                except Exception as e:
                    logger.warning(f"⚠️ Error updating sheet {sheet_name}: {e}")
                    continue
            
            # Room not found
            logger.warning(f"⚠️ Room {room_id} not found for booked dates update")
            return False
            
        except Exception as e:
            logger.error(f"❌ Error updating room booked dates: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def get_available_rooms_by_type(self, room_type: str, check_in: str, check_out: str) -> List[Dict[str, Any]]:
        """
        Get all available rooms of a specific type for the given date range.
        Returns list of room info dictionaries.
        """
        try:
            # Parse dates
            try:
                check_in_date = datetime.strptime(check_in, "%Y-%m-%d").date()
                check_out_date = datetime.strptime(check_out, "%Y-%m-%d").date()
            except ValueError:
                return []
            
            available_rooms = []
            
            # Find all hotel sheets
            all_sheets = self.discover_sheets()
            room_sheets = [s for s in all_sheets if self.detect_sheet_type(s) == 'hotel']
            
            for sheet_name in room_sheets:
                try:
                    data = self.read_all_data(sheet_name, use_cache=True)
                    if not data or len(data) < 2:
                        continue
                    
                    headers = data[0]
                    
                    # Find columns
                    room_id_col = None
                    room_name_col = None
                    price_col = None
                    booked_dates_col = None
                    
                    for idx, header in enumerate(headers):
                        header_lower = str(header).lower()
                        if 'room id' in header_lower or 'room_id' in header_lower:
                            room_id_col = idx
                        elif 'room name' in header_lower or 'room_name' in header_lower:
                            room_name_col = idx
                        elif 'price' in header_lower:
                            price_col = idx
                        elif 'booked dates' in header_lower or 'booked_dates' in header_lower:
                            booked_dates_col = idx
                    
                    if room_id_col is None:
                        continue
                    
                    # Check each room
                    for row in data[1:]:
                        if len(row) <= room_id_col:
                            continue
                        
                        room_id = str(row[room_id_col]).strip()
                        if not room_id:
                            continue
                        
                        # Get room name/type
                        room_name = ""
                        if room_name_col is not None and room_name_col < len(row):
                            room_name = str(row[room_name_col]).strip()
                        
                        # Check if room type matches (case-insensitive)
                        if room_type.lower() not in room_name.lower() and room_name.lower() not in room_type.lower():
                            # Also check if room_name contains common variations
                            room_type_lower = room_type.lower()
                            if room_type_lower not in ['twin', 'double', 'villa', 'two bed room villa']:
                                continue
                            if 'twin' in room_type_lower and 'twin' not in room_name.lower():
                                continue
                            if 'double' in room_type_lower and 'double' not in room_name.lower():
                                continue
                            if 'villa' in room_type_lower and 'villa' not in room_name.lower():
                                continue
                        
                        # Check availability
                        is_available = True
                        if booked_dates_col is not None and booked_dates_col < len(row):
                            booked_dates_str = str(row[booked_dates_col]).strip()
                            booked_ranges = self._parse_booked_dates(booked_dates_str)
                            
                            # Clean up expired dates before checking
                            booked_ranges = self._cleanup_expired_booked_dates(booked_ranges)
                            
                            # Check for overlaps
                            for booked_check_in, booked_check_out in booked_ranges:
                                if self._dates_overlap(check_in_date, check_out_date, booked_check_in, booked_check_out):
                                    is_available = False
                                    break
                        
                        if is_available:
                            # Get price
                            price = ""
                            if price_col is not None and price_col < len(row):
                                price = str(row[price_col]).strip()
                            
                            available_rooms.append({
                                'room_id': room_id,
                                'room_name': room_name or room_type,
                                'price': price,
                                'sheet_name': sheet_name
                            })
                
                except Exception as e:
                    logger.warning(f"⚠️ Error checking sheet {sheet_name}: {e}")
                    continue
            
            return available_rooms
            
        except Exception as e:
            logger.error(f"❌ Error getting available rooms: {e}")
            import traceback
            traceback.print_exc()
            return []

# Global instance (lazy initialization)
_sheets_manager_instance = None

def get_sheets_manager() -> GoogleSheetsManager:
    """Get or create the global GoogleSheetsManager instance."""
    global _sheets_manager_instance
    if _sheets_manager_instance is None:
        _sheets_manager_instance = GoogleSheetsManager()
    return _sheets_manager_instance

# Create global instance for backward compatibility
sheets_manager = get_sheets_manager()
