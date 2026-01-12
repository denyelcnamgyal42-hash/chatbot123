"""Unified Flask application for WhatsApp webhook and Employee Dashboard."""
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import requests 
import config 
import logging 
import re 
import time
from datetime import datetime
from threading import Thread 
from queue import Queue 
from functools import wraps 
from flask_limiter import Limiter 
from flask_limiter.util import get_remote_address
from google_sheets import sheets_manager as enhanced_sheets
import google_sheets
import json
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create unified Flask app
app = Flask(__name__)
CORS(app)

# Rate limiting
limiter = Limiter(
    app=app, 
    key_func=get_remote_address,
    default_limits=["2000 per hour", "80 per second"],
    storage_uri="memory://",
    strategy="fixed-window",
    headers_enabled=True
)

# Message queue for async processing
message_queue = Queue(maxsize=1000)

# ==================== WhatsApp Webhook Functions ====================

def validate_phone_number(phone_number: str) -> bool:
    """Validate WhatsApp phone number format."""
    cleaned = re.sub(r'\D', '', phone_number)
    return len(cleaned) >= 10 and len(cleaned) <= 15

def sanitize_message(text: str) -> str:
    """Sanitize message text."""
    sanitized = re.sub(r'[\x00-\x1F\x7F]', '', text)
    return sanitized[:4000]

def send_whatsapp_message(phone_number: str, message: str, message_id: str = None):
    """Send a WhatsApp message using Meta API."""
    if not validate_phone_number(phone_number):
        logger.error(f"Invalid phone number: {phone_number}")
        return None
    
    message = sanitize_message(message)
    
    url = config.WHATSAPP_API_URL
    
    headers = {
        "Authorization": f"Bearer {config.WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": phone_number,
        "type": "text",
        "text": {"body": message}
    }
    
    if message_id:
        payload["context"] = {"message_id": message_id}
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.post(
                url, 
                json=payload, 
                headers=headers,
                timeout=10
            )
            
            if response.status_code == 200:
                logger.info(f"✅ Message sent to {phone_number}")
                return response.json()
            elif response.status_code == 401:
                logger.error(f"❌ Authentication failed. Check WHATSAPP_ACCESS_TOKEN")
                logger.error(f"Response: {response.text}")
                return None
            elif response.status_code == 429:
                retry_after = int(response.headers.get('Retry-After', 5))
                logger.warning(f"Rate limited. Retrying after {retry_after} seconds")
                time.sleep(retry_after)
                continue
            else:
                logger.error(f"❌ HTTP Error {response.status_code}: {response.text}")
                try:
                    error_json = response.json()
                    logger.error(f"Error details: {error_json}")
                except:
                    pass
                
        except requests.exceptions.Timeout:
            logger.warning(f"Timeout sending message (attempt {attempt + 1})")
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error: {e}")
        
        time.sleep(2 ** attempt)
    
    logger.error(f"Failed to send message after {max_retries} attempts")
    return None

def process_message_async():
    """Background worker to process messages."""
    logger.info("🚀 Message processing worker thread started")
    while True:
        try:
            # Use timeout to allow periodic health checks
            try:
                data = message_queue.get(timeout=1)
            except:
                continue  # Timeout is normal, just check again
            
            if data is None:
                logger.info("🛑 Worker thread received shutdown signal")
                break
                
            phone, text, name, message_id = data
            
            logger.info(f"📨 Processing async message from {phone}: {text[:50]}")
            
            try:
                # Lazy import to avoid blocking app startup
                logger.info("🔄 Importing whatsapp_agent...")
                from langchain_agent import whatsapp_agent
                logger.info("✅ whatsapp_agent imported successfully")
                
                # Process with agent
                logger.info(f"🤖 Processing message with agent: {text[:50]}...")
                response_text = whatsapp_agent.process_message(text, phone, name)
                logger.info(f"✅ Agent response generated: {response_text[:50]}...")
                
                # Send response
                logger.info(f"📤 Sending response to {phone}")
                send_whatsapp_message(phone, response_text, message_id)
                logger.info(f"✅ Response sent successfully to {phone}")
                
            except Exception as e:
                logger.error(f"❌ Error in async processing: {e}", exc_info=True)
                import traceback
                logger.error(f"Full traceback: {traceback.format_exc()}")
                error_msg = "I apologize, but I encountered an error. Please try again."
                try:
                    send_whatsapp_message(phone, error_msg, message_id)
                except Exception as send_error:
                    logger.error(f"❌ Failed to send error message: {send_error}")
            
            message_queue.task_done()
            
        except Exception as e:
            logger.error(f"❌ Error in async worker: {e}", exc_info=True)

# Start background worker
worker_thread = Thread(target=process_message_async, daemon=True)
worker_thread.start()

# ==================== Dashboard Authentication ====================

def verify_auth():
    """Verify Bearer token authentication for dashboard routes."""
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return False
    
    token = auth_header.split(' ')[1]
    return token == config.DASHBOARD_AUTH_TOKEN

# ==================== WhatsApp Webhook Routes ====================

@app.route("/webhook", methods=["GET"])
def verify_webhook():
    """Verify webhook for Meta API."""
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    
    logger.info(f"🔍 Webhook verification attempt: mode={mode}")
    
    if mode == "subscribe" and token == config.WHATSAPP_VERIFY_TOKEN:
        logger.info("✅ Webhook verified successfully")
        return challenge, 200
    else:
        logger.warning(f"❌ Webhook verification failed. Expected: {config.WHATSAPP_VERIFY_TOKEN}, Got: {token}")
        return "Forbidden", 403

@app.route("/webhook", methods=["POST"])
@limiter.limit("80 per second")
def handle_webhook():
    """Handle incoming WhatsApp messages."""
    logger.info("📥 Received POST to /webhook")
    
    try:
        # Parse the JSON data
        data = request.get_json()
        logger.info(f"Raw webhook data: {data}")
        
        if not data:
            logger.warning("Empty request received")
            return jsonify({"status": "ignored"}), 200
        
        if data.get("object") != "whatsapp_business_account":
            logger.warning(f"Invalid object type: {data.get('object')}")
            return jsonify({"status": "ignored"}), 200
        
        entries = data.get("entry", [])
        
        for entry in entries:
            changes = entry.get("changes", [])
            
            for change in changes:
                value = change.get("value", {})
                
                # Handle messages
                if "messages" in value:
                    messages = value["messages"]
                    
                    for message in messages:
                        if message.get("type") == "text":
                            from_number = message.get("from", "")
                            message_text = message.get("text", {}).get("body", "")
                            message_id = message.get("id", "")
                            
                            # Get customer name
                            contacts = value.get("contacts", [{}])
                            customer_name = contacts[0].get("profile", {}).get("name", "Customer")
                            
                            logger.info(f"📩 Message from {from_number}: {message_text}")
                            
                            # Add to queue for async processing
                            try:
                                if not message_queue.full():
                                    message_queue.put((
                                        from_number,
                                        message_text,
                                        customer_name,
                                        message_id
                                    ))
                                    logger.info(f"📥 Queued message from {from_number}")
                                else:
                                    logger.error("Message queue is full!")
                                    send_whatsapp_message(from_number, "I'm busy. Please try again later.", message_id)
                            except Exception as e:
                                logger.error(f"Error queuing message: {e}")
        
        # Always return 200 immediately
        return jsonify({"status": "accepted"}), 200
    
    except Exception as e:
        logger.error(f"❌ Error handling webhook: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Internal server error"}), 500

