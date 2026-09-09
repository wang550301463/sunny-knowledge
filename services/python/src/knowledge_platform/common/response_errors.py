"""Do not allow FastAPI output validation to disclose private response bodies in logs."""

from fastapi.exceptions import ResponseValidationError
from fastapi.responses import JSONResponse


def install_response_validation_handler(app):
    """Handle only this known server error; cancellation and other handlers are untouched."""

    @app.exception_handler(ResponseValidationError)
    async def invalid_response(_request, _error):
        # Never stringify the exception, inspect errors(), or log its body. The
        # existing telemetry still observes a fixed 500 and the route template.
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "response_contract_violation",
                    "message": "Service response failed validation",
                }
            },
        )