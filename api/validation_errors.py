"""422 responses that do not echo the request back.

FastAPI's default handler puts each error's `input` in the response: the offending value,
or for a missing field or a model-level error the whole request body. Bodies carry secrets
(MCP server headers, user access tokens), so a typo in one field would send every secret in
the request back in the error, and from there into client logs. This handler returns the
same shape without `input`; `loc` and `msg` still say what is wrong.
"""

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.status import HTTP_422_UNPROCESSABLE_CONTENT


async def validation_error_without_input(request: Request, exc: Exception) -> JSONResponse:
    errors = exc.errors() if isinstance(exc, RequestValidationError) else []
    detail = [{key: value for key, value in error.items() if key != "input"} for error in errors]
    return JSONResponse(status_code=HTTP_422_UNPROCESSABLE_CONTENT, content={"detail": jsonable_encoder(detail)})


def install_validation_error_handler(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, validation_error_without_input)
