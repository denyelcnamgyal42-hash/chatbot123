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
    
    # Pre-load agent in this worker process (synchronously to ensure it's ready)
    logger.info(f"🔄 Pre-loading agent in worker {worker.pid} (synchronous)...")
    import time
    preload_start = time.time()
    try:
        # Force load the agent in this worker process
        agent = app_unified.get_agent()
        preload_elapsed = time.time() - preload_start
        logger.info(f"✅ Agent pre-loaded in worker {worker.pid} in {preload_elapsed:.2f}s")
    except Exception as e:
        preload_elapsed = time.time() - preload_start
        logger.error(f"❌ Failed to pre-load agent in worker {worker.pid} after {preload_elapsed:.2f}s: {e}", exc_info=True)
        import traceback
        logger.error(f"Preload traceback: {traceback.format_exc()}")
        # Continue anyway - will load on first message
    
    logger.info(f"✅ Worker thread started in worker {worker.pid}")
