"""The request id is what makes an interleaved log readable, so the rules
about where it comes from are worth pinning down.

An id arriving from outside is trusted-but-checked: nginx mints one per request
and it is more useful than one of ours (it is also in the nginx access line),
but it is written verbatim into every log line this process emits, so an
unbounded or exotic value is a way to forge log entries.
"""

import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.logging_config import (
    NO_REQUEST,
    RequestIdFilter,
    get_request_id,
    sanitise_request_id,
)
from app.middleware import RequestContextMiddleware


def _client() -> TestClient:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/echo")
    def echo() -> dict:
        # Read inside the endpoint on purpose: a ContextVar set by
        # BaseHTTPMiddleware would not be visible here, which is exactly why
        # the middleware is raw ASGI.
        return {"seen": get_request_id()}

    @app.get("/boom")
    def boom() -> dict:
        raise RuntimeError("no")

    return TestClient(app, raise_server_exceptions=False)


def test_generates_an_id_and_echoes_it_back() -> None:
    response = _client().get("/echo")

    assert response.status_code == 200
    header = response.headers["x-request-id"]
    assert header
    # The endpoint and the response header must agree, or a user quoting the
    # id from their browser lands on nothing.
    assert response.json()["seen"] == header


def test_reuses_a_sane_upstream_id() -> None:
    response = _client().get("/echo", headers={"X-Request-ID": "nginx-abc123"})

    assert response.headers["x-request-id"] == "nginx-abc123"
    assert response.json()["seen"] == "nginx-abc123"


def test_replaces_an_unusable_upstream_id() -> None:
    for hostile in ("x" * 200, "has spaces", "semi;colon", ""):
        response = _client().get("/echo", headers={"X-Request-ID": hostile})
        assert response.headers["x-request-id"] != hostile


def test_context_does_not_leak_between_requests() -> None:
    client = _client()
    first = client.get("/echo").json()["seen"]
    second = client.get("/echo").json()["seen"]

    assert first != second
    # And nothing is left behind for the scheduler thread to inherit.
    assert get_request_id() == NO_REQUEST


def test_handled_error_responses_carry_the_id() -> None:
    response = _client().get("/nope")

    assert response.status_code == 404
    assert response.headers["x-request-id"]


def test_unhandled_exception_is_logged_with_its_id(caplog) -> None:
    """The 500 is the case the id matters most for -- and the one case where
    it reaches the log but not the response.

    Starlette's ServerErrorMiddleware wraps the user middleware stack from the
    outside, so it writes that response past our send wrapper. Asserted here so
    the day someone moves the middleware and the header appears, this test says
    what changed rather than quietly passing.
    """
    # RequestIdFilter lives on the *handler* -- that is what makes it apply to
    # sqlalchemy and uvicorn records too -- so caplog's own handler needs it
    # before the id is visible here.
    caplog.handler.addFilter(RequestIdFilter())
    with caplog.at_level(logging.INFO, logger="app.access"):
        response = _client().get("/boom")

    assert response.status_code == 500
    assert "x-request-id" not in response.headers

    line = next(r for r in caplog.records if r.name == "app.access")
    assert line.ctx_status == 500
    assert line.request_id not in ("", NO_REQUEST)


def test_filter_stamps_records_from_any_logger() -> None:
    """Third-party loggers must get the id without knowing it exists."""
    record = logging.LogRecord(
        "sqlalchemy.engine", logging.INFO, __file__, 1, "SELECT 1", None, None
    )
    RequestIdFilter().filter(record)

    assert record.request_id == NO_REQUEST


def test_sanitise_accepts_only_short_boring_ids() -> None:
    assert sanitise_request_id("abc-123_x.y:z") == "abc-123_x.y:z"
    assert sanitise_request_id("a" * 64) == "a" * 64
    assert sanitise_request_id("a" * 65) is None
    assert sanitise_request_id("drop table") is None
    assert sanitise_request_id(None) is None
