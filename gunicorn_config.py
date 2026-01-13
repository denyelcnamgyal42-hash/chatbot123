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
    
    # Pre-load agent in this worker process (in background thread to avoid blocking)
    logger.info(f"🔄 Pre-loading agent in worker {worker.pid} (background)...")
    from threading import Thread
    import time
    
    def preload_in_background():
        preload_start = time.time()
        try:
            logger.info(f"🔄 Background preload started in worker {worker.pid}...")
            # Force load the agent in this worker process
            agent = app_unified.get_agent()
            preload_elapsed = time.time() - preload_start
            logger.info(f"✅ Agent pre-loaded in worker {worker.pid} in {preload_elapsed:.2f}s")
        except Exception as e:
            preload_elapsed = time.time() - preload_start
            logger.error(f"❌ Failed to pre-load agent in worker {worker.pid} after {preload_elapsed:.2f}s: {e}", exc_info=True)
            import traceback
            logger.error(f"Preload traceback: {traceback.format_exc()}")
    
    # Start preload in background thread so worker can accept requests
    preload_thread = Thread(target=preload_in_background, daemon=True)
    preload_thread.start()
    
    logger.info(f"✅ Worker thread started in worker {worker.pid} (agent preloading in background)")
