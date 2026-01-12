"""Main application entry point."""
import threading
import config
import sys
import os

# Add parent directory to path so imports work
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import apps (dashboard app already has all routes)
from whatsapp_webhook import app as whatsapp_app
from employee_dashboard_api import app as dashboard_app
from background_tasks import get_task_manager

if __name__ == "__main__":
    print("=" * 50)
    print("Starting BotPocketFlow")
    print("=" * 50)
    
    print(f"WhatsApp Webhook: http://localhost:{config.PORT}/webhook")
    print(f"Health check: http://localhost:{config.PORT}/health")
    print(f"Dashboard: http://localhost:{config.DASHBOARD_PORT}/")
    print("-" * 50)
    
    # Run in threads
    def run_whatsapp():
        whatsapp_app.run(port=config.PORT, host="0.0.0.0", debug=config.DEBUG, use_reloader=False)
    
    def run_dashboard():
        dashboard_app.run(port=config.DASHBOARD_PORT, host="0.0.0.0", debug=config.DEBUG, use_reloader=False)
    
    t1 = threading.Thread(target=run_whatsapp)
    t2 = threading.Thread(target=run_dashboard)
    
    t1.daemon = True
    t2.daemon = True
    
    t1.start()
    t2.start()
    
    # Start background tasks (auto checkout & vectorstore refresh)
    task_manager = get_task_manager()
    task_manager.start()
    
    print("✅ Both servers started!")
    print("✅ Background tasks started (auto checkout & vectorstore refresh)")
    print("Press Ctrl+C to stop")
    print("=" * 50)
    
    try:
        t1.join()
        t2.join()
    except KeyboardInterrupt:
        print("\n👋 Shutting down...")