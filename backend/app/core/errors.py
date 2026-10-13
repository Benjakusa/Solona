from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message


async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


async def integrity_error_handler(_: Request, __: IntegrityError) -> JSONResponse:
    # Constraint names and SQL may expose implementation details or tenant data.
    return JSONResponse(
        status_code=409,
        content={"error": {"code": "conflict", "message": "The requested record conflicts with existing data."}},
    )