@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint."""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "queue_size": message_queue.qsize()
    }), 200

@app.route("/send-test", methods=["POST"])
def send_test_message():
    """Endpoint to test sending messages (for debugging)."""
    try:
        data = request.get_json()
        phone = data.get("phone")
        message = data.get("message")
        
        if not phone or not message:
            return jsonify({"error": "Phone and message required"}), 400
        
        result = send_whatsapp_message(phone, message)
        
        if result:
            return jsonify({"status": "sent", "result": result}), 200
        else:
            return jsonify({"error": "Failed to send"}), 500
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ==================== Dashboard Routes ====================

@app.route('/')
def dashboard():
    """Serve dashboard HTML."""
    return render_template('dashboard.html')

@app.route('/api/notifications', methods=['GET'])
def get_notifications():
    """Get recent notifications for dashboard."""
    if not verify_auth():
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        notifications = enhanced_sheets.read_all_data('notifications')
        if not notifications:
            return jsonify({'notifications': [], 'count': 0})
        
        headers = notifications[0]
        recent_notifications = []
        
        for row in notifications[-20:]:  
            if not any(row):
                continue
            
            notif_dict = dict(zip(headers, row))
            recent_notifications.append(notif_dict)
        
        return jsonify({
            'notifications': recent_notifications[::-1],  
            'count': len(recent_notifications)
        })
        
    except Exception as e:
        logger.error(f"❌ Error fetching notifications: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/bookings/pending', methods=['GET'])
def get_pending_bookings():
    """Get all pending bookings from Pending Bookings sheet."""
    if not verify_auth():
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        pending_sheet = enhanced_sheets._get_or_create_pending_bookings_sheet()
        
        bookings = enhanced_sheets.read_all_data(pending_sheet)
        if not bookings:
            return jsonify({'bookings': [], 'count': 0})
        
        headers = ['Booking ID', 'Customer Name', 'Phone', 'Check-in', 'Check-out', 
                  'Room Type', 'Room Name', 'Room ID', 'Num Rooms', 'Guests', 
                  'Price', 'Status', 'Created At', 'Notes']
        
        pending_bookings = []
        current_month = None
        header_row_found = False
        header_indices = {}  
        
        for row_idx, row in enumerate(bookings):
            if not any(row):
                continue
            
            first_cell = str(row[0]).strip() if row and len(row) > 0 else ''
            row_str = ' '.join([str(cell).strip() for cell in row if cell]).lower()
            
            # Check if this is a month header (format: "January, 2026")
            if ',' in first_cell and any(month in first_cell for month in ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']):
                current_month = first_cell
                continue
            
            # Check if this is a header row (contains "Booking ID", "Customer Name", etc.)
            if any(h.lower() in row_str for h in ['booking id', 'customer name', 'phone']):
                header_row_found = True
                # Map headers to column indices
                for idx, cell in enumerate(row):
                    cell_lower = str(cell).strip().lower()
                    for header in headers:
                        if header.lower() in cell_lower or cell_lower in header.lower():
                            header_indices[header] = idx
                            break
                continue
            
            # Skip empty rows
            if not row_str or not any(row):
                continue
            
            # Only process rows after we've found the header row
            if not header_row_found:
                continue
            
            # This is a booking row - use header indices if available, otherwise use position
            booking_dict = {}
            for header_name in headers:
                if header_name in header_indices:
                    col_idx = header_indices[header_name]
                    if col_idx < len(row):
                        booking_dict[header_name] = str(row[col_idx]).strip() if row[col_idx] is not None else ''
                    else:
                        booking_dict[header_name] = ''
                else:
                    # Fallback to position-based mapping
                    header_idx = headers.index(header_name) if header_name in headers else -1
                    if header_idx >= 0 and header_idx < len(row):
                        booking_dict[header_name] = str(row[header_idx]).strip() if row[header_idx] is not None else ''
                    else:
                        booking_dict[header_name] = ''
            
            # Skip if this doesn't look like a booking row (no booking ID)
            if not booking_dict.get('Booking ID', '').strip():
                continue
            
            # Check status (case-insensitive) - default to pending if empty
            status = str(booking_dict.get('Status', booking_dict.get('status', ''))).strip().lower()
            if not status:
                status = 'pending'
            
            # Include bookings with pending status or empty status
            if status in ['pending', 'pending_payment', '']:
                # Normalize field names for dashboard template
                normalized_booking = {
                    'id': booking_dict.get('Booking ID', ''),
                    'booking_id': booking_dict.get('Booking ID', ''),
                    'customer_name': booking_dict.get('Customer Name', ''),
                    'phone': booking_dict.get('Phone', ''),
                    'check_in': booking_dict.get('Check-in', ''),
                    'check_out': booking_dict.get('Check-out', ''),
                    'room_type': booking_dict.get('Room Type', ''),
                    'room_name': booking_dict.get('Room Name', ''),
                    'room_id': booking_dict.get('Room ID', ''),
                    'num_rooms': booking_dict.get('Num Rooms', '1'),
                    'guests': booking_dict.get('Guests', ''),
                    'price': booking_dict.get('Price', '0'),
                    'status': status or 'pending',
                    'created_at': booking_dict.get('Created At', ''),
                    'notes': booking_dict.get('Notes', ''),
                    'row_index': row_idx + 1,
                    'month': current_month
                }
                pending_bookings.append(normalized_booking)
        
        # Sort bookings: newer months first, then by check-in date within month
        def get_sort_key(booking):
            try:
                check_in_str = booking.get('Check-in', booking.get('check-in', ''))
                if check_in_str:
                    check_in_date = datetime.strptime(check_in_str, "%Y-%m-%d")
                    return (-check_in_date.year, -check_in_date.month, check_in_date.date())
            except:
                pass
            return (0, 0, datetime.now().date())
        
        pending_bookings.sort(key=get_sort_key)
        
        return jsonify({
            'bookings': pending_bookings,
            'count': len(pending_bookings),
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        logger.error(f"❌ Error fetching pending bookings: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/bookings/<booking_id>/approve', methods=['POST'])
def approve_booking(booking_id):
    """Approve a booking (payment confirmed) and decrement room availability."""
    if not verify_auth():
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        # Handle both JSON and form data
        try:
            data = request.get_json() or {}
        except:
            data = request.form.to_dict() or {}
        employee_note = data.get('note', 'Payment confirmed via call')
        
        # Get pending bookings sheet
        pending_sheet = enhanced_sheets._get_or_create_pending_bookings_sheet()
        
        # Update booking status (this will move it to monthly sheet)
        success = enhanced_sheets.update_booking_status(
            pending_sheet,
            booking_id,
            'approved',
            employee_note
        )
        
        if success:
            logger.info(f"✅ Booking {booking_id} approved")
            return jsonify({
                'success': True,
                'message': f'Booking {booking_id} approved successfully',
                'booking_id': booking_id
            })
        else:
            return jsonify({
                'success': False,
                'message': f'Booking {booking_id} not found or already processed'
            }), 404
            
    except Exception as e:
        logger.error(f"❌ Error approving booking {booking_id}: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/bookings/<booking_id>/reject', methods=['POST'])
def reject_booking(booking_id):
    """Reject a booking."""
    if not verify_auth():
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        # Handle both JSON and form data
        try:
            data = request.get_json() or {}
        except:
            data = request.form.to_dict() or {}
        reason = data.get('reason', 'Rejected by employee')
        
        # Find bookings sheet
        all_sheets = enhanced_sheets.discover_sheets()
        bookings_sheet = None
        for sheet in all_sheets:
            if 'booking' in sheet.lower():
                bookings_sheet = sheet
                break
        
        if not bookings_sheet:
            bookings_sheet = config.BOOKINGS_SHEET
        
        success = enhanced_sheets.update_booking_status(
            bookings_sheet,
            booking_id,
            'rejected',
            reason
        )
        
        if success:
            logger.info(f"❌ Booking {booking_id} rejected: {reason}")
            return jsonify({
                'success': True,
                'message': f'Booking {booking_id} rejected',
                'booking_id': booking_id
            })
        else:
            return jsonify({
                'success': False,
                'message': f'Booking {booking_id} not found'
            }), 404
            
    except Exception as e:
        logger.error(f"❌ Error rejecting booking {booking_id}: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/reindex', methods=['POST'])
def reindex_vectorstore():
    """Force refresh of the vectorstore index to include new sheets."""
    if not verify_auth():
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        from dense_retrieval import get_dense_retrieval
        retriever = get_dense_retrieval()
        retriever.refresh_index(force=True)
        
        # Get stats
        stats = retriever.get_stats()
        
        return jsonify({
            'success': True,
            'message': 'Vectorstore reindexed successfully',
            'stats': stats,
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"❌ Error reindexing: {e}")
        return jsonify({'error': str(e)}), 500

# ==================== Cleanup ====================

def cleanup():
    """Cleanup function for graceful shutdown."""
    logger.info("Shutting down...")
    message_queue.put(None)
    worker_thread.join(timeout=5)

import atexit
atexit.register(cleanup)

# ==================== Background Tasks ====================

def start_background_tasks():
    """Start background tasks in a separate thread to avoid blocking app startup."""
    def _start_tasks():
        try:
            from background_tasks import get_task_manager
            task_manager = get_task_manager()
            task_manager.start()
            logger.info("✅ Background tasks started (auto checkout & vectorstore refresh)")
        except Exception as e:
            logger.warning(f"⚠️ Could not start background tasks: {e}")
    
    # Start in background thread so it doesn't block app startup
    task_thread = Thread(target=_start_tasks, daemon=True)
    task_thread.start()

# Start background tasks when module is imported (works with gunicorn)
start_background_tasks()

# ==================== Main Entry Point ====================

if __name__ == "__main__":
    # Use Render's PORT if available, otherwise use config
    port = int(os.getenv("PORT", config.PORT))
    
    logger.info(f"🚀 Starting unified BotPocketFlow server on port {port}")
    logger.info(f"WhatsApp Webhook: http://localhost:{port}/webhook")
    logger.info(f"Dashboard: http://localhost:{port}/")
    logger.info(f"Health check: http://localhost:{port}/health")
    
    # Start Flask app - this must bind to port immediately
    app.run(
        host="0.0.0.0",
        port=port,
        debug=config.DEBUG,
        threaded=True
    )
