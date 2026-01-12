from flask import Flask, request, jsonify, render_template
import config
from google_sheets import sheets_manager as enhanced_sheets
import google_sheets
from datetime import datetime, timedelta
import json
import logging


app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def verify_auth():
    """Verify Bearer token authentication."""
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return False
    
    token = auth_header.split(' ')[1]
    return token == config.DASHBOARD_AUTH_TOKEN

@app.route('/')
def dashboard():
    """Serve dashboard HTML."""
    return render_template('dashboard.html')

# REMOVED: Order endpoints - System is now hotel reservations only

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
        
        for row in notifications[-20:]:  # Last 20 notifications
            if not any(row):
                continue
            
            notif_dict = dict(zip(headers, row))
            recent_notifications.append(notif_dict)
        
        return jsonify({
            'notifications': recent_notifications[::-1],  # Reverse to show newest first
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
        # Get pending bookings sheet specifically
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
        header_indices = {}  # Map header names to column indices
        
        # Process bookings organized by month sections
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
            
            if not row_str or not any(row):
                continue
            
            if not header_row_found:
                continue
            
            booking_dict = {}
            for header_name in headers:
                if header_name in header_indices:
                    col_idx = header_indices[header_name]
                    if col_idx < len(row):
                        booking_dict[header_name] = str(row[col_idx]).strip() if row[col_idx] is not None else ''
                    else:
                        booking_dict[header_name] = ''
                else:

                    header_idx = headers.index(header_name) if header_name in headers else -1
                    if header_idx >= 0 and header_idx < len(row):
                        booking_dict[header_name] = str(row[header_idx]).strip() if row[header_idx] is not None else ''
                    else:
                        booking_dict[header_name] = ''
            
            if not booking_dict.get('Booking ID', '').strip():
                continue
            
            status = str(booking_dict.get('Status', booking_dict.get('status', ''))).strip().lower()
            if not status:
                status = 'pending'  
            
            if status in ['pending', 'pending_payment', '']:
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

if __name__ == '__main__':
    print(f"🚀 Starting Employee Dashboard on port {config.DASHBOARD_PORT}")
    app.run(port=config.DASHBOARD_PORT, debug=config.DEBUG)