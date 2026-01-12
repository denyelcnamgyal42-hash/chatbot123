"""Check session storage integrity."""
import json
import os
from datetime import datetime
from session_manager import SessionManager, Session

def check_sessions():
    """Check if sessions are stored correctly."""
    print("=" * 50)
    print("Checking Session Storage")
    print("=" * 50)
    
    # Check if sessions.json exists
    if not os.path.exists("sessions.json"):
        print("[ERROR] sessions.json file does not exist")
        return
    
    # Load and validate JSON
    try:
        with open("sessions.json", 'r', encoding='utf-8') as f:
            data = json.load(f)
        print("[OK] sessions.json is valid JSON")
    except json.JSONDecodeError as e:
        print(f"[ERROR] Invalid JSON: {e}")
        return
    except Exception as e:
        print(f"[ERROR] Error reading file: {e}")
        return
    
    # Check structure
    print(f"\nTotal sessions: {len(data)}")
    
    if len(data) == 0:
        print("[WARNING] No sessions found")
        return
    
    # Validate each session
    issues = []
    for phone, session_data in data.items():
        print(f"\nSession: {phone}")
        
        # Check required fields
        required_fields = ["phone_number", "created_at", "last_active", "history", "context", "session_id"]
        missing_fields = [field for field in required_fields if field not in session_data]
        if missing_fields:
            issues.append(f"Session {phone}: Missing fields: {missing_fields}")
            print(f"  [ERROR] Missing fields: {missing_fields}")
        else:
            print(f"  [OK] All required fields present")
        
        # Check history
        history = session_data.get("history", [])
        print(f"  History: {len(history)} messages")
        
        if len(history) > 10:
            issues.append(f"Session {phone}: History has {len(history)} messages (should be max 10)")
            print(f"  [WARNING] History has {len(history)} messages (limit is 10)")
        
        # Check history structure
        for i, msg in enumerate(history):
            if "role" not in msg or "content" not in msg:
                issues.append(f"Session {phone}: Message {i} missing role or content")
                print(f"  [ERROR] Message {i} missing role or content")
        
        # Check timestamps
        try:
            created = datetime.fromisoformat(session_data["created_at"])
            last_active = datetime.fromisoformat(session_data["last_active"])
            print(f"  Created: {created.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"  Last active: {last_active.strftime('%Y-%m-%d %H:%M:%S')}")
            
            # Check if session is expired (48 hours TTL)
            expiry = last_active.timestamp() + (48 * 3600)
            if datetime.now().timestamp() > expiry:
                print(f"  [WARNING] Session expired (48h TTL)")
            else:
                print(f"  [OK] Session is valid")
        except Exception as e:
            issues.append(f"Session {phone}: Invalid timestamps: {e}")
            print(f"  [ERROR] Invalid timestamps: {e}")
        
        # Check context
        context = session_data.get("context", {})
        print(f"  Context fields: {list(context.keys())}")
    
    # Test SessionManager
    print("\n" + "=" * 50)
    print("Testing SessionManager")
    print("=" * 50)
    
    try:
        manager = SessionManager()
        print(f"[OK] SessionManager initialized")
        print(f"Loaded {len(manager.sessions)} sessions")
        
        # Test session retrieval
        if data:
            test_phone = list(data.keys())[0]
            session = manager.get_session(test_phone)
            print(f"[OK] Successfully retrieved session for {test_phone}")
            print(f"   History: {len(session.history)} messages")
            print(f"   Session ID: {session.session_id}")
    except Exception as e:
        issues.append(f"SessionManager error: {e}")
        print(f"[ERROR] SessionManager error: {e}")
    
    # Summary
    print("\n" + "=" * 50)
    print("Summary")
    print("=" * 50)
    
    if issues:
        print(f"[WARNING] Found {len(issues)} issue(s):")
        for issue in issues:
            print(f"   - {issue}")
    else:
        print("[OK] All checks passed! Sessions are stored correctly.")
    
    return len(issues) == 0

if __name__ == "__main__":
    check_sessions()
