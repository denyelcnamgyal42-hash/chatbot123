"""Universal LangChain agent for any Google Sheets data."""
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import fallback agent classes (always available)
try:
    from langchain.agents import ZeroShotAgent, AgentExecutor
    from langchain.memory import ConversationBufferMemory
    from langchain.chains import LLMChain
    from langchain.prompts import PromptTemplate
    HAS_FALLBACK_AGENT = True
except ImportError:
    HAS_FALLBACK_AGENT = False

from langchain_core.tools import Tool
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
try:
    from langchain_community.callbacks.manager import get_openai_callback
except ImportError:
    from langchain.callbacks import get_openai_callback
from typing import Dict, List, Optional, Tuple, Any
import config
from google_sheets import sheets_manager  # FIXED
# Lazy import - don't import dense_retrieval at module level to avoid blocking
# from dense_retrieval import get_dense_retrieval  # Moved to lazy import
from session_manager import session_manager
from datetime import datetime, timedelta
import json
import re
import time
import traceback

# Lazy initialization - only load when first used
_dense_retriever_instance = None

def get_dense_retriever():
    """Get dense retriever instance (lazy initialization)."""
    global _dense_retriever_instance
    if _dense_retriever_instance is None:
        print("🔄 Initializing dense retriever (first use)...")
        # Lazy import to avoid blocking module import
        from dense_retrieval import get_dense_retrieval
        _dense_retriever_instance = get_dense_retrieval()
        print("✅ Dense retriever initialized")
    return _dense_retriever_instance

