import json
import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text

from app.api.v1.router import router
from app.core.config import get_settings
from app.core.errors import ApiError, api_error_handler, integrity_error_handler
from app.core.rate_limit import limiter
from app.db.session import get_db
from sqlalchemy.exc import IntegrityError


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({"level": record.levelname.lower(), "logger": record.name, "message": record.getMessage()})


logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler()])
for handler in logging.getLogger().handlers:
    handler.setFormatter(JsonFormatter())
logger = logging.getLogger("pharmacy_api")

settings = get_settings()
app = FastAPI(
    title="SOLONA Pharmacy Platform API",
    version="1.0.0",
    description="Versioned tenant and shop API for the SOLONA Pharmacy platform.",
    docs_url="/api/v1/docs",
    redoc_url="/api/v1/redoc",
    openapi_url="/api/v1/openapi.json",
)
app.state.limiter = limiter
app.add_exception_handler(ApiError, api_error_handler)
app.add_exception_handler(IntegrityError, integrity_error_handler)

if settings.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError):
    # Drop Pydantic's `input` and `ctx`; request bodies can include passwords
    # and invitation/device credentials.
    details = [{"location": list(error.get("loc", ())), "message": error.get("msg", "Invalid value"), "type": error.get("type", "value_error")} for error in exc.errors()]
    return JSONResponse(status_code=422, content={"error": {"code": "validation_error", "message": "The request is invalid.", "details": details}})


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(_: Request, __: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"error": {"code": "rate_limited", "message": "Too many requests. Try again later."}})


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, _: Exception):
    request_id = getattr(request.state, "request_id", "unknown")
    logger.error(json.dumps({"event": "request_failed", "request_id": request_id, "path": request.url.path}))
    return JSONResponse(status_code=500, content={"error": {"code": "internal_error", "message": "The request could not be completed."}})


@app.middleware("http")
async def request_logging(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID")
    if not request_id or len(request_id) > 100 or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in request_id):
        request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.error(json.dumps({"event": "request_failed", "request_id": request_id, "method": request.method, "path": request.url.path}))
        raise
    elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
    response.headers["X-Request-ID"] = request_id
    logger.info(json.dumps({"event": "request_completed", "request_id": request_id, "method": request.method, "path": request.url.path, "status": response.status_code, "duration_ms": elapsed_ms}))
    return response


app.include_router(router, prefix="/api/v1")


@app.get("/health/live", tags=["operations"])
def liveness():
    return {"status": "ok"}


@app.get("/health/ready", tags=["operations"])
def readiness():
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
    return {"status": "ready"}
