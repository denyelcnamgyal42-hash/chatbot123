"""Main application entry point."""
import os
import config
import sys

# Add parent directory to path so imports work
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import unified app
from app_unified import app
from background_tasks import get_task_manager

if __name__ == "__main__":
    print("=" * 50)
    print("Starting BotPocketFlow")
    print("=" * 50)
    
    # Use Render's PORT if available, otherwise use config
    port = int(os.getenv("PORT", config.PORT))
    
    print(f"WhatsApp Webhook: http://localhost:{port}/webhook")
    print(f"Health check: http://localhost:{port}/health")
    print(f"Dashboard: http://localhost:{port}/")
    print("-" * 50)
    
    # Start background tasks (auto checkout & vectorstore refresh)
    task_manager = get_task_manager()
    task_manager.start()
    
    print("✅ Background tasks started (auto checkout & vectorstore refresh)")
    print("Press Ctrl+C to stop")
    print("=" * 50)
    
    # Run unified app
    app.run(
        host="0.0.0.0",
        port=port,
        debug=config.DEBUG,
        threaded=True
    )