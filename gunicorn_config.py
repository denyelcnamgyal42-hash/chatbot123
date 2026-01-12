"""Gunicorn configuration file."""
import logging

logger = logging.getLogger(__name__)

def post_fork(server, worker):
    """Called after each worker process is forked."""
    # Import here to avoid circular imports
    from app_unified import ensure_worker_thread
    import app_unified
    
    # Reset the flag so thread starts in this worker
    app_unified._worker_thread_started = False
    logger.info(f"🔄 Gunicorn worker {worker.pid} forked - starting worker thread")
    ensure_worker_thread()
