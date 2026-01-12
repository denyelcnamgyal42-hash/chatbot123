"""Gunicorn configuration file."""
import logging
import os

logger = logging.getLogger(__name__)

# Increase timeout to 180 seconds (for dense retrieval initialization)
timeout = 180
worker_timeout = 180

def post_fork(server, worker):
    """Called after each worker process is forked."""
    logger.info(f"🔄 Gunicorn worker {worker.pid} forked - starting worker thread")
    
    # Import here to avoid circular imports
    import app_unified
    
    # Reset the flag so thread starts in this worker
    app_unified._worker_thread_started = False
    app_unified.ensure_worker_thread()
    
    # Pre-load agent in this worker process
    logger.info(f"🔄 Pre-loading agent in worker {worker.pid}...")
    app_unified.preload_agent()
    
    logger.info(f"✅ Worker thread started in worker {worker.pid}")
