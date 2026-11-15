"""App-wide 422 handler: validation errors never echo the request body (api/validation_errors.py)."""

import os

os.environ["TESTING"] = "true"

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from api.validation_errors import install_validation_error_handler


class _Body(BaseModel):
    name: str
    access_token: str


def _client() -> TestClient:
    app = FastAPI()
    install_validation_error_handler(app)

    @app.post("/things")
    def create(body: _Body):
        return body

    return TestClient(app)


def test_missing_field_does_not_echo_the_body():
    response = _client().post("/things", json={"access_token": "tok-SECRET"})
    assert response.status_code == 422
    assert "tok-SECRET" not in response.text
    [error] = response.json()["detail"]
    assert error["loc"] == ["body", "name"]
    assert error["type"] == "missing"
    assert "input" not in error


def test_bad_field_does_not_echo_its_value():
    response = _client().post("/things", json={"name": "x", "access_token": {"nested": "tok-SECRET"}})
    assert response.status_code == 422
    assert "tok-SECRET" not in response.text
    assert response.json()["detail"][0]["loc"] == ["body", "access_token"]


def test_main_app_installs_it():
    from tests.test_utils import create_test_client

    _, app = create_test_client()
    from fastapi.exceptions import RequestValidationError

    from api.validation_errors import validation_error_without_input

    assert app.exception_handlers[RequestValidationError] is validation_error_without_input
