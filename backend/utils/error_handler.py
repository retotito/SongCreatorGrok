"""Global error handling for FastAPI."""

from fastapi import Request
from fastapi.responses import JSONResponse
from utils.logger import log
import traceback


async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch all unhandled exceptions and return a clean JSON response.
    
    This prevents the server from crashing on unexpected errors.
    """
    error_id = id(exc)
    log.error(f"Unhandled exception [{error_id}]: {type(exc).__name__}: {exc}")
    log.debug(f"Traceback [{error_id}]:\n{traceback.format_exc()}")
    
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "message": f"Internal server error: {type(exc).__name__}",
            "detail": str(exc),
            "error_id": error_id
        }
    )


class ServiceError(Exception):
    """Custom exception for service-level errors with user-friendly messages."""
    
    def __init__(self, message: str, detail: str = "", status_code: int = 400):
        self.message = message
        self.detail = detail
        self.status_code = status_code
        super().__init__(message)


async def service_exception_handler(request: Request, exc: ServiceError) -> JSONResponse:
    """Handle known service errors with clean messages."""
    log.warning(f"Service error: {exc.message} - {exc.detail}")
    
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "status": "error",
            "message": exc.message,
            "detail": exc.detail
        }
    )
