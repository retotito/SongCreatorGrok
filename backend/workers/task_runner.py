"""Task runner for executing AI models in isolated subprocesses.

Prevents heavy AI model failures from crashing the main server.
"""

import asyncio
import json
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Callable, Dict
from utils.logger import log_step

# Process pool for running AI tasks
_executor = ProcessPoolExecutor(max_workers=2)


async def run_in_subprocess(func: Callable, *args, **kwargs) -> Any:
    """Run a function in a separate process.
    
    This isolates AI model execution from the main server process.
    If the model crashes, the server stays alive.
    """
    loop = asyncio.get_event_loop()
    
    try:
        result = await loop.run_in_executor(_executor, func, *args)
        return result
    except Exception as e:
        log_step("WORKER", f"Subprocess failed: {type(e).__name__}: {e}")
        raise


def shutdown_workers():
    """Clean shutdown of the process pool."""
    _executor.shutdown(wait=False)
    log_step("WORKER", "Worker pool shut down")