class UniversalAgent:
    """Universal agent for any Google Sheets data."""
    
    def __init__(self):
        """Initialize the universal agent."""
        print("🌍 Initializing Universal Agent...")
        
        # OpenAI setup
        api_key = config.OPENAI_API_KEY.strip()
        if not api_key:
            raise ValueError("❌ OPENAI_API_KEY is not set")
        
        self.llm = ChatOpenAI(
            model=config.MODEL_NAME,
            temperature=0.3,
            api_key=api_key,
            max_tokens=2000  # Increased to prevent message truncation
        )
        
        # Create universal tools
        self.tools = self._create_universal_tools()
        
        # Create agent
        self.agent = self._create_universal_agent()
        
        print(f"✅ Universal Agent initialized with {len(self.tools)} tools")
    
    def _create_universal_tools(self) -> List[Tool]:
        """Create universal tools for any data."""
        
        # Store reference to self for accessing customer context
        agent_self = self
        
        def search_rooms(query: str) -> str:
            """Search for available hotel rooms."""
            try:
                print(f"🔍 Searching for rooms: {query}")
                
                # Extract dates from query if available (check extracted_dates from context)
                check_in_date = None
                check_out_date = None
                check_in_raw = None
                check_out_raw = None
                
                # Try to extract dates from the query or from agent_self context
                # The dates should be passed via the instruction context
                # For now, we'll check booked dates if dates are provided in the query
                import re
                from datetime import datetime
                
                # Look for date patterns in query
                date_pattern = re.search(r'(\d{1,2})(?:st|nd|rd|th)?\s+(january|february|march|april|may|june|july|august|september|october|november|december)', query.lower())
                if date_pattern:
                    day = int(date_pattern.group(1))
                    month_name = date_pattern.group(2).capitalize()
                    months = {
                        'January': 1, 'February': 2, 'March': 3, 'April': 4,
                        'May': 5, 'June': 6, 'July': 7, 'August': 8,
                        'September': 9, 'October': 10, 'November': 11, 'December': 12
                    }
                    current_year = datetime.now().year
                    check_in_date = datetime(current_year, months[month_name], day)
                    if check_in_date < datetime.now():
                        check_in_date = datetime(current_year + 1, months[month_name], day)
                    check_in_raw = check_in_date.strftime("%Y-%m-%d")
                    # DO NOT default to 1 night - dates will be used for availability checking only
                    # Checkout will be set when user specifies nights
                    check_out_raw = None
                
                # Use hotel search - only searches hotel/room sheets
                results = get_dense_retriever().search_hotels(query, k=20)  # Get more results to filter
                
                if not results:
                    return "❌ No rooms found matching your search. Please try different dates or room types."
                
                # Format response - friendly and readable
                response_parts = ["🛏️ Available Rooms:", ""]
                available_count = 0
                
                for item in results[:20]:  # Check up to 20 rooms
                    row_data = item.get('row_data', {})
                    sheet_name = item.get('sheet_name', '')
                    
                    # Dynamically extract key info using sheet structure
                    structure = sheets_manager.get_sheet_structure(sheet_name)
                    name_col = structure.get('name_column')
                    price_col = structure.get('price_column')
                    room_id_col = None
                    
                    # Find room ID column
                    for idx, header in enumerate(structure['headers']):
                        header_lower = str(header).lower()
                        if 'room id' in header_lower or 'room_id' in header_lower:
                            room_id_col = idx
                            break
                    
                    # Get name
                    if name_col is not None and name_col < len(structure['headers']):
                        name_key = structure['headers'][name_col]
                        name = row_data.get(name_key, 'Unnamed Room')
                    else:
                        name = (row_data.get('name') or row_data.get('room_type') or 
                               row_data.get('Room Name') or row_data.get('Room Type') or 'Unnamed Room')
                    
                    # Get room ID
                    room_id = None
                    if room_id_col is not None and room_id_col < len(structure['headers']):
                        room_id_key = structure['headers'][room_id_col]
                        room_id = row_data.get(room_id_key, '')
                    
                    # Get price
                    if price_col is not None and price_col < len(structure['headers']):
                        price_key = structure['headers'][price_col]
                        price = row_data.get(price_key, 'Price not listed')
                    else:
                        price = (row_data.get('price') or row_data.get('Price') or 
                               row_data.get('cost') or row_data.get('rate') or 'Price not listed')
                    
                    # Clean up price (remove currency symbols for display, add back)
                    if price and price != 'Price not listed':
                        try:
                            price_clean = str(price).replace(',', '').replace('Nu.', '').replace('$', '').strip()
                            price_float = float(price_clean)
                            price = f"Nu.{int(price_float)}"
                        except:
                            pass
                    
                    # Check availability if dates are provided
                    is_available = True
                    if check_in_raw and check_out_raw and room_id:
                        is_available, availability_msg = sheets_manager.check_room_availability_from_booked_dates_column(
                            room_id, check_in_raw, check_out_raw
                        )
                        if not is_available:
                            continue  # Skip unavailable rooms
                    
                    # Only show available rooms
                    available_count += 1
                    response_parts.append(f"🛏️ {name} - {price}/night")
                    
                    if available_count >= 10:  # Limit to 10 rooms in response
                        break
                
                if available_count == 0:
                    return "❌ No rooms available for the requested dates. Please try different dates."
                
                response_parts.append("")
                response_parts.append("Which room would you like to book? 😊")
                
                return "\n".join(response_parts)
                
            except Exception as e:
                print(f"❌ Room search error: {e}")
                traceback.print_exc()
                return "Sorry, I encountered an error searching for rooms. Please try again."
        
        def check_booking_status(identifier: str) -> str:
            """Check status of a booking."""
            try:
                # Find bookings sheet dynamically
                all_sheets = sheets_manager.discover_sheets()
                bookings_sheet = None
                for sheet in all_sheets:
                    if 'booking' in sheet.lower():
                        bookings_sheet = sheet
                        break
                
                if not bookings_sheet:
                    bookings_sheet = config.BOOKINGS_SHEET
                
                # Read bookings data
                bookings_data = sheets_manager.read_all_data(bookings_sheet)
                if not bookings_data or len(bookings_data) < 2:
                    return "No bookings found."
                
                headers = bookings_data[0]
                identifier_lower = identifier.lower()
                
                # Find matching booking by ID, phone, or name
                matching_bookings = []
                for row in bookings_data[1:]:
                    if len(row) < len(headers):
                        continue
                    row_dict = dict(zip(headers, row[:len(headers)]))
                    
                    booking_id = str(row_dict.get('booking_id', row_dict.get('id', ''))).lower()
                    phone = str(row_dict.get('phone', '')).lower()
                    name = str(row_dict.get('customer_name', row_dict.get('name', ''))).lower()
                    
                    if (identifier_lower in booking_id or 
                        identifier_lower in phone or 
                        identifier_lower in name):
                        matching_bookings.append(row_dict)
                
                if not matching_bookings:
                    return f"❌ No booking found for '{identifier}'."
                
                # Format response
                response_parts = []
                for booking in matching_bookings[:5]:  # Max 5 results
                    booking_id = booking.get('booking_id', booking.get('id', 'N/A'))
                    room_type = booking.get('room_type', 'N/A')
                    check_in = booking.get('check_in', booking.get('check-in', 'N/A'))
                    check_out = booking.get('check_out', booking.get('check-out', 'N/A'))
                    status = booking.get('status', 'pending')
                    price = booking.get('price', booking.get('total', 'N/A'))
                    
                    emoji = self._get_status_emoji(status)
                    response_parts.append(f"{emoji} Booking {booking_id}:")
                    response_parts.append(f"  Room: {room_type}")
                    response_parts.append(f"  Check-in: {check_in}")
                    response_parts.append(f"  Check-out: {check_out}")
                    response_parts.append(f"  Price: Nu.{price}")
                    response_parts.append(f"  Status: {status.title()}")
                    response_parts.append("")
                
                return "\n".join(response_parts)
                
            except Exception as e:
                print(f"❌ Booking status check error: {e}")
                traceback.print_exc()
                return f"Error checking booking status: {str(e)}"
        
        # REMOVED: All product/order functions - System is now hotel reservations only
        # Removed: create_universal_order, check_order_status, get_product_details, 
        # check_availability, get_recommendations, cancel_order
        
        def get_help(empty: str = "") -> str:
            """Get help about available commands."""
            return """
🤖 **I can help you with hotel reservations!**

🏨 **Hotel Bookings:**
- Check room availability for your dates
- Example: "rooms available for 21st January", "show me available rooms for next week"

📅 **Booking Process:**
- Tell me your travel dates and room preference
- I'll show you available rooms and create a booking
- Example: "I want to book a single room from 21st to 22nd January"

📋 **Checking Booking Status:**
- Check your booking status
- Example: "check my booking", "what's my booking status"

💡 **Tips:**
- Just chat naturally - I understand your intent!
- I'll ask for any missing information (check-in date, check-out date, room type)
- All bookings go to our team for confirmation and payment
            """
        
        def create_booking(booking_details: str) -> str:
            """Create a hotel booking - format: 'room_type, check_in, check_out, customer_name, phone, [num_rooms], [guests]'.
            Dates can be relative: 'tomorrow', '2 nights', or actual dates YYYY-MM-DD."""
            try:
                from datetime import datetime, timedelta
                
                parts = [p.strip() for p in booking_details.split(",")]
                
                if len(parts) < 5:
                    return "Please provide: 'room_type, check_in (YYYY-MM-DD or 'tomorrow'), check_out (YYYY-MM-DD or '2 nights'), your_name, your_phone' (num_rooms and guests optional)"
                
                room_type = parts[0]
                check_in_str = parts[1]
                check_out_str = parts[2]
                
                # Get customer info from context if not provided
                if len(parts) >= 5:
                    customer_name = parts[3]
                    phone = parts[4]
                else:
                    # Get from agent's stored context
                    if hasattr(agent_self, '_current_customer_name') and agent_self._current_customer_name:
                        customer_name = agent_self._current_customer_name
                    else:
                        customer_name = "Customer"
                    if hasattr(agent_self, '_current_customer_phone') and agent_self._current_customer_phone:
                        phone = agent_self._current_customer_phone
                    else:
                        return "❌ Customer phone number is required. Please provide your phone number."
                
                num_rooms = parts[5] if len(parts) > 5 else "1"
                # Limit to maximum 3 rooms per booking
                try:
                    num_rooms_int = int(num_rooms)
                    if num_rooms_int > 3:
                        return f"❌ Sorry, you can book a maximum of 3 rooms per booking. You requested {num_rooms_int} rooms. Please book in separate bookings or reduce the number of rooms to 3 or less."
                    num_rooms = str(min(num_rooms_int, 3))
                except:
                    num_rooms = "1"  # Default to 1 if parsing fails
                
                guests = parts[6] if len(parts) > 6 else "2"
                
                # Extract dates from conversation history if relative dates provided
                # Get conversation history to extract dates
                session = session_manager.get_session(agent_self._current_customer_phone) if hasattr(agent_self, '_current_customer_phone') else None
                conversation_text = ""
                if session:
                    recent_messages = session.history[-10:] if len(session.history) > 10 else session.history
                    conversation_text = " ".join([msg.content for msg in recent_messages])
                
                # Parse check-in date - handle multiple formats including "21st January"
                check_in = None
                if check_in_str.lower() in ["tomorrow", "tomorrow's"]:
                    check_in = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
                elif "today" in check_in_str.lower():
                    check_in = datetime.now().strftime("%Y-%m-%d")
                else:
                    # Try to parse various date formats
                    try:
                        # Try YYYY-MM-DD format
                        datetime.strptime(check_in_str, "%Y-%m-%d")
                        check_in = check_in_str
                    except:
                        # Try parsing "21st January" or "22nd January" format
                        date_patterns = [
                            r'(\d{1,2})(?:st|nd|rd|th)?\s+(january|february|march|april|may|june|july|august|september|october|november|december)',
                            r'(\d{4}-\d{2}-\d{2})',  # YYYY-MM-DD
                            r'(\d{1,2})/(\d{1,2})/(\d{4})'  # MM/DD/YYYY
                        ]
                        
                        for pattern in date_patterns:
                            match = re.search(pattern, check_in_str, re.IGNORECASE)
                            if match:
                                if len(match.groups()) == 2:  # "21st January" format
                                    day = int(match.group(1))
                                    month_name = match.group(2).lower()
                                    months = {
                                        'january': 1, 'february': 2, 'march': 3, 'april': 4,
                                        'may': 5, 'june': 6, 'july': 7, 'august': 8,
                                        'september': 9, 'october': 10, 'november': 11, 'december': 12
                                    }
                                    if month_name in months:
                                        current_year = datetime.now().year
                                        # If date is in the past, assume next year
                                        if months[month_name] < datetime.now().month or (months[month_name] == datetime.now().month and day < datetime.now().day):
                                            current_year += 1
                                        check_in = f"{current_year}-{months[month_name]:02d}-{day:02d}"
                                        break
                                elif len(match.groups()) == 1:  # YYYY-MM-DD format
                                    check_in = match.group(1)
                                    break
                        
                        # If still no date, look in conversation history
                        if not check_in:
                            date_pattern = r'(\d{4}-\d{2}-\d{2})'
                            matches = re.findall(date_pattern, conversation_text)
                            if matches:
                                check_in = matches[0]
                            else:
                                # Try "21st January" pattern in conversation
                                conv_pattern = r'(\d{1,2})(?:st|nd|rd|th)?\s+(january|february|march|april|may|june|july|august|september|october|november|december)'
                                conv_match = re.search(conv_pattern, conversation_text, re.IGNORECASE)
                                if conv_match:
                                    day = int(conv_match.group(1))
                                    month_name = conv_match.group(2).lower()
                                    months = {
                                        'january': 1, 'february': 2, 'march': 3, 'april': 4,
                                        'may': 5, 'june': 6, 'july': 7, 'august': 8,
                                        'september': 9, 'october': 10, 'november': 11, 'december': 12
                                    }
                                    if month_name in months:
                                        current_year = datetime.now().year
                                        if months[month_name] < datetime.now().month or (months[month_name] == datetime.now().month and day < datetime.now().day):
                                            current_year += 1
                                        check_in = f"{current_year}-{months[month_name]:02d}-{day:02d}"
                        
                        if not check_in:
                            return f"❌ Could not parse check-in date: {check_in_str}. Please provide date as '21st January' or YYYY-MM-DD format."
                
                # Parse check-out date - handle multiple formats including "22nd January"
                check_out = None
                if "night" in check_out_str.lower() or "nights" in check_out_str.lower():
                    # Extract number of nights
                    nights_match = re.search(r'(\d+)\s*(?:night|nights)', check_out_str.lower())
                    if nights_match:
                        nights = int(nights_match.group(1))
                        check_in_date = datetime.strptime(check_in, "%Y-%m-%d")
                        check_out = (check_in_date + timedelta(days=nights)).strftime("%Y-%m-%d")
                    else:
                        # Default to 1 night
                        check_in_date = datetime.strptime(check_in, "%Y-%m-%d")
                        check_out = (check_in_date + timedelta(days=1)).strftime("%Y-%m-%d")
                else:
                    # Try to parse various date formats
                    try:
                        datetime.strptime(check_out_str, "%Y-%m-%d")
                        check_out = check_out_str
                    except:
                        # Try parsing "22nd January" format
                        date_patterns = [
                            r'(\d{1,2})(?:st|nd|rd|th)?\s+(january|february|march|april|may|june|july|august|september|october|november|december)',
                            r'(\d{4}-\d{2}-\d{2})',  # YYYY-MM-DD
                            r'(\d{1,2})/(\d{1,2})/(\d{4})'  # MM/DD/YYYY
                        ]
                        
                        for pattern in date_patterns:
                            match = re.search(pattern, check_out_str, re.IGNORECASE)
                            if match:
                                if len(match.groups()) == 2:  # "22nd January" format
                                    day = int(match.group(1))
                                    month_name = match.group(2).lower()
                                    months = {
                                        'january': 1, 'february': 2, 'march': 3, 'april': 4,
                                        'may': 5, 'june': 6, 'july': 7, 'august': 8,
                                        'september': 9, 'october': 10, 'november': 11, 'december': 12
                                    }
                                    if month_name in months:
                                        current_year = datetime.now().year
                                        if months[month_name] < datetime.now().month or (months[month_name] == datetime.now().month and day < datetime.now().day):
                                            current_year += 1
                                        check_out = f"{current_year}-{months[month_name]:02d}-{day:02d}"
                                        break
                                elif len(match.groups()) == 1:  # YYYY-MM-DD format
                                    check_out = match.group(1)
                                    break
                        
                        # If still no date, look in conversation history for second date
                        if not check_out:
                            # Look for all date patterns in conversation (both "21st January" and YYYY-MM-DD)
                            all_dates = []
                            
                            # Find "21st January" style dates
                            conv_pattern = r'(\d{1,2})(?:st|nd|rd|th)?\s+(january|february|march|april|may|june|july|august|september|october|november|december)'
                            for conv_match in re.finditer(conv_pattern, conversation_text + " " + check_in_str + " " + check_out_str, re.IGNORECASE):
                                day = int(conv_match.group(1))
                                month_name = conv_match.group(2).lower()
                                months = {
                                    'january': 1, 'february': 2, 'march': 3, 'april': 4,
                                    'may': 5, 'june': 6, 'july': 7, 'august': 8,
                                    'september': 9, 'october': 10, 'november': 11, 'december': 12
                                }
                                if month_name in months:
                                    current_year = datetime.now().year
                                    if months[month_name] < datetime.now().month or (months[month_name] == datetime.now().month and day < datetime.now().day):
                                        current_year += 1
                                    date_str = f"{current_year}-{months[month_name]:02d}-{day:02d}"
                                    all_dates.append(date_str)
                            
                            # Also find YYYY-MM-DD dates
                            date_pattern = r'(\d{4}-\d{2}-\d{2})'
                            all_dates.extend(re.findall(date_pattern, conversation_text + " " + check_in_str + " " + check_out_str))
                            
                            # Remove duplicates and sort
                            all_dates = sorted(list(set(all_dates)))
                            
                            if len(all_dates) > 1:
                                # If check-in is first date, check-out is second
                                if check_in == all_dates[0]:
                                    check_out = all_dates[1]
                                else:
                                    check_out = all_dates[-1]  # Use last date
                            elif len(all_dates) == 1:
                                # Only one date found - assume check-out is 1 day after check-in
                                if check_in != all_dates[0]:
                                    check_out = all_dates[0]
                                else:
                                    check_in_date = datetime.strptime(check_in, "%Y-%m-%d")
                                    check_out = (check_in_date + timedelta(days=1)).strftime("%Y-%m-%d")
                            else:
                                # Default to 1 night after check-in
                                check_in_date = datetime.strptime(check_in, "%Y-%m-%d")
                                check_out = (check_in_date + timedelta(days=1)).strftime("%Y-%m-%d")
                        
                        if not check_out:
                            return f"❌ Could not parse check-out date: {check_out_str}. Please provide date as '22nd January' or YYYY-MM-DD format."
                
                # Search for the room type
                search_results = get_dense_retriever().search_hotels(room_type, k=10)  # Show more rooms
                
                if not search_results:
                    return f"❌ Room type '{room_type}' not found. Please search for available rooms first."
                
                # Use first result
                best_match = search_results[0]
                sheet_name = best_match.get('sheet_name')
                row_data = best_match.get('row_data', {})
                
                # Get sheet structure
                structure = sheets_manager.get_sheet_structure(sheet_name)
                headers = structure.get('headers', [])
                
                # Extract Room ID
                room_id = None
                for key in ['Room ID', 'room_id', 'Room Id', 'ID']:
                    if key in row_data:
                        room_id = str(row_data[key]).strip()
                        break
                # Also check headers
                if not room_id:
                    for idx, header in enumerate(headers):
                        if 'room id' in str(header).lower() or (header == 'ID' and idx < len(row_data)):
                            room_id = str(row_data.get(header, '')).strip() if header in row_data else ''
                            break
                
                # Extract Room Name
                room_name = None
                name_col = structure.get('name_column')
                if name_col is not None and name_col < len(headers):
                    name_key = headers[name_col]
                    room_name = row_data.get(name_key, '')
                else:
                    for key in ['Room Name', 'room_name', 'Name']:
                        if key in row_data:
                            room_name = str(row_data[key]).strip()
                            break
                
                # Get price per night
                price_col = structure.get('price_column')
                price_per_night = '0'
                
                # Try multiple ways to get price
                if price_col is not None and price_col < len(headers):
                    price_key = headers[price_col]
                    price_per_night = row_data.get(price_key, '0')
                    print(f"💰 Found price from column '{price_key}': {price_per_night}")
                
                # Fallback: try common price column names (case-insensitive)
                if not price_per_night or price_per_night == '0' or price_per_night == '':
                    for price_key in ['price', 'Price', 'rate', 'Rate', 'cost', 'Cost', 'amount', 'Amount']:
                        if price_key in row_data and row_data[price_key]:
                            price_per_night = str(row_data[price_key]).strip()
                            if price_per_night and price_per_night != '0':
                                print(f"💰 Found price from fallback '{price_key}': {price_per_night}")
                                break
                
                # If still no price, use default
                if not price_per_night or price_per_night == '0' or price_per_night == '':
                    print(f"⚠️ No price found, using default")
                    price_per_night = '0'
                
                # Calculate total price: price_per_night * number_of_nights * num_rooms
                try:
                    from datetime import datetime
                    check_in_date = datetime.strptime(check_in, "%Y-%m-%d")
                    check_out_date = datetime.strptime(check_out, "%Y-%m-%d")
                    nights = (check_out_date - check_in_date).days
                    if nights <= 0:
                        nights = 1  # Minimum 1 night
                    
                    price_per_night_float = float(str(price_per_night).replace(',', '').replace('Nu.', '').replace('$', '').strip())
                    num_rooms_int = int(num_rooms) if num_rooms else 1
                    total_price = price_per_night_float * nights * num_rooms_int
                    price = str(total_price)
                    print(f"💰 Price calculation: {price_per_night} per night × {nights} nights × {num_rooms} rooms = {total_price}")
                except Exception as e:
                    print(f"⚠️ Price calculation error: {e}, using price_per_night as total")
                    price = price_per_night  # Fallback to price per night
                
                # Get pending bookings sheet
                pending_sheet = sheets_manager._get_or_create_pending_bookings_sheet()
                
                # Check for duplicate bookings before creating
                try:
                    bookings_data = sheets_manager.read_all_data(pending_sheet)
                    if bookings_data and len(bookings_data) > 1:
                        headers_row = bookings_data[0]
                        try:
                            phone_idx = None
                            room_type_idx = None
                            check_in_idx = None
                            status_idx = None
                            
                            # Find column indices
                            for idx, header in enumerate(headers_row):
                                header_lower = str(header).lower()
                                if 'phone' in header_lower:
                                    phone_idx = idx
                                elif 'room_type' in header_lower or 'room type' in header_lower:
                                    room_type_idx = idx
                                elif 'check-in' in header_lower or 'check_in' in header_lower:
                                    check_in_idx = idx
                                elif 'status' in header_lower:
                                    status_idx = idx
                            
                            if phone_idx is not None and room_type_idx is not None and check_in_idx is not None:
                                for row in bookings_data[1:]:
                                    if len(row) > max(phone_idx, room_type_idx, check_in_idx):
                                        row_phone = str(row[phone_idx]).strip()
                                        row_room = str(row[room_type_idx]).strip().lower()
                                        row_checkin = str(row[check_in_idx]).strip()
                                        row_status = str(row[status_idx]).strip().lower() if status_idx is not None and status_idx < len(row) else 'pending'
                                        
                                        # Check for duplicate (same customer, room, check-in date, pending status)
                                        if (row_phone == str(phone).strip() and
                                            row_room == room_type.lower() and
                                            row_checkin == check_in and
                                            row_status == 'pending'):
                                            booking_id_existing = row[0] if len(row) > 0 else "N/A"
                                            print(f"⚠️ Duplicate booking prevented: {booking_id_existing}")
                                            return f"✅ You already have a pending booking for {room_type} on {check_in}! Booking ID: {booking_id_existing}\nOur team will contact you at {phone} for confirmation."
                        except (ValueError, IndexError) as e:
                            print(f"⚠️ Error checking duplicates: {e}")
                            pass
                except Exception as e:
                    print(f"⚠️ Error reading pending bookings: {e}")
                    pass
                
                # Check date-based availability if room_id is available
                if room_id:
                    is_available, availability_msg = sheets_manager.check_room_availability_by_date(
                        room_id, check_in, check_out
                    )
                    if not is_available:
                        return f"❌ {availability_msg}\n\nPlease choose different dates or another room."
                
                # Validate dates
                try:
                    check_in_date = datetime.strptime(check_in, "%Y-%m-%d")
                    check_out_date = datetime.strptime(check_out, "%Y-%m-%d")
                    
                    if check_in_date >= check_out_date:
                        return "❌ Check-in date must be before check-out date. Please correct your dates."
                    
                    if check_in_date.date() < datetime.now().date():
                        return "❌ Check-in date cannot be in the past. Please choose a future date."
                    
                    # Calculate number of nights
                    nights = (check_out_date - check_in_date).days
                    if nights <= 0:
                        return "❌ Invalid date range. Please ensure check-out is after check-in."
                
                except ValueError:
                    return "❌ Invalid date format. Please provide dates in YYYY-MM-DD format."
                
                # Check room capacity if room info is available
                if room_id:
                    room_info = sheets_manager.get_room_info(room_id)
                    if room_info:
                        # Try to find max guest capacity
                        max_guests = None
                        for key in ['Max Guest', 'max_guest', 'Max Guests', 'max_guests', 'Capacity', 'capacity']:
                            if key in room_info:
                                try:
                                    max_guests = int(float(str(room_info[key]).strip()))
                                    break
                                except:
                                    pass
                        
                        if max_guests:
                            try:
                                guests_int = int(guests)
                                if guests_int > max_guests:
                                    return f"❌ This room can accommodate a maximum of {max_guests} guests, but you requested {guests_int} guests. Please reduce the number of guests or book additional rooms."
                            except:
                                pass
                
                # Create booking
                booking_id = sheets_manager.create_booking(
                    customer_name=customer_name,
                    phone=phone,
                    check_in=check_in,
                    check_out=check_out,
                    room_type=room_type,
                    room_name=room_name or room_type,
                    room_id=room_id or '',
                    num_rooms=int(num_rooms) if num_rooms else 1,
                    guests=int(guests) if guests else 2,
                    price=float(price) if price else 0.0,
                    status="pending"
                )
                
                if booking_id:
                    # Friendly, well-formatted confirmation message
                    return f"""✅ Booking confirmed! 

🆔 Booking ID: {booking_id}
🛏️ Room: {room_type}
📅 Check-in: {check_in}
📅 Check-out: {check_out}
💰 Total Price: Nu.{price}

Our team will contact you at {phone} for confirmation and payment. Thank you for choosing us! 😊"""
                else:
                    # If booking failed, check if there are other available rooms of the same type
                    if room_type:
                        # Map room_type to search term
                        room_type_map = {
                            'Twin Room': 'twin',
                            'Double Room': 'double',
                            'Two Bed Room Villa': 'villa',
                            'Twin': 'twin',
                            'Double': 'double',
                            'Villa': 'villa'
                        }
                        search_type = room_type_map.get(room_type, room_type.lower())
                        available_rooms = sheets_manager.get_available_rooms_by_type(search_type, check_in, check_out)
                        
                        if available_rooms and len(available_rooms) > 0:
                            # There are other rooms of this type available
                            room_list = ", ".join([r.get('room_id', '') for r in available_rooms[:3]])
                            return f"❌ The specific room you selected is not available for these dates. However, we have other {room_type} rooms available (IDs: {room_list}). Would you like me to book one of these instead? Just say 'yes' and I'll select an available room for you! 😊"
                        else:
                            return f"❌ Sorry, no {room_type} rooms are available for the dates {check_in} to {check_out}. Please try different dates or another room type."
                    else:
                        return "❌ Sorry, I couldn't create your booking. Please try again or contact us directly."
                    
            except Exception as e:
                print(f"❌ Booking creation error: {e}")
                traceback.print_exc()
                return f"Error creating booking: {str(e)}"
        
        # REMOVED: get_product_details, check_availability, get_recommendations, cancel_order - System is now hotel reservations only
        
        def get_help(empty: str = "") -> str:
            """Get help about available commands."""
            return """
🤖 **I can help you with hotel reservations!**

🏨 **Hotel Bookings:**
- Check room availability for your dates
- Example: "rooms available for 21st January", "show me available rooms for next week"

📅 **Booking Process:**
- Tell me your travel dates and room preference
- I'll show you available rooms and create a booking
- Example: "I want to book a single room from 21st to 22nd January"

📋 **Checking Booking Status:**
- Check your booking status
- Example: "check my booking", "what's my booking status"

💡 **Tips:**
- Just chat naturally - I understand your intent!
- I'll ask for any missing information (check-in date, check-out date, room type)
- All bookings go to our team for confirmation and payment
            """
        
        # Create Tool objects - Hotel reservations only
        tools = [
            Tool(
                name="SearchRooms",
                func=search_rooms,
                description="Search for available hotel rooms. Use this FIRST when customer asks about room availability or wants to book. Input: search query with dates (e.g., 'rooms available for 21st January' or 'single room for tomorrow')."
            ),
            Tool(
                name="CreateBooking",
                func=create_booking,
                description="Create hotel booking. Use ONLY when customer explicitly confirms after you show booking summary (says 'yes', 'confirm', 'proceed'). Input: 'room_type, check_in (YYYY-MM-DD or 'tomorrow'), check_out (YYYY-MM-DD or '2 nights'), customer_name, phone, [num_rooms], [guests]'. Customer name and phone are auto-extracted from context if not provided. Dates can be relative: 'tomorrow' for check-in, '2 nights' for check-out (will calculate from check-in). Extract dates from conversation if customer said 'tomorrow' or '2 nights'."
            ),
            Tool(
                name="CheckBookingStatus",
                func=check_booking_status,
                description="Check booking status. Input: booking_id, phone number, or customer name."
            ),
            Tool(
                name="GetHelp",
                func=get_help,
                description="Get help about available commands. Input: any text (ignored)."
            ),
        ]
        
        return tools
    
    def _get_status_emoji(self, status: str) -> str:
        """Get emoji for order status."""
        status = status.lower()
        if 'pending' in status:
            return "⏳"
        elif 'confirmed' in status or 'approved' in status:
            return "✅"
        elif 'cancelled' in status or 'rejected' in status:
            return "❌"
        elif 'completed' in status or 'delivered' in status:
            return "🎉"
        elif 'shipped' in status:
            return "🚚"
        else:
            return "📋"
    
    def _create_universal_agent(self):
        """Create universal ReAct agent using LangChain 1.2.0 API."""
        # System prompt for the agent
        system_prompt = """You are a friendly and helpful WhatsApp assistant for a hotel. You're warm, conversational, and make customers feel welcome! 😊

You help customers check room availability and make hotel room reservations. You have access to Google Sheets containing room availability data and booking information.

────────────────────────
CORE PRINCIPLES
────────────────────────
1. ALWAYS be friendly, warm, and conversational
2. Use natural WhatsApp-style language (concise but polite)
3. Format messages clearly with line breaks for readability
4. Use emojis sparingly (1-2 per message max)
5. Ask ONLY ONE question at a time if information is missing
6. ALWAYS ask for number of nights if not provided
7. NEVER ask for payment details — a staff member will call
8. NEVER assume dates or number of nights

────────────────────────
SERVICE INQUIRIES (IMPORTANT)
────────────────────────
- “What services do you provide?” / “What do you offer?”
  → Give a brief 2-3 line response ONLY (DO NOT use SearchRooms)

Example:
"Hi there! 😊  
We offer comfortable hotel room bookings. I can help you check room availability and make reservations for your preferred dates.  
When would you like to stay?"

- “Show me available rooms” / “What rooms do you have?”
  → Use SearchRooms

────────────────────────
BOOKING FLOW (FOLLOW EXACTLY — NO SKIPPING)
────────────────────────

────────────────────────
STEP 1 - AVAILABILITY CHECK
────────────────────────
Trigger phrases:
- “Check room availability”
- “Show me available rooms”
- “Rooms available for [dates]”

Action:
→ Use SearchRooms
→ Show ONLY available rooms
→ DO NOT show booking summary
→ DO NOT calculate price

Example:
"Here are the available rooms for your dates:

🛏️ Single Room - Nu.800/night - 10 available  
🛏️ Double Room - Nu.1,200/night - 15 available  
🛏️ Triple Room - Nu.1,500/night - 8 available  

Which room would you like? 😊"

────────────────────────
STEP 2 - ROOM SELECTION (STRICT)
────────────────────────
When the customer says:
- “I want a single room”
- “How about one double room”
- “I'll take the triple room”

RULE:
Before showing a booking summary, ALL of the following MUST be known:
1. Room type
2. Check-in date
3. Number of nights OR check-out date

❗ CRITICAL:
- If ANY of the above is missing:
  → Ask ONE clear question to collect the missing info
  → DO NOT show booking summary
  → DO NOT calculate price

Example questions:
- “How many nights will you be staying?”
- “What date would you like to check in?”

────────────────────────
STEP 3 - BOOKING SUMMARY (ONLY WHEN INFO IS COMPLETE)
────────────────────────
ONLY when room type + dates + number of nights are known:

Show booking summary EXACTLY in this format:

"Great! Here's your booking summary:

Room: [Room Type]  
Check-in: [Date]  
Check-out: [Date]  
Total Price: Nu.[Amount]  

Would you like to confirm this booking? Just reply 'yes' or 'confirm'! 😊"

❗ Formatting rules:
- Each field MUST be on its own line
- Use line breaks exactly as shown
- DO NOT create booking yet

────────────────────────
STEP 4 - CONFIRMATION (CREATE BOOKING)
────────────────────────
ONLY when the customer explicitly says:
- “yes”
- “confirm”
- “proceed”
- “ok”
- “create it”

Action:
→ Use CreateBooking tool immediately

CreateBooking format:
room_type, check_in, check_out, customer_name, phone

────────────────────────
CRITICAL RULES (NON-NEGOTIABLE)
────────────────────────
- “Check availability” = Use SearchRooms ONLY
- NEVER show booking summary before availability
- NEVER assume dates or number of nights
- NEVER create booking without explicit confirmation
- NEVER skip steps
- If information is missing → ask ONE question only

────────────────────────
AVAILABLE TOOLS
────────────────────────
- SearchRooms  
  Use when checking availability or showing room options

- CreateBooking  
  Use ONLY after customer confirms booking summary

- CheckBookingStatus  
  Use to check existing bookings

- GetHelp  
  Show help menu

────────────────────────
EXAMPLE FLOW (REFERENCE)
────────────────────────
Customer: “Can you check room availability for 21st January?”
→ Use SearchRooms
→ Show rooms only

Customer: “I want a single room”
→ If nights missing: ask “How many nights will you be staying?”

Customer: “2 nights”
→ Show booking summary

Customer: “Yes”
→ Use CreateBooking
"""
        
        try:
            try:    
                from langchain.agents import create_agent
                agent_graph = create_agent(
                    model=self.llm,
                    tools=self.tools,
                    system_prompt=system_prompt
                )
                
                # Create wrapper for compatibility
                class LangGraphExecutor:
                        def __init__(self, graph, tools):
                            self.graph = graph
                            self.tools = tools
                            self.verbose = config.DEBUG
                        
                        def invoke(self, input_dict):
                            try:
                                # Extract input message
                                input_text = input_dict.get("input", "")
                                if "Customer:" in input_text:
                                    # Extract just the message part
                                    parts = input_text.split("Message:")
                                    if len(parts) > 1:
                                        input_text = parts[1].strip()
                                
                                # Invoke the graph
                                result = self.graph.invoke({"messages": [HumanMessage(content=input_text)]})
                                
                                # Extract the final message - ensure COMPLETE response
                                if isinstance(result, dict) and "messages" in result:
                                    messages = result["messages"]
                                    if messages:
                                        # Find the last assistant message with actual content (not tool calls)
                                        # Look for the longest, most complete message
                                        content = None
                                        longest_content = ""
                                        for msg in reversed(messages):
                                            msg_content = None
                                            if hasattr(msg, "content") and msg.content:
                                                msg_content = str(msg.content)
                                            elif isinstance(msg, dict):
                                                msg_content = msg.get("content", "")
                                            
                                            if msg_content and not msg_content.startswith(("Action", "Tool", "Thought")):
                                                # Prefer longer, more complete messages
                                                if len(msg_content) > len(longest_content):
                                                    longest_content = msg_content
                                                    content = msg_content
                                        
                                        # Use the longest content found
                                        if longest_content and len(longest_content) > 20:
                                            content = longest_content
                                        
                                        # Fallback: use last message if no content found
                                        if not content or len(str(content).strip()) < 10:
                                            last_message = messages[-1]
                                            if hasattr(last_message, "content"):
                                                content = last_message.content
                                            elif isinstance(last_message, dict):
                                                content = last_message.get("content", str(last_message))
                                            else:
                                                content = str(last_message)
                                        
                                        # Ensure content is not empty or None
                                        if not content or content.strip() == "":
                                            content = "I apologize, I couldn't process that request."
                                        
                                        return {"output": str(content)}
                                
                                # Fallback
                                return {"output": str(result)}
                            except Exception as e:
                                print(f"❌ Agent invoke error: {e}")
                                traceback.print_exc()
                                return {"output": f"Error processing request: {str(e)}"}
                    
                executor = LangGraphExecutor(agent_graph, self.tools)
                print("✅ Universal agent created successfully (LangGraph)")
                return executor
            except ImportError:
                # create_agent not available, use fallback
                print("⚠️ create_agent not available, using fallback (ZeroShotAgent)")
                raise
            except Exception as e:
                print(f"⚠️ LangGraph agent creation failed: {e}, using fallback")
                raise
        except:
            # Fallback: Use older ZeroShotAgent approach
            if not HAS_FALLBACK_AGENT:
                raise RuntimeError("No agent creation method available. Please install langchain or langgraph.")
            
            print("⚠️ Using fallback agent creation (ZeroShotAgent)")
            
            prefix = system_prompt + "\n\nBegin! Use tools appropriately.\n\nHuman: {input}\nAssistant:"
            
            prompt = ZeroShotAgent.create_prompt(
                self.tools,
                prefix=prefix,
                suffix="",
                input_variables=["input"]
            )
            
            memory = ConversationBufferMemory(
                memory_key="chat_history",
                return_messages=True,
                max_token_limit=1500
            )
            
            llm_chain = LLMChain(llm=self.llm, prompt=prompt)
            agent = ZeroShotAgent(
                llm_chain=llm_chain,
                tools=self.tools,
                verbose=config.DEBUG,
                max_iterations=3
            )
            
            agent_executor = AgentExecutor.from_agent_and_tools(
                agent=agent,
                tools=self.tools,
                memory=memory,
                verbose=config.DEBUG,
                handle_parsing_errors=True,
                max_iterations=2,  # Reduced to prevent duplicate tool calls
                early_stopping_method="generate",
                max_execution_time=20  # Reduced timeout
            )
            
            print("✅ Universal agent created successfully (ZeroShotAgent fallback)")
            return agent_executor
    
    def process_message(self, message: str, customer_phone: str, customer_name: str = "") -> str:
        """Process customer message."""
        start_time = time.time()
        print(f"\n{'='*50}")
        print(f"📱 Message from {customer_name} ({customer_phone}): {message}")
        
        try:
            # Add to session
            session_manager.add_message(customer_phone, "user", message)
            
            # Store customer context for tools to access
            self._current_customer_name = customer_name or 'Guest'
            self._current_customer_phone = customer_phone
            
            # Get conversation history for context
            session = session_manager.get_session(customer_phone)
            conversation_history = ""
            last_hotel_shown = None
            last_booking_summary_shown = False
            has_booking_dates = False
            booking_info = {}  # Store booking details when summary is shown
            extracted_dates = {}  # Store extracted dates from conversation (check_in, check_out, nights)
            
            if session:
                # Get last few messages for context (especially to remember what product was shown)
                recent_messages = session.history[-6:] if len(session.history) > 6 else session.history
                if recent_messages:
                    # Format conversation history to help agent remember context
                    conv_lines = []
                    for msg in recent_messages:
                        if msg.role == "assistant":
                            # Try to extract product names from assistant messages
                            content = msg.content
                            content_lower = content.lower()
                            conv_lines.append(f"Assistant: {content}")
                            
                            # Check if booking summary was shown (asking for confirmation)
                            if any(phrase in content_lower for phrase in ["would you like to confirm", "shall i proceed", "should i create", "confirm this booking"]):
                                last_booking_summary_shown = True
                                # Try to extract booking details from summary
                                # BUT: Only use these if extracted_dates doesn't have current dates (user may have corrected dates)
                                room_match = re.search(r'Room[:\s]+([^\n,]+)', content, re.IGNORECASE)
                                if room_match:
                                    booking_info['room_type'] = room_match.group(1).strip()
                                # Only extract dates from previous summary if we don't have current extracted_dates
                                # This allows user to correct dates (e.g., "No on 25") and have the correction take priority
                                if not extracted_dates.get('check_in'):
                                    checkin_match = re.search(r'Check-in[:\s]+([^\n,]+)', content, re.IGNORECASE)
                                    if checkin_match:
                                        booking_info['check_in'] = checkin_match.group(1).strip()
                                if not extracted_dates.get('check_out'):
                                    checkout_match = re.search(r'Check-out[:\s]+([^\n,]+)', content, re.IGNORECASE)
                                    if checkout_match:
                                        booking_info['check_out'] = checkout_match.group(1).strip()
                            
                            # Look for product mentions in assistant messages
                            if "Nu." in content or "price" in content.lower():
                                # Try to extract product name - look for patterns
                                # Pattern 1: "Product Name for Nu.price" or "Product Name Nu.price"
                                product_match = re.search(r'([A-Z][a-zA-Z\s]+?)\s+(?:for|at|is)\s*Nu.', content)
                                if not product_match:
                                    product_match = re.search(r'([A-Z][a-zA-Z\s]+?)\s+Nu.', content)
                                if not product_match:
                                    # Pattern 2: Look for capitalized words before price indicators
                                    product_match = re.search(r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)', content)
                                if product_match:
                                    potential_product = product_match.group(1).strip()
                                    # Filter out common words and validate
                                    if (len(potential_product) > 3 and 
                                        potential_product not in ["The", "We", "Our", "You", "Would", "Which", "What"] and
                                        not potential_product.startswith("Would")):
                                        # REMOVED: Product tracking - System is now hotel reservations only
                                        pass
                            
                            # Check for hotel/room mentions
                            if any(word in content_lower for word in ["room", "hotel", "single", "double", "triple", "suite", "quad", "family"]):
                                room_patterns = [
                                    r'(?:Single|Double|Triple|Quad|Family|Suite|Deluxe|Standard)[\s\w]*Room',
                                    r'Room[^,\n]+Nu.\s*\d+'
                                ]
                                for pattern in room_patterns:
                                    match = re.search(pattern, content, re.IGNORECASE)
                                    if match:
                                        last_hotel_shown = match.group(0).strip()
                                        break
                            
                            # Check for booking dates in previous messages
                            if any(word in content_lower for word in ["tomorrow", "night", "nights", "check-in", "check-out", "check in", "check out", "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december", "202", "21st", "22nd", "sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]):
                                has_booking_dates = True
                        else:
                            conv_lines.append(f"Customer: {msg.content}")
                            customer_msg_lower = msg.content.lower()
                            # Also check customer messages for dates
                            if any(word in customer_msg_lower for word in ["tomorrow", "night", "nights", "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december", "202", "21st", "22nd", "sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]):
                                has_booking_dates = True
                            
                            # Extract dates from customer messages and store them
                            # Check for "next Sunday", "coming Sunday", etc.
                            if "next sunday" in customer_msg_lower or "this next sunday" in customer_msg_lower or "coming sunday" in customer_msg_lower:
                                today = datetime.now()
                                days_ahead = 6 - today.weekday()  # Days until next Sunday
                                if days_ahead <= 0:
                                    days_ahead += 7
                                next_sunday = today + timedelta(days=days_ahead)
                                extracted_dates['check_in'] = next_sunday.strftime("%dth %B %Y")
                                extracted_dates['check_in_raw'] = next_sunday.strftime("%Y-%m-%d")
                                # Check for number of nights - only set checkout if explicitly provided
                                nights_match = re.search(r'(\d+)\s*nights?', customer_msg_lower)
                                if nights_match:
                                    num_nights = int(nights_match.group(1))
                                    extracted_dates['check_out'] = (next_sunday + timedelta(days=num_nights)).strftime("%dth %B %Y")
                                    extracted_dates['check_out_raw'] = (next_sunday + timedelta(days=num_nights)).strftime("%Y-%m-%d")
                                    extracted_dates['nights'] = num_nights
                                # Don't default to 1 night - let user specify
                            
                            # Extract "for X nights" pattern
                            nights_match = re.search(r'for\s+(\d+)\s*nights?', customer_msg_lower)
                            if nights_match and 'check_in' in extracted_dates:
                                num_nights = int(nights_match.group(1))
                                extracted_dates['nights'] = num_nights
                                # Recalculate check_out if check_in exists
                                if 'check_in_raw' in extracted_dates:
                                    check_in_dt = datetime.strptime(extracted_dates['check_in_raw'], "%Y-%m-%d")
                                    check_out_dt = check_in_dt + timedelta(days=num_nights)
                                    extracted_dates['check_out'] = check_out_dt.strftime("%dth %B %Y")
                                    extracted_dates['check_out_raw'] = check_out_dt.strftime("%Y-%m-%d")
                            
                            # Extract specific dates like "21st January"
                            date_match = re.search(r'(\d{1,2})(?:st|nd|rd|th)?\s+(january|february|march|april|may|june|july|august|september|october|november|december)(?:\s+(\d{4}))?', customer_msg_lower)
                            if date_match:
                                day = date_match.group(1)
                                month = date_match.group(2).capitalize()
                                year = date_match.group(3) if date_match.group(3) else "2026"
                                try:
                                    date_str = f"{day} {month} {year}"
                                    parsed_date = datetime.strptime(date_str, "%d %B %Y")
                                    if 'check_in' not in extracted_dates:
                                        extracted_dates['check_in'] = parsed_date.strftime("%dth %B %Y")
                                        extracted_dates['check_in_raw'] = parsed_date.strftime("%Y-%m-%d")
                                    else:
                                        extracted_dates['check_out'] = parsed_date.strftime("%dth %B %Y")
                                        extracted_dates['check_out_raw'] = parsed_date.strftime("%Y-%m-%d")
                                except:
                                    pass
                            
                            # Extract standalone day numbers (like "25") - will be combined with "this month" or "next month"
                            standalone_day_match = re.search(r'^\s*(\d{1,2})\s*$', customer_msg_lower.strip())
                            if standalone_day_match:
                                day_num = int(standalone_day_match.group(1))
                                today = datetime.now()
                                try:
                                    # Check if "this month" or "next month" was mentioned in recent messages
                                    # Look in the last few customer messages
                                    if session:
                                        recent_customer_msgs = [msg.content.lower() for msg in session.history[-5:] if msg.role == "user"]
                                        recent_text_lower = " ".join(recent_customer_msgs)
                                        is_next_month = "next month" in recent_text_lower
                                        is_this_month = "this month" in recent_text_lower or (not is_next_month)
                                        
                                        if is_this_month:
                                            target_date = datetime(today.year, today.month, day_num)
                                            if target_date < today:
                                                if today.month == 12:
                                                    target_date = datetime(today.year + 1, 1, day_num)
                                                else:
                                                    target_date = datetime(today.year, today.month + 1, day_num)
                                        elif is_next_month:
                                            if today.month == 12:
                                                target_date = datetime(today.year + 1, 1, day_num)
                                            else:
                                                target_date = datetime(today.year, today.month + 1, day_num)
                                        else:
                                            # Default to current month if day is in future, otherwise next month
                                            target_date = datetime(today.year, today.month, day_num)
                                            if target_date < today:
                                                if today.month == 12:
                                                    target_date = datetime(today.year + 1, 1, day_num)
                                                else:
                                                    target_date = datetime(today.year, today.month + 1, day_num)
                                        
                                        if 'check_in' not in extracted_dates:
                                            day_str = str(day_num)
                                            suffix = "st" if day_str.endswith('1') and not day_str.endswith('11') else "nd" if day_str.endswith('2') and not day_str.endswith('12') else "rd" if day_str.endswith('3') and not day_str.endswith('13') else "th"
                                            extracted_dates['check_in'] = f"{day_num}{suffix} {target_date.strftime('%B %Y')}"
                                            extracted_dates['check_in_raw'] = target_date.strftime("%Y-%m-%d")
                                except ValueError:
                                    pass
                    conversation_history = "\n\nRecent conversation:\n" + "\n".join(conv_lines)
            
            # Prepare context - ZeroShotAgent expects only 'input' key
            # Include customer info and conversation history in the input string
            instruction = ""
            msg_lower = message.lower()
            
            # Check if this is a "what services" query (should give brief summary, NOT list all items)
            service_inquiry_words = ["what services", "what do you provide", "what do you offer", "what do you have", "services do you", "what can you"]
            is_service_inquiry = any(phrase in msg_lower for phrase in service_inquiry_words) and not any(word in msg_lower for word in ["show", "available", "list", "see"])
            
            # Check if this is an availability check request (should search, NOT show booking summary)
            # Also check for patterns like "rooms on 25", "available on 25", etc.
            availability_check_words = ["check availability", "check room availability", "room availability", "available rooms", "show available", "what rooms", "show me rooms", "show me available"]
            is_availability_check = any(phrase in msg_lower for phrase in availability_check_words)
            
            # Also check for "on [day]" pattern (e.g., "rooms on 25", "available on 25")
            if not is_availability_check:
                on_date_pattern = re.search(r'\bon\s+(\d{1,2})\b', msg_lower)
                if on_date_pattern and any(word in msg_lower for word in ["room", "available", "availability"]):
                    is_availability_check = True
            
            # IMPORTANT: "I want to book a room on [date]" should be treated as availability check, not booking request
            # User is checking what's available, not actually booking yet
            if not is_availability_check:
                # Check for "I want to book" + date pattern (without room type specified)
                book_with_date_pattern = re.search(r'(?:i\s+want\s+to\s+book|want\s+to\s+book|i\s+want\s+a\s+room|want\s+a\s+room).*?\bon\s+(\d{1,2})\b', msg_lower)
                if book_with_date_pattern:
                    # Check if a specific room type is mentioned (if yes, might be booking request)
                    # But if just "room" or no specific type, treat as availability check
                    specific_room_types = ["twin", "double", "villa", "single", "triple", "family", "suite"]
                    has_specific_room_type = any(room_type in msg_lower for room_type in specific_room_types)
                    # If no specific room type mentioned, it's an availability check
                    if not has_specific_room_type or "a room" in msg_lower or "room on" in msg_lower:
                        is_availability_check = True
            
            # REMOVED: Product request tracking - System is now hotel reservations only
            
            # Check for booking dates in message (also check if dates were already found in history)
            has_booking_dates = has_booking_dates or any(word in msg_lower for word in ["tomorrow", "night", "nights", "check-in", "check-out", "check in", "check out", "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december", "21st", "22nd", "sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"])
            
            # Extract dates from patterns like "on 25 january" (with month) or "on 25" (default to current month/year)
            # IMPORTANT: Prioritize "on [day] [month]" pattern over "on [day]" pattern
            on_date_pattern = None
            on_date_with_month_pattern = re.search(r'\bon\s+(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december)', msg_lower)
            if on_date_with_month_pattern:
                # Check for "on [day] [month]" pattern first (e.g., "on 25 january")
                day_num = int(on_date_with_month_pattern.group(1))
                month_name = on_date_with_month_pattern.group(2).lower()
                months = {
                    'january': 1, 'february': 2, 'march': 3, 'april': 4,
                    'may': 5, 'june': 6, 'july': 7, 'august': 8,
                    'september': 9, 'october': 10, 'november': 11, 'december': 12
                }
                try:
                    current_year = datetime.now().year
                    target_date = datetime(current_year, months[month_name], day_num)
                    # If date is in the past, use next year
                    if target_date < datetime.now():
                        target_date = datetime(current_year + 1, months[month_name], day_num)
                    # Format date
                    day_str = str(day_num)
                    if day_str.endswith('1') and not day_str.endswith('11'):
                        suffix = "st"
                    elif day_str.endswith('2') and not day_str.endswith('12'):
                        suffix = "nd"
                    elif day_str.endswith('3') and not day_str.endswith('13'):
                        suffix = "rd"
                    else:
                        suffix = "th"
                    # OVERRIDE existing check_in if user explicitly says "on [day] [month]"
                    extracted_dates['check_in'] = f"{day_num}{suffix} {target_date.strftime('%B %Y')}"
                    extracted_dates['check_in_raw'] = target_date.strftime("%Y-%m-%d")
                    # Clear checkout if it was set, since user is changing check-in date
                    if 'check_out' in extracted_dates:
                        extracted_dates.pop('check_out', None)
                        extracted_dates.pop('check_out_raw', None)
                        extracted_dates.pop('nights', None)
                    # DO NOT set check_out automatically - ask customer for checkout date or number of nights
                    on_date_pattern = on_date_with_month_pattern  # Mark that we found a date pattern
                except (ValueError, KeyError):
                    pass
            # Also check for "on [day]" pattern (without month - default to current month/year)
            if not on_date_pattern:
                on_date_pattern = re.search(r'\bon\s+(\d{1,2})\b', msg_lower)
            if on_date_pattern and not on_date_with_month_pattern:
                # Check if user is correcting a date (e.g., "No on 25" or just "on 25" after previous date mention)
                # Always extract "on [day]" pattern to allow date corrections
                day_num = int(on_date_pattern.group(1))
                today = datetime.now()
                # Use current month and year, but if day is in the past, use next month
                try:
                    target_date = datetime(today.year, today.month, day_num)
                    if target_date < today:
                        # Day is in the past, use next month
                        if today.month == 12:
                            target_date = datetime(today.year + 1, 1, day_num)
                        else:
                            target_date = datetime(today.year, today.month + 1, day_num)
                    # Format date
                    day_str = str(day_num)
                    if day_str.endswith('1') and not day_str.endswith('11'):
                        suffix = "st"
                    elif day_str.endswith('2') and not day_str.endswith('12'):
                        suffix = "nd"
                    elif day_str.endswith('3') and not day_str.endswith('13'):
                        suffix = "rd"
                    else:
                        suffix = "th"
                    # OVERRIDE existing check_in if user explicitly says "on [day]"
                    extracted_dates['check_in'] = f"{day_num}{suffix} {target_date.strftime('%B %Y')}"
                    extracted_dates['check_in_raw'] = target_date.strftime("%Y-%m-%d")
                    # Clear checkout if it was set, since user is changing check-in date
                    if 'check_out' in extracted_dates:
                        extracted_dates.pop('check_out', None)
                        extracted_dates.pop('check_out_raw', None)
                        extracted_dates.pop('nights', None)
                    # DO NOT set check_out automatically - ask customer for checkout date or number of nights
                except ValueError:
                    # Invalid date (e.g., Feb 30), skip
                    pass
            
            # Also extract dates from current message (but "on [day]" takes priority if found)
            if ("next sunday" in msg_lower or "this next sunday" in msg_lower or "coming sunday" in msg_lower) and not on_date_pattern:
                # Only set if "on [day]" pattern wasn't found (to allow corrections)
                today = datetime.now()
                days_ahead = 6 - today.weekday()
                if days_ahead <= 0:
                    days_ahead += 7
                next_sunday = today + timedelta(days=days_ahead)
                day_str = next_sunday.strftime("%d").lstrip("0")
                if day_str.endswith('1') and not day_str.endswith('11'):
                    suffix = "st"
                elif day_str.endswith('2') and not day_str.endswith('12'):
                    suffix = "nd"
                elif day_str.endswith('3') and not day_str.endswith('13'):
                    suffix = "rd"
                else:
                    suffix = "th"
                extracted_dates['check_in'] = f"{day_str}{suffix} {next_sunday.strftime('%B %Y')}"
                extracted_dates['check_in_raw'] = next_sunday.strftime("%Y-%m-%d")
                # Check for number of nights in current message
                nights_match = re.search(r'for\s+(\d+)\s*nights?', msg_lower)
                if nights_match:
                    num_nights = int(nights_match.group(1))
                    check_out_date = next_sunday + timedelta(days=num_nights)
                    day_str_out = check_out_date.strftime("%d").lstrip("0")
                    if day_str_out.endswith('1') and not day_str_out.endswith('11'):
                        suffix_out = "st"
                    elif day_str_out.endswith('2') and not day_str_out.endswith('12'):
                        suffix_out = "nd"
                    elif day_str_out.endswith('3') and not day_str_out.endswith('13'):
                        suffix_out = "rd"
                    else:
                        suffix_out = "th"
                    extracted_dates['check_out'] = f"{day_str_out}{suffix_out} {check_out_date.strftime('%B %Y')}"
                    extracted_dates['check_out_raw'] = check_out_date.strftime("%Y-%m-%d")
                    extracted_dates['nights'] = num_nights
                # DO NOT set check_out automatically - ask customer for checkout date or number of nights
            
            # Extract "for X nights" from current message
            nights_match = re.search(r'for\s+(\d+)\s*nights?', msg_lower)
            if nights_match and 'check_in_raw' in extracted_dates:
                num_nights = int(nights_match.group(1))
                extracted_dates['nights'] = num_nights
                check_in_dt = datetime.strptime(extracted_dates['check_in_raw'], "%Y-%m-%d")
                check_out_dt = check_in_dt + timedelta(days=num_nights)
                day_str_out = str(check_out_dt.day)
                if day_str_out.endswith('1') and not day_str_out.endswith('11'):
                    suffix_out = "st"
                elif day_str_out.endswith('2') and not day_str_out.endswith('12'):
                    suffix_out = "nd"
                elif day_str_out.endswith('3') and not day_str_out.endswith('13'):
                    suffix_out = "rd"
                else:
                    suffix_out = "th"
                extracted_dates['check_out'] = f"{check_out_dt.day}{suffix_out} {check_out_dt.strftime('%B %Y')}"
                extracted_dates['check_out_raw'] = check_out_dt.strftime("%Y-%m-%d")
            
            # Extract checkout date patterns like "until 26th", "checkout 26th", "till 26th"
            checkout_patterns = [
                r'until\s+(\d{1,2})(?:st|nd|rd|th)?',
                r'checkout\s+(\d{1,2})(?:st|nd|rd|th)?',
                r'till\s+(\d{1,2})(?:st|nd|rd|th)?',
                r'check-out\s+(\d{1,2})(?:st|nd|rd|th)?',
                r'check\s+out\s+(\d{1,2})(?:st|nd|rd|th)?'
            ]
            if 'check_in_raw' in extracted_dates and 'check_out_raw' not in extracted_dates:
                for pattern in checkout_patterns:
                    match = re.search(pattern, msg_lower)
                    if match:
                        day_num = int(match.group(1))
                        today = datetime.now()
                        # Use same month/year as check-in date
                        check_in_dt = datetime.strptime(extracted_dates['check_in_raw'], "%Y-%m-%d")
                        try:
                            check_out_date = datetime(check_in_dt.year, check_in_dt.month, day_num)
                            # If checkout is before check-in, use next month
                            if check_out_date <= check_in_dt:
                                if check_in_dt.month == 12:
                                    check_out_date = datetime(check_in_dt.year + 1, 1, day_num)
                                else:
                                    check_out_date = datetime(check_in_dt.year, check_in_dt.month + 1, day_num)
                            day_str_out = str(check_out_date.day)
                            if day_str_out.endswith('1') and not day_str_out.endswith('11'):
                                suffix_out = "st"
                            elif day_str_out.endswith('2') and not day_str_out.endswith('12'):
                                suffix_out = "nd"
                            elif day_str_out.endswith('3') and not day_str_out.endswith('13'):
                                suffix_out = "rd"
                            else:
                                suffix_out = "th"
                            extracted_dates['check_out'] = f"{check_out_date.day}{suffix_out} {check_out_date.strftime('%B %Y')}"
                            extracted_dates['check_out_raw'] = check_out_date.strftime("%Y-%m-%d")
                            extracted_dates['nights'] = (check_out_date - check_in_dt).days
                            break
                        except ValueError:
                            pass
            
            if is_service_inquiry:
                # Customer asks "what services do you provide" - give brief summary, NO UniversalSearch needed
                instruction = f"\n\n⚠️ ACTION REQUIRED: Customer asks what services you provide! Give a BRIEF friendly summary (2-3 lines) mentioning: hotel rooms, food/snacks, products. DO NOT use UniversalSearch - just say what services you offer. Example: 'Hi there! 😊 We offer hotel room bookings, food & snacks, and sports merchandise. Would you like to know more about any specific service?'"
            elif is_availability_check:
                # Customer wants to check availability - use UniversalSearch to show rooms, NOT booking summary
                # Extract dates from the availability check query and remember them
                dates_context = ""
                today = datetime.now()
                current_date_context = f" (Today is {today.strftime('%A, %B %d, %Y')})"
                if extracted_dates:
                    dates_context = f" IMPORTANT: Customer mentioned dates - Check-in: {extracted_dates.get('check_in', '')}, Check-out: {extracted_dates.get('check_out', '')}, Nights: {extracted_dates.get('nights', 1)}. Use these EXACT dates in your response and search query. "
                else:
                    # Check if there's a day number in the query (e.g., "on 25")
                    day_match = re.search(r'\bon\s+(\d{1,2})\b', msg_lower)
                    if day_match:
                        day_num = int(day_match.group(1))
                        # Default to current month if day is in future, otherwise next month
                        try:
                            target_date = datetime(today.year, today.month, day_num)
                            if target_date < today:
                                if today.month == 12:
                                    target_date = datetime(today.year + 1, 1, day_num)
                                else:
                                    target_date = datetime(today.year, today.month + 1, day_num)
                            month_name = target_date.strftime('%B')
                            dates_context = f" IMPORTANT: When customer says 'on {day_num}', interpret it as {day_num}{'st' if day_num % 10 == 1 and day_num != 11 else 'nd' if day_num % 10 == 2 and day_num != 12 else 'rd' if day_num % 10 == 3 and day_num != 13 else 'th'} {month_name} {target_date.year} (current month context). "
                        except ValueError:
                            pass
                instruction = f"\n\n⚠️ ACTION REQUIRED: Customer wants to CHECK ROOM AVAILABILITY!{current_date_context}{dates_context}Use SearchRooms tool with query about rooms/dates. Show available rooms with clear formatting. When mentioning dates in your response, use the dates provided above. DO NOT show booking summary yet - just show available rooms and ask which one they'd like! Remember the dates mentioned in the query for when they select a room."
            # Check if customer is confirming a booking (after booking summary was shown)
            booking_confirmation_words = ["yes", "confirm", "proceed", "ok", "create it", "book it", "sure", "yeah", "yep"]
            is_booking_confirmation = last_booking_summary_shown and any(word in msg_lower for word in booking_confirmation_words)
            
            # Check if customer wants a specific room but booking summary hasn't been shown yet
            # Also check for simple room selection like "one triple room", "single room", etc.
            room_selection_words = ["one", "single", "double", "triple", "quad", "family", "suite"]
            is_simple_room_selection = any(word in msg_lower for word in room_selection_words) and ("room" in msg_lower or "suite" in msg_lower) and last_hotel_shown and not last_booking_summary_shown
            
            # Check for "all the available rooms" or similar phrases
            all_rooms_patterns = ["all the available", "all available rooms", "all the rooms", "book all", "all rooms", "every room"]
            is_all_rooms_request = any(pattern in msg_lower for pattern in all_rooms_patterns) and last_hotel_shown and not last_booking_summary_shown
            
            room_request_patterns = ["how about", "i want", "i'll take", "i'd like", "let me book", "book me"]
            # IMPORTANT: Don't treat "I want to book a room on [date]" as room request if it's just checking availability
            # Only treat as room request if user has seen rooms already (last_hotel_shown) or is selecting a specific room type
            is_potential_room_request = any(pattern in msg_lower for pattern in room_request_patterns) or is_simple_room_selection
            # Exclude availability checks from room requests - if user is checking availability, don't create booking
            is_room_request = is_potential_room_request and not is_availability_check and (last_hotel_shown or has_booking_dates) and not last_booking_summary_shown and not is_all_rooms_request
            
            if is_booking_confirmation:
                # Customer confirmed booking after summary was shown - NOW create it
                room_type = booking_info.get('room_type', last_hotel_shown or 'room')
                # PRIORITIZE extracted_dates over booking_info (user may have corrected dates)
                # Use raw dates (YYYY-MM-DD format) for CreateBooking tool
                check_in = extracted_dates.get('check_in_raw') or booking_info.get('check_in_raw') or booking_info.get('check_in', '')
                check_out = extracted_dates.get('check_out_raw') or booking_info.get('check_out_raw') or booking_info.get('check_out', '')
                # If we have nights but not checkout date, calculate it
                if not check_out:
                    nights_to_use = extracted_dates.get('nights') or booking_info.get('nights', 1)
                    if nights_to_use and check_in:
                        try:
                            # Try to parse check_in if it's not already in YYYY-MM-DD format
                            if len(check_in) == 10 and check_in.count('-') == 2:
                                check_in_date = datetime.strptime(check_in, "%Y-%m-%d")
                            else:
                                # Try to parse from display format or use current extracted_dates
                                check_in_raw = extracted_dates.get('check_in_raw') or booking_info.get('check_in_raw')
                                if check_in_raw:
                                    check_in_date = datetime.strptime(check_in_raw, "%Y-%m-%d")
                                    check_in = check_in_raw
                                else:
                                    raise ValueError("Cannot parse check_in")
                            num_nights = int(nights_to_use)
                            check_out_date = check_in_date + timedelta(days=num_nights)
                            check_out = check_out_date.strftime("%Y-%m-%d")
                        except:
                            pass
                instruction = f"\n\n⚠️ ACTION REQUIRED: Customer confirmed booking! Use CreateBooking IMMEDIATELY with room_type='{room_type}', check_in='{check_in}' (must be YYYY-MM-DD format), check_out='{check_out}' (must be YYYY-MM-DD format). The check_out date MUST be calculated from check_in + number of nights. Extract dates from conversation history if missing. Do NOT ask questions - create the booking NOW!"
            elif is_room_request:
                # Customer wants a specific room AFTER seeing available rooms
                # Check conversation history for dates mentioned previously
                check_in_from_history = None
                check_out_from_history = None
                check_in_raw_from_history = None
                if session:
                    # IMPORTANT: Search messages in REVERSE order (most recent first) to get the LATEST date
                    # This prevents using old dates from previous conversations
                    recent_messages = session.history[-10:] if len(session.history) > 10 else session.history
                    
                    # Search messages in reverse order (most recent first) to find the latest date
                    check_in_from_history_found = False
                    for msg in reversed(recent_messages):
                        msg_text = msg.content.lower()
                        
                        # Check for "on [day] [month]" pattern first (most specific)
                        on_date_with_month = re.search(r'\bon\s+(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december)', msg_text)
                        if on_date_with_month and not check_in_from_history_found:
                            day_num = int(on_date_with_month.group(1))
                            month_name = on_date_with_month.group(2).lower()
                            months = {
                                'january': 1, 'february': 2, 'march': 3, 'april': 4,
                                'may': 5, 'june': 6, 'july': 7, 'august': 8,
                                'september': 9, 'october': 10, 'november': 11, 'december': 12
                            }
                            try:
                                current_year = datetime.now().year
                                target_date = datetime(current_year, months[month_name], day_num)
                                # If date is in the past, use next year
                                if target_date < datetime.now():
                                    target_date = datetime(current_year + 1, months[month_name], day_num)
                                day_str = str(day_num)
                                suffix = "st" if day_str.endswith('1') and not day_str.endswith('11') else "nd" if day_str.endswith('2') and not day_str.endswith('12') else "rd" if day_str.endswith('3') and not day_str.endswith('13') else "th"
                                check_in_from_history = f"{day_num}{suffix} {target_date.strftime('%B %Y')}"
                                check_in_raw_from_history = target_date.strftime("%Y-%m-%d")
                                # Check if number of nights was mentioned in recent messages
                                recent_text_for_nights = " ".join([m.content for m in recent_messages[-5:]])
                                nights_match = re.search(r'for\s+(\d+)\s*nights?', recent_text_for_nights.lower())
                                if nights_match:
                                    num_nights = int(nights_match.group(1))
                                    check_out_date = target_date + timedelta(days=num_nights)
                                    day_str_out = str(check_out_date.day)
                                    suffix_out = "st" if day_str_out.endswith('1') and not day_str_out.endswith('11') else "nd" if day_str_out.endswith('2') and not day_str_out.endswith('12') else "rd" if day_str_out.endswith('3') and not day_str_out.endswith('13') else "th"
                                    check_out_from_history = f"{check_out_date.day}{suffix_out} {check_out_date.strftime('%B %Y')}"
                                check_in_from_history_found = True
                                break  # Found the most recent date, stop searching
                            except (ValueError, KeyError):
                                pass
                    
                    # If no "on [day] [month]" pattern found, check for other patterns
                    if not check_in_from_history_found:
                        recent_text = " ".join([msg.content for msg in recent_messages])
                        recent_text_lower = recent_text.lower()
                        
                        # Also check for standalone day numbers (like "25") combined with "this month" or "next month"
                        if re.search(r'\b(\d{1,2})\b', recent_text_lower):
                            # Look for patterns like "25" + "this month" or "25th of this month"
                            day_match = re.search(r'\b(\d{1,2})(?:st|nd|rd|th)?\b', recent_text_lower)
                            if day_match:
                                day_num = int(day_match.group(1))
                                today = datetime.now()
                                try:
                                    # Check if "this month" or "next month" was mentioned
                                    is_next_month = "next month" in recent_text_lower
                                    is_this_month = "this month" in recent_text_lower
                                    
                                    # Also check assistant messages for date context (e.g., "25th of this month")
                                    for msg in session.history[-10:]:
                                        if msg.role == "assistant":
                                            msg_lower = msg.content.lower()
                                            # Check if assistant mentioned a date with "this month" or "next month"
                                            if f"{day_num}" in msg_lower and ("this month" in msg_lower or "next month" in msg_lower):
                                                is_this_month = "this month" in msg_lower
                                                is_next_month = "next month" in msg_lower
                                                break
                                    
                                    if is_this_month:
                                        target_date = datetime(today.year, today.month, day_num)
                                        if target_date < today:
                                            if today.month == 12:
                                                target_date = datetime(today.year + 1, 1, day_num)
                                            else:
                                                target_date = datetime(today.year, today.month + 1, day_num)
                                    elif is_next_month:
                                        if today.month == 12:
                                            target_date = datetime(today.year + 1, 1, day_num)
                                        else:
                                            target_date = datetime(today.year, today.month + 1, day_num)
                                    else:
                                        # Default to current month if day is in future, otherwise next month
                                        target_date = datetime(today.year, today.month, day_num)
                                        if target_date < today:
                                            if today.month == 12:
                                                target_date = datetime(today.year + 1, 1, day_num)
                                            else:
                                                target_date = datetime(today.year, today.month + 1, day_num)
                                    
                                    day_str = str(day_num)
                                    suffix = "st" if day_str.endswith('1') and not day_str.endswith('11') else "nd" if day_str.endswith('2') and not day_str.endswith('12') else "rd" if day_str.endswith('3') and not day_str.endswith('13') else "th"
                                    check_in_from_history = f"{day_num}{suffix} {target_date.strftime('%B %Y')}"
                                    check_in_raw_from_history = target_date.strftime("%Y-%m-%d")
                                    # Check if number of nights was mentioned
                                    nights_match = re.search(r'for\s+(\d+)\s*nights?', recent_text_lower)
                                    if nights_match:
                                        num_nights = int(nights_match.group(1))
                                        check_out_date = target_date + timedelta(days=num_nights)
                                        day_str_out = str(check_out_date.day)
                                        suffix_out = "st" if day_str_out.endswith('1') and not day_str_out.endswith('11') else "nd" if day_str_out.endswith('2') and not day_str_out.endswith('12') else "rd" if day_str_out.endswith('3') and not day_str_out.endswith('13') else "th"
                                        check_out_from_history = f"{check_out_date.day}{suffix_out} {check_out_date.strftime('%B %Y')}"
                                except ValueError:
                                    pass
                        # Also check for "on [day]" pattern (without month - default to current month/year)
                        elif re.search(r'\bon\s+(\d{1,2})\b', recent_text_lower):
                            on_date_match = re.search(r'\bon\s+(\d{1,2})\b', recent_text_lower)
                            day_num = int(on_date_match.group(1))
                            today = datetime.now()
                            try:
                                target_date = datetime(today.year, today.month, day_num)
                                if target_date < today:
                                    if today.month == 12:
                                        target_date = datetime(today.year + 1, 1, day_num)
                                    else:
                                        target_date = datetime(today.year, today.month + 1, day_num)
                                day_str = str(day_num)
                                suffix = "st" if day_str.endswith('1') and not day_str.endswith('11') else "nd" if day_str.endswith('2') and not day_str.endswith('12') else "rd" if day_str.endswith('3') and not day_str.endswith('13') else "th"
                                check_in_from_history = f"{day_num}{suffix} {target_date.strftime('%B %Y')}"
                                check_in_raw_from_history = target_date.strftime("%Y-%m-%d")
                                # Check if number of nights was mentioned
                                nights_match = re.search(r'for\s+(\d+)\s*nights?', recent_text_lower)
                                if nights_match:
                                    num_nights = int(nights_match.group(1))
                                    check_out_date = target_date + timedelta(days=num_nights)
                                    day_str_out = str(check_out_date.day)
                                    suffix_out = "st" if day_str_out.endswith('1') and not day_str_out.endswith('11') else "nd" if day_str_out.endswith('2') and not day_str_out.endswith('12') else "rd" if day_str_out.endswith('3') and not day_str_out.endswith('13') else "th"
                                    check_out_from_history = f"{check_out_date.day}{suffix_out} {check_out_date.strftime('%B %Y')}"
                            except ValueError:
                                pass
                        # Also check for "next sunday" or similar dates (only if "on [day]" wasn't found)
                        elif "next sunday" in recent_text_lower or "this next sunday" in recent_text_lower or "coming sunday" in recent_text_lower:
                            today = datetime.now()
                            days_ahead = 6 - today.weekday()
                            if days_ahead <= 0:
                                days_ahead += 7
                            next_sunday = today + timedelta(days=days_ahead)
                            day_str = next_sunday.strftime("%d").lstrip("0")
                            suffix = "st" if day_str.endswith('1') and not day_str.endswith('11') else "nd" if day_str.endswith('2') and not day_str.endswith('12') else "rd" if day_str.endswith('3') and not day_str.endswith('13') else "th"
                            check_in_from_history = f"{day_str}{suffix} {next_sunday.strftime('%B %Y')}"
                            check_in_raw_from_history = next_sunday.strftime("%Y-%m-%d")
                            # Check if number of nights was mentioned
                            nights_match = re.search(r'for\s+(\d+)\s*nights?', recent_text.lower())
                            if nights_match:
                                num_nights = int(nights_match.group(1))
                                check_out_date = next_sunday + timedelta(days=num_nights)
                            day_str_out = check_out_date.strftime("%d").lstrip("0")
                            suffix_out = "st" if day_str_out.endswith('1') and not day_str_out.endswith('11') else "nd" if day_str_out.endswith('2') and not day_str_out.endswith('12') else "rd" if day_str_out.endswith('3') and not day_str_out.endswith('13') else "th"
                            check_out_from_history = f"{day_str_out}{suffix_out} {check_out_date.strftime('%B %Y')}"
                
                # Use dates from history if current message doesn't have them, but ONLY if checkout was explicitly mentioned
                # IMPORTANT: Only use dates from history if they were mentioned in the CURRENT conversation context
                # Since we're only checking messages from the most recent booking request, these dates should be current
                if not extracted_dates.get('check_in') and check_in_from_history:
                    extracted_dates['check_in'] = check_in_from_history
                    if check_in_raw_from_history:
                        extracted_dates['check_in_raw'] = check_in_raw_from_history
                # Only use checkout from history if it was explicitly mentioned (e.g., "for 2 nights")
                # IMPORTANT: Only copy checkout if nights were explicitly mentioned in the CURRENT conversation
                # This prevents using stale checkout dates from previous conversations
                if not extracted_dates.get('check_out') and check_out_from_history:
                    # Double-check that nights were actually mentioned in recent messages
                    nights_explicitly_mentioned = False
                    if session:
                        # Check the last 10 messages for explicit nights mention
                        recent_text_check = " ".join([msg.content for msg in session.history[-10:]])
                        nights_explicitly_mentioned = bool(re.search(r'for\s+(\d+)\s*nights?', recent_text_check.lower()))
                    # Only use checkout from history if nights were explicitly mentioned
                    if not nights_explicitly_mentioned:
                        # Clear check_out_from_history if nights weren't mentioned
                        check_out_from_history = None
                    else:
                        extracted_dates['check_out'] = check_out_from_history
                
                # Check if we have check-in but not check-out - need to ask for checkout date or nights
                # IMPORTANT: Only consider checkout as available if it's in extracted_dates (not just check_out_from_history)
                # This ensures we don't use stale checkout dates from previous conversations
                has_check_in = bool(extracted_dates.get('check_in') or extracted_dates.get('check_in_raw') or check_in_from_history)
                has_check_out = bool(extracted_dates.get('check_out') or extracted_dates.get('check_out_raw'))
                
                if has_check_in and not has_check_out:
                    # We have check-in date but not check-out - ask for checkout date or number of nights
                    check_in_display = extracted_dates.get('check_in') or check_in_from_history or ''
                    instruction = f"\n\n⚠️ ACTION REQUIRED: Customer selected a room! Extract room type from their message (e.g., 'one villa' = Two Bed Room Villa, 'villa' = Two Bed Room Villa, 'one family suite' = Family Suite). Check-in date is {check_in_display}. IMPORTANT: Customer has NOT specified checkout date or number of nights yet. You MUST ask them clearly: 'How many nights would you like to stay, or what is your checkout date?' DO NOT create booking summary yet - ask for checkout information first! Format your response clearly and professionally."
                elif has_check_in and has_check_out:
                    # We have both dates - show booking summary
                    check_in_display = extracted_dates.get('check_in', '') or check_in_from_history or ''
                    check_out_display = extracted_dates.get('check_out', '') or check_out_from_history or ''
                    # Validate that check-out is after check-in, and calculate if missing
                    if check_in_display and check_out_display:
                        try:
                            # Try to get raw dates for comparison first
                            check_in_raw_val = extracted_dates.get('check_in_raw') or check_in_raw_from_history
                            check_out_raw_val = extracted_dates.get('check_out_raw')
                            
                            # Parse dates (raw format preferred, fallback to display format)
                            check_in_dt = None
                            check_out_dt = None
                            
                            if check_in_raw_val:
                                check_in_dt = datetime.strptime(check_in_raw_val, "%Y-%m-%d")
                            else:
                                # Try to parse display format like "25th January 2026"
                                import re as re_mod
                                date_match = re_mod.search(r'(\d{1,2})(?:st|nd|rd|th)?\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})', check_in_display)
                                if date_match:
                                    day = int(date_match.group(1))
                                    month_name = date_match.group(2)
                                    year = int(date_match.group(3))
                                    months_map = {
                                        'January': 1, 'February': 2, 'March': 3, 'April': 4,
                                        'May': 5, 'June': 6, 'July': 7, 'August': 8,
                                        'September': 9, 'October': 10, 'November': 11, 'December': 12
                                    }
                                    check_in_dt = datetime(year, months_map[month_name], day)
                            
                            if check_out_raw_val:
                                check_out_dt = datetime.strptime(check_out_raw_val, "%Y-%m-%d")
                            else:
                                # Try to parse display format like "19th January 2026"
                                date_match = re.search(r'(\d{1,2})(?:st|nd|rd|th)?\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})', check_out_display)
                                if date_match:
                                    day = int(date_match.group(1))
                                    month_name = date_match.group(2)
                                    year = int(date_match.group(3))
                                    months_map = {
                                        'January': 1, 'February': 2, 'March': 3, 'April': 4,
                                        'May': 5, 'June': 6, 'July': 7, 'August': 8,
                                        'September': 9, 'October': 10, 'November': 11, 'December': 12
                                    }
                                    check_out_dt = datetime(year, months_map[month_name], day)
                            
                            # If we have both dates, validate
                            if check_in_dt and check_out_dt:
                                # If check-out is before or equal to check-in, recalculate to 1 night after check-in
                                if check_out_dt <= check_in_dt:
                                    check_out_dt = check_in_dt + timedelta(days=1)
                                    day_str_out = str(check_out_dt.day)
                                    suffix_out = "st" if day_str_out.endswith('1') and not day_str_out.endswith('11') else "nd" if day_str_out.endswith('2') and not day_str_out.endswith('12') else "rd" if day_str_out.endswith('3') and not day_str_out.endswith('13') else "th"
                                    check_out_display = f"{check_out_dt.day}{suffix_out} {check_out_dt.strftime('%B %Y')}"
                                    check_out_raw_val = check_out_dt.strftime("%Y-%m-%d")
                                    extracted_dates['check_out'] = check_out_display
                                    extracted_dates['check_out_raw'] = check_out_raw_val
                                    extracted_dates['nights'] = 1
                            elif check_in_dt and not check_out_dt:
                                # Check-in exists but checkout doesn't - DO NOT default to 1 night
                                # Instead, ask user for number of nights
                                has_check_out = False
                                check_out_display = ''
                        except Exception as e:
                            # If validation fails, default to 1 night if we have check-in
                            if check_in_display:
                                try:
                                    check_in_raw_val = extracted_dates.get('check_in_raw') or check_in_raw_from_history
                                    # DO NOT default to 1 night - if checkout is missing, ask user
                                    # This should not happen if has_check_out is True, but if it does, ask for nights
                                    if check_in_raw_val and not check_out_raw_val:
                                        has_check_out = False
                                        check_out_display = ''
                                except:
                                    pass
                    # Calculate nights from dates - DO NOT default to 1
                    check_in_raw = extracted_dates.get('check_in_raw') or check_in_raw_from_history or ''
                    check_out_raw = extracted_dates.get('check_out_raw') or ''
                    nights = None
                    if check_in_raw and check_out_raw:
                        try:
                            check_in_dt = datetime.strptime(check_in_raw, "%Y-%m-%d")
                            check_out_dt = datetime.strptime(check_out_raw, "%Y-%m-%d")
                            nights = (check_out_dt - check_in_dt).days
                        except:
                            nights = extracted_dates.get('nights')
                    else:
                        nights = extracted_dates.get('nights')
                    
                    nights_display = f"{nights} nights" if nights else "TBD"
                    dates_info = f"Use these EXACT dates: Check-in: {check_in_display}, Check-out: {check_out_display}, Nights: {nights_display}. "
                    # Store in booking_info for later use
                    booking_info['check_in'] = check_in_display
                    booking_info['check_out'] = check_out_display
                    booking_info['check_in_raw'] = check_in_raw
                    booking_info['check_out_raw'] = check_out_raw
                    booking_info['nights'] = nights
                    
                    instruction = f"\n\n⚠️ CRITICAL ACTION REQUIRED: Customer selected a room! Extract room type from their message (e.g., 'one triple room' = Triple Room, 'single room' = Single Room). {dates_info}Calculate total price (price per night × number of nights × number of rooms).\n\nCRITICAL FORMATTING REQUIREMENTS:\n1. You MUST respond with the COMPLETE booking summary - DO NOT stop mid-sentence\n2. Use this EXACT format with proper line breaks:\n\nGreat! Here's your booking summary:\n\nRoom: [Room Type]\nCheck-in: [Date]\nCheck-out: [Date]\nTotal Price: Nu.[Amount]\n\nWould you like to confirm this booking? Just reply 'yes' or 'confirm'! 😊\n\n3. Replace [Room Type] with actual room name\n4. Replace [Date] with actual dates from conversation (use the dates provided above)\n5. Replace [Amount] with calculated total price\n6. ALWAYS include the confirmation question at the end\n7. DO NOT use CreateBooking tool yet - just show the summary and wait!\n\nIMPORTANT: Your response MUST be complete. Finish every sentence. Do not truncate!"
                else:
                    # No dates extracted - extract from conversation
                    dates_info = ""
                    if session:
                        recent_msgs = " ".join([msg.content for msg in session.history[-5:]])
                        dates_info = f"Extract dates from conversation: {recent_msgs}. For 'next Sunday', calculate next Sunday's date (today is {datetime.now().strftime('%A, %B %d, %Y')}). "
                    
                    instruction = f"\n\n⚠️ ACTION REQUIRED: Customer selected a room! Extract room type from their message. {dates_info}If dates are not clear, ask customer for check-in and checkout dates or number of nights. DO NOT create booking summary until you have both check-in and checkout dates!"
            elif is_all_rooms_request:
                # Customer wants to book all available rooms - explain the 3-room limit
                instruction = f"\n\n⚠️ ACTION REQUIRED: Customer said 'all the available rooms' but we have a limit of maximum 3 rooms per booking to prevent misuse. Politely explain: 'I understand you'd like to book multiple rooms. However, we have a limit of 3 rooms per booking. Could you please specify which room(s) you'd like to book? You can select up to 3 different room types. For example: \"one double room and one twin room\" or just \"one villa\".' DO NOT create bookings yet - wait for them to specify which rooms they want (up to 3)."
            elif has_booking_dates and any(word in msg_lower for word in ["book", "booking"]) and not is_availability_check:
                # Customer explicitly wants to BOOK (not just check availability) - but still need room selection first
                # Only show summary if they've already seen rooms, otherwise show rooms first
                if last_hotel_shown:
                    instruction = f"\n\n⚠️ ACTION REQUIRED: Customer wants to book! Extract dates from conversation. Show booking summary with dates and ask which room they want. DO NOT use CreateBooking yet!"
                else:
                    instruction = f"\n\n⚠️ ACTION REQUIRED: Customer wants to book rooms! Use UniversalSearch to show available rooms first. Extract dates from conversation (e.g., '21st January' = 2026-01-21). Show available rooms and ask which room they'd like. DO NOT show booking summary yet!"
            
            input_text = f"Customer: {self._current_customer_name} | Phone: {self._current_customer_phone} | Current message: {message}{conversation_history}{instruction}"
            context = {"input": input_text}
            
            # Get response from agent
            with get_openai_callback() as cb:
                try:
                    response = self.agent.invoke(context)
                    output = response.get("output", "I apologize, I couldn't process that.")
                except Exception as parse_error:
                    # Handle parsing errors gracefully
                    error_str = str(parse_error)
                    output = None
                    
                    # Try to extract the actual response from the error
                    # Pattern 1: Look for "Could not parse LLM output: `...`"
                    # Try multiple patterns to catch different error formats
                    parse_output_match = re.search(r'Could not parse LLM output:\s*[`\'"](.+?)[`\'"]', error_str, re.DOTALL)
                    if not parse_output_match:
                        # Try without quotes
                        parse_output_match = re.search(r'Could not parse LLM output:\s*(.+?)(?:\n|For troubleshooting)', error_str, re.DOTALL)
                    if parse_output_match:
                        output = parse_output_match.group(1).strip()
                        # Remove any trailing backticks, quotes, or whitespace
                        output = output.rstrip('`\'"').strip()
                        print(f"✅ Parsing error handled - extracted output: {output[:80]}...")
                    
                    # Pattern 2: Look for "Final Answer: ..."
                    if not output:
                        final_answer_match = re.search(r'Final Answer:\s*(.+?)(?:\n|$)', error_str, re.DOTALL)
                        if final_answer_match:
                            output = final_answer_match.group(1).strip()
                            print(f"⚠️ Parsing error handled - extracted final answer")
                    
                    # Pattern 3: Look for "both a final answer and a parse-able action"
                    if not output and "both a final answer and a parse-able action" in error_str:
                        final_answer_match = re.search(r'Final Answer:\s*(.+?)(?:\n|$)', error_str, re.DOTALL)
                        if final_answer_match:
                            output = final_answer_match.group(1).strip()
                            print(f"⚠️ Parsing error handled - extracted final answer from action+answer error")
                        else:
                            # Try to extract action result
                            action_match = re.search(r'Action:\s*(\w+)', error_str)
                            if action_match:
                                action = action_match.group(1)
                                # If it's CreateBooking, the tool was likely called
                                if "CreateBooking" in action:
                                    output = "✅ Your request has been processed! Our team will contact you soon for confirmation."
                    
                    # If still no output, use generic error message
                    if not output:
                        output = "I apologize, but I encountered an error. Please try again."
                    
                    print(f"⚠️ Agent parsing error: {parse_error}")
                    print(f"📝 Extracted output: {output[:100]}...")
                
                print(f"📊 Tokens: {cb.total_tokens} (${cb.total_cost:.4f})")
                print(f"⏱️  Thinking time: {time.time() - start_time:.2f}s")
            
            # Check if response is truncated (common patterns: "Great! Here" without completion, ends mid-sentence)
            output_lower = output.lower()
            if (output_lower.startswith("great! here") and 
                not any(keyword in output_lower for keyword in ["booking summary", "room:", "check-in:", "total price", "confirm this booking"]) and
                len(output) < 100):
                # Response was truncated - this is likely a booking summary that got cut off
                # Try to complete it or regenerate
                print(f"⚠️ Detected truncated booking summary response, attempting to complete...")
                
                # Extract room type from conversation if available
                room_type = "selected room"
                if "single" in msg_lower:
                    room_type = "Single Room"
                elif "double" in msg_lower:
                    room_type = "Double Room"
                elif "triple" in msg_lower:
                    room_type = "Triple Room"
                elif "quad" in msg_lower or "family" in msg_lower:
                    room_type = "Family Suite" if "family" in msg_lower else "Quad Room"
                
                # Extract dates from conversation - USE extracted_dates and booking_info FIRST
                check_in_date = extracted_dates.get('check_in') or booking_info.get('check_in', '')
                check_out_date = extracted_dates.get('check_out') or booking_info.get('check_out', '')
                
                # If dates not found in extracted_dates/booking_info, extract from conversation
                if not check_in_date and session:
                    conv_text = " ".join([msg.content for msg in session.history[-10:]])
                    conv_text_lower = conv_text.lower()
                    
                    # PRIORITY 1: Check for "on [day] [month]" pattern (e.g., "on 25 january")
                    on_date_with_month = re.search(r'\bon\s+(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december)', conv_text_lower)
                    if on_date_with_month:
                        day_num = int(on_date_with_month.group(1))
                        month_name = on_date_with_month.group(2).lower()
                        months = {
                            'january': 1, 'february': 2, 'march': 3, 'april': 4,
                            'may': 5, 'june': 6, 'july': 7, 'august': 8,
                            'september': 9, 'october': 10, 'november': 11, 'december': 12
                        }
                        try:
                            current_year = datetime.now().year
                            target_date = datetime(current_year, months[month_name], day_num)
                            if target_date < datetime.now():
                                target_date = datetime(current_year + 1, months[month_name], day_num)
                            day_str = str(day_num)
                            suffix = "st" if day_str.endswith('1') and not day_str.endswith('11') else "nd" if day_str.endswith('2') and not day_str.endswith('12') else "rd" if day_str.endswith('3') and not day_str.endswith('13') else "th"
                            check_in_date = f"{day_num}{suffix} {target_date.strftime('%B %Y')}"
                            # Default to 1 night if checkout not specified
                            if not check_out_date:
                                check_out_date_dt = target_date + timedelta(days=1)
                                day_str_out = str(check_out_date_dt.day)
                                suffix_out = "st" if day_str_out.endswith('1') and not day_str_out.endswith('11') else "nd" if day_str_out.endswith('2') and not day_str_out.endswith('12') else "rd" if day_str_out.endswith('3') and not day_str_out.endswith('13') else "th"
                                check_out_date = f"{check_out_date_dt.day}{suffix_out} {check_out_date_dt.strftime('%B %Y')}"
                        except (ValueError, KeyError):
                            pass
                    # PRIORITY 2: Extract dates like "25 january" (without "on")
                    # PRIORITY 2: Extract dates like "25 january" (without "on")
                    elif any(month in conv_text_lower for month in ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]):
                        # Extract dates like "21st January" or "25 january"
                        date_match = re.search(r'(\d{1,2})(?:st|nd|rd|th)?\s+(january|february|march|april|may|june|july|august|september|october|november|december)', conv_text_lower)
                        if date_match:
                            day = date_match.group(1)
                            month = date_match.group(2).capitalize()
                            try:
                                check_in_dt = datetime.strptime(f"{day} {month} 2026", "%d %B %Y")
                                day_str = str(int(day))
                                suffix = "st" if day_str.endswith('1') and not day_str.endswith('11') else "nd" if day_str.endswith('2') and not day_str.endswith('12') else "rd" if day_str.endswith('3') and not day_str.endswith('13') else "th"
                                check_in_date = f"{day}{suffix} {month} 2026"
                                # Default to next day if checkout not specified
                                if not check_out_date:
                                    check_out_dt = check_in_dt + timedelta(days=1)
                                    day_str_out = str(check_out_dt.day)
                                    suffix_out = "st" if day_str_out.endswith('1') and not day_str_out.endswith('11') else "nd" if day_str_out.endswith('2') and not day_str_out.endswith('12') else "rd" if day_str_out.endswith('3') and not day_str_out.endswith('13') else "th"
                                    check_out_date = f"{check_out_dt.day}{suffix_out} {check_out_dt.strftime('%B %Y')}"
                            except:
                                pass
                    # PRIORITY 3: Extract "next Sunday" or similar relative dates (only as fallback)
                    elif "next sunday" in conv_text_lower or "this next sunday" in conv_text_lower:
                        today = datetime.now()
                        # Calculate next Sunday (0 = Monday, 6 = Sunday)
                        days_ahead = 6 - today.weekday()  # Days until next Sunday
                        if days_ahead <= 0:  # If today is Sunday or past, get next week's Sunday
                            days_ahead += 7
                        next_sunday = today + timedelta(days=days_ahead)
                        # Format with proper suffix and year
                        day_str = next_sunday.strftime("%d").lstrip("0")
                        if day_str.endswith('1') and not day_str.endswith('11'):
                            suffix = "st"
                        elif day_str.endswith('2') and not day_str.endswith('12'):
                            suffix = "nd"
                        elif day_str.endswith('3') and not day_str.endswith('13'):
                            suffix = "rd"
                        else:
                            suffix = "th"
                        check_in_date = f"{day_str}{suffix} {next_sunday.strftime('%B %Y')}"
                        # Check for number of nights
                        nights_match = re.search(r'for\s+(\d+)\s*nights?', conv_text_lower)
                        num_nights = int(nights_match.group(1)) if nights_match else 1
                        next_checkout = next_sunday + timedelta(days=num_nights)
                        day_str_out = next_checkout.strftime("%d").lstrip("0")
                        if day_str_out.endswith('1') and not day_str_out.endswith('11'):
                            suffix_out = "st"
                        elif day_str_out.endswith('2') and not day_str_out.endswith('12'):
                            suffix_out = "nd"
                        elif day_str_out.endswith('3') and not day_str_out.endswith('13'):
                            suffix_out = "rd"
                        else:
                            suffix_out = "th"
                        check_out_date = f"{day_str_out}{suffix_out} {next_checkout.strftime('%B %Y')}"
                
                # Fallback to defaults only if still no dates found
                if not check_in_date:
                    check_in_date = "next Sunday"
                    check_out_date = "next Monday"  # Default to 1 night
                
                # Try to get price from conversation or use default
                price = "800"  # Default
                if "single" in room_type.lower():
                    price = "800"
                elif "double" in room_type.lower():
                    price = "1,200"
                elif "triple" in room_type.lower():
                    price = "1,500"
                elif "quad" in room_type.lower():
                    price = "1,800"
                elif "family" in room_type.lower():
                    price = "2,500"
                
                # Complete the truncated response
                output = f"""Great! Here's your booking summary:

Room: {room_type}
Check-in: {check_in_date}
Check-out: {check_out_date}
Total Price: Nu.{price}

Would you like to confirm this booking? Just reply 'yes' or 'confirm'! 😊"""
                
                print(f"✅ Completed truncated response with booking summary")
            
            # Add to session
            session_manager.add_message(customer_phone, "assistant", output)
            
            # Update session context if booking created
            if any(keyword in output.lower() for keyword in ['booking created', 'booking id', 'booking confirmed']):
                session_manager.update_context(
                    phone_number=customer_phone,
                    last_intent="booking_created",
                    pending_booking=True
                )
            
            total_time = time.time() - start_time
            print(f"✅ Total processing: {total_time:.2f}s")
            print(f"🤖 Response: {output[:100]}...")
            print(f"{'='*50}\n")
            
            return output
            
        except Exception as e:
            print(f"❌ Processing error: {e}")
            traceback.print_exc()
            
            error_msg = "I apologize, but I encountered an error. Please try again."
            session_manager.add_message(customer_phone, "assistant", error_msg)
            
            return error_msg

# Lazy initialization - only create agent when first accessed
_agent_instance = None
_agent_lock = None  # Will be initialized on first use

def _get_lock():
    """Get or create the lock (lazy initialization to avoid import issues)."""
    global _agent_lock
    if _agent_lock is None:
        import threading
        _agent_lock = threading.Lock()
    return _agent_lock

def get_agent():
    """Get or create the agent instance (lazy initialization)."""
    global _agent_instance
    if _agent_instance is None:
        lock = _get_lock()
        with lock:
            # Double-check pattern
            if _agent_instance is None:
                try:
                    print("🌍 Creating Universal Agent (first use)...")
                    _agent_instance = UniversalAgent()
                    print("🎉 Universal Agent is ready for ANY data!")
                except Exception as e:
                    print(f"❌ Failed to initialize agent: {e}")
                    traceback.print_exc()
                    # Fallback
                    class FallbackAgent:
                        def process_message(self, message, phone, name=""):
                            return "Hello! I'm your assistant. I can help you search and order anything from our inventory."
                    _agent_instance = FallbackAgent()
    return _agent_instance

# For backward compatibility - create a simple proxy object
class AgentProxy:
    """Proxy that lazily creates the agent on first method call."""
    def __getattr__(self, name):
        # Only access agent when attribute is actually requested
        agent = get_agent()
        return getattr(agent, name)

# Create proxy instances - these are just lightweight objects
whatsapp_agent = AgentProxy()
universal_agent = AgentProxy()