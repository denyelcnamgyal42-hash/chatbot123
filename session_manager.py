"""Session management for conversation history and context."""
import json
import os
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict, field
import hashlib

@dataclass
class Message:
    """Represents a single message in conversation history."""
    role: str  # "user" or "assistant"
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    
    def to_dict(self):
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp
        }

@dataclass
class SessionContext:
    """Contextual information for the session."""
    pending_order: Optional[Dict] = None
    pending_booking: Optional[Dict] = None
    last_intent: Optional[str] = None  # "order", "booking", "inquiry", "support"
    current_product: Optional[str] = None
    current_hotel: Optional[str] = None
    cart: List[Dict] = field(default_factory=list)  # For multi-item purchases
    preferences: Dict[str, Any] = field(default_factory=dict)  # User preferences
    
    def to_dict(self):
        return {
            "pending_order": self.pending_order,
            "pending_booking": self.pending_booking,
            "last_intent": self.last_intent,
            "current_product": self.current_product,
            "current_hotel": self.current_hotel,
            "cart": self.cart,
            "preferences": self.preferences
        }

class Session:
    """Represents a user session."""
    def __init__(self, phone_number: str):
        self.phone_number = phone_number
        self.created_at = datetime.now()
        self.last_active = datetime.now()
        self.history: List[Message] = []
        self.context = SessionContext()
        self.session_id = hashlib.md5(f"{phone_number}{self.created_at.isoformat()}".encode()).hexdigest()[:8]
    
    def add_message(self, role: str, content: str):
        """Add a message to history."""
        self.history.append(Message(role=role, content=content))
        self.last_active = datetime.now()
        
        # Keep only last 10 messages to avoid excessive growth
        if len(self.history) > 10:
            self.history = self.history[-10:]
    
    def get_conversation_summary(self, max_messages: int = 5) -> str:
        """Get formatted conversation summary for agent context."""
        recent = self.history[-max_messages:] if self.history else []
        return "\n".join([f"{msg.role}: {msg.content}" for msg in recent])
    
    def update_context(self, **kwargs):
        """Update session context."""
        for key, value in kwargs.items():
            if hasattr(self.context, key):
                setattr(self.context, key, value)
        self.last_active = datetime.now()
    
    def clear_cart(self):
        """Clear the shopping cart."""
        self.context.cart = []
    
    def add_to_cart(self, product: Dict, quantity: int = 1):
        """Add product to cart."""
        # Check if product already in cart
        for item in self.context.cart:
            if item.get("product_name") == product.get("name"):
                item["quantity"] += quantity
                break
        else:
            self.context.cart.append({
                "product_name": product.get("name"),
                "quantity": quantity,
                "price": product.get("price"),
                "product_data": product
            })
    
    def to_dict(self) -> Dict:
        """Convert session to dictionary for serialization."""
        return {
            "phone_number": self.phone_number,
            "created_at": self.created_at.isoformat(),
            "last_active": self.last_active.isoformat(),
            "history": [msg.to_dict() for msg in self.history],
            "context": self.context.to_dict(),
            "session_id": self.session_id
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'Session':
        """Create session from dictionary."""
        session = cls(data["phone_number"])
        session.created_at = datetime.fromisoformat(data["created_at"])
        session.last_active = datetime.fromisoformat(data["last_active"])
        session.session_id = data.get("session_id", session.session_id)
        
        # Restore history
        session.history = [
            Message(role=msg["role"], content=msg["content"], timestamp=msg["timestamp"])
            for msg in data.get("history", [])
        ]
        
        # Restore context
        context_data = data.get("context", {})
        session.context = SessionContext(
            pending_order=context_data.get("pending_order"),
            pending_booking=context_data.get("pending_booking"),
            last_intent=context_data.get("last_intent"),
            current_product=context_data.get("current_product"),
            current_hotel=context_data.get("current_hotel"),
            cart=context_data.get("cart", []),
            preferences=context_data.get("preferences", {})
        )
        
        return session

class SessionManager:
    """Manages user sessions with persistence."""
    
    def __init__(self, session_file: str = "sessions.json", ttl_hours: int = 48):
        """
        Initialize session manager.
        
        Args:
            session_file: Path to session storage file
            ttl_hours: Session time-to-live in hours
        """
        self.session_file = session_file
        self.ttl_hours = ttl_hours
        self.sessions: Dict[str, Session] = {}
        self.lock = threading.RLock()  # Thread-safe operations
        
        # Load existing sessions
        self._load_sessions()
        
        # Start cleanup scheduler
        self._start_cleanup_scheduler()
    
    def _load_sessions(self):
        """Load sessions from storage file."""
        if os.path.exists(self.session_file):
            try:
                with open(self.session_file, 'r') as f:
                    data = json.load(f)
                
                with self.lock:
                    for phone, session_data in data.items():
                        try:
                            session = Session.from_dict(session_data)
                            # Check if session is still valid (not expired)
                            if self._is_session_valid(session):
                                self.sessions[phone] = session
                        except Exception as e:
                            print(f"Error loading session for {phone}: {e}")
                            continue
                
                print(f"Loaded {len(self.sessions)} valid sessions from storage")
            except Exception as e:
                print(f"Error loading session file: {e}. Starting with empty sessions.")
                self.sessions = {}
        else:
            self.sessions = {}
    
    def _save_sessions(self):
        """Save sessions to storage file."""
        with self.lock:
            data = {phone: session.to_dict() for phone, session in self.sessions.items()}
        
        try:
            # Write to temporary file first, then rename (atomic operation)
            temp_file = f"{self.session_file}.tmp"
            with open(temp_file, 'w') as f:
                json.dump(data, f, indent=2, default=str)
            
            os.replace(temp_file, self.session_file)
        except Exception as e:
            print(f"Error saving sessions: {e}")
    
    def _is_session_valid(self, session: Session) -> bool:
        """Check if session is still within TTL."""
        expiry_time = session.last_active + timedelta(hours=self.ttl_hours)
        return datetime.now() < expiry_time
    
    def _cleanup_expired_sessions(self):
        """Remove expired sessions."""
        with self.lock:
            expired_count = 0
            valid_sessions = {}
            
            for phone, session in self.sessions.items():
                if self._is_session_valid(session):
                    valid_sessions[phone] = session
                else:
                    expired_count += 1
            
            if expired_count > 0:
                self.sessions = valid_sessions
                self._save_sessions()
                print(f"Cleaned up {expired_count} expired sessions")
    
    def _start_cleanup_scheduler(self):
        """Start background thread for session cleanup."""
        def cleanup_job():
            import time
            while True:
                time.sleep(3600)  # Run every hour
                self._cleanup_expired_sessions()
        
        thread = threading.Thread(target=cleanup_job, daemon=True)
        thread.start()
    
    def get_session(self, phone_number: str) -> Session:
        """
        Get existing session or create new one.
        
        Args:
            phone_number: User's WhatsApp phone number
            
        Returns:
            Session object
        """
        with self.lock:
            if phone_number not in self.sessions:
                # Create new session
                session = Session(phone_number)
                self.sessions[phone_number] = session
                print(f"Created new session for {phone_number}")
                self._save_sessions()
            else:
                session = self.sessions[phone_number]
                session.last_active = datetime.now()
            
            return session
    
    def update_session(self, phone_number: str, session: Session):
        """Update session in storage."""
        with self.lock:
            self.sessions[phone_number] = session
            session.last_active = datetime.now()
            self._save_sessions()
    
    def add_message(self, phone_number: str, role: str, content: str):
        """Add message to session history and save."""
        session = self.get_session(phone_number)
        session.add_message(role, content)
        self.update_session(phone_number, session)
    
    def update_context(self, phone_number: str, **kwargs):
        """Update session context."""
        session = self.get_session(phone_number)
        session.update_context(**kwargs)
        self.update_session(phone_number, session)
    
    def clear_cart(self, phone_number: str):
        """Clear session's shopping cart."""
        session = self.get_session(phone_number)
        session.clear_cart()
        self.update_session(phone_number, session)
    
    def add_to_cart(self, phone_number: str, product: Dict, quantity: int = 1):
        """Add product to session's cart."""
        session = self.get_session(phone_number)
        session.add_to_cart(product, quantity)
        self.update_session(phone_number, session)
    
    def get_cart_total(self, phone_number: str) -> float:
        """Calculate total price of items in cart."""
        session = self.get_session(phone_number)
        total = 0.0
        for item in session.context.cart:
            try:
                price = float(item.get("price", 0))
                quantity = int(item.get("quantity", 1))
                total += price * quantity
            except (ValueError, TypeError):
                continue
        return total
    
    def get_session_stats(self) -> Dict:
        """Get session manager statistics."""
        with self.lock:
            now = datetime.now()
            active_24h = 0
            active_1h = 0
            
            for session in self.sessions.values():
                hours_since = (now - session.last_active).total_seconds() / 3600
                if hours_since < 1:
                    active_1h += 1
                if hours_since < 24:
                    active_24h += 1
            
            return {
                "total_sessions": len(self.sessions),
                "active_1h": active_1h,
                "active_24h": active_24h,
                "avg_messages_per_session": sum(len(s.history) for s in self.sessions.values()) / max(len(self.sessions), 1)
            }
    
    def delete_session(self, phone_number: str):
        """Delete a session."""
        with self.lock:
            if phone_number in self.sessions:
                del self.sessions[phone_number]
                self._save_sessions()
                return True
            return False

# Global instance for easy import
session_manager = SessionManager()