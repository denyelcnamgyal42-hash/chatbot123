from flask import Flask, request, jsonify
import requests 
import config 
import logging 
import re 
import time  # ADDED THIS IMPORT
from datetime import datetime
from threading import Thread 
from queue import Queue 
from functools import wraps 
from langchain_agent import whatsapp_agent
from flask_limiter import Limiter 
from flask_limiter.util import get_remote_address

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

limiter = Limiter(
    app=app, 
    key_func=get_remote_address,
    default_limits=["2000 per hour", "80 per second"],
    storage_uri="memory://",
    strategy="fixed-window",
    headers_enabled=True
)
message_queue = Queue(maxsize=1000)

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
                return None  # Don't retry on auth errors
            elif response.status_code == 429:
                retry_after = int(response.headers.get('Retry-After', 5))
                logger.warning(f"Rate limited. Retrying after {retry_after} seconds")
                time.sleep(retry_after)
                continue
            else:
                logger.error(f"❌ HTTP Error {response.status_code}: {response.text}")
                # Log full error for debugging
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
    while True:
        try:
            data = message_queue.get()
            if data is None:
                break
                
            phone, text, name, message_id = data
            
            logger.info(f"Processing async message from {phone}")
            
            try:
                # Process with agent
                response_text = whatsapp_agent.process_message(text, phone, name)
                
                # Send response
                send_whatsapp_message(phone, response_text, message_id)
                
            except Exception as e:
                logger.error(f"❌ Error in async processing: {e}", exc_info=True)
                error_msg = "I apologize, but I encountered an error. Please try again."
                send_whatsapp_message(phone, error_msg, message_id)
                
            message_queue.task_done()
            
        except Exception as e:
            logger.error(f"❌ Error in async worker: {e}", exc_info=True)

# Start background worker
worker_thread = Thread(target=process_message_async, daemon=True)
worker_thread.start()

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

def cleanup():
    """Cleanup function for graceful shutdown."""
    logger.info("Shutting down...")
    message_queue.put(None)
    worker_thread.join(timeout=5)

import atexit
atexit.register(cleanup)

if __name__ == "__main__":
    logger.info(f"🚀 Starting WhatsApp webhook server on port {config.PORT}")
    app.run(
        host="0.0.0.0",
        port=config.PORT,
        debug=config.DEBUG,
        threaded=True
    )