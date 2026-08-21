"""Process-wide logging setup, and the request id every line carries.

The service runs several requests and several background jobs concurrently in
one process, so an unlabelled log file interleaves them: a traceback from a job
sits between two lines of somebody's history fetch, and nothing says which
work each line belongs to. `request_id` fixes that -- nginx mints one per
request (`$req_id`, see frontend/nginx/conf.d/00-maps.conf), the middleware
puts it in a ContextVar, and the filter below stamps it onto every record
emitted while that context is active, whoever wrote the log call.

A ContextVar rather than thread-local state because the request path is async:
asyncio tasks inherit the context of the task that created them, threads do
not. Background jobs run on their own threads and set their own id explicitly
(see services/jobs/runner.py).
"""

import contextvars
import datetime
import json
import logging
import re
import uuid

# "-" rather than None so text log lines stay a fixed shape when there is no
# request in flight -- startup, shutdown, and the scheduler's own ticks.
NO_REQUEST = "-"

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=NO_REQUEST
)

# What an id coming in from outside is allowed to look like. Anything else is
# discarded and replaced with one of ours: the value is written verbatim into
# every log line, and an attacker-chosen id is otherwise a way to forge log
# entries or to put 8 KB on disk per request.
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def sanitise_request_id(value: str | None) -> str | None:
    """Accept an upstream id only if it is short and boring. Else None."""
    if value and _SAFE_ID.match(value):
        return value
    return None


def get_request_id() -> str:
    return _request_id.get()


def set_request_id(value: str) -> contextvars.Token:
    return _request_id.set(value)


def reset_request_id(token: contextvars.Token) -> None:
    _request_id.reset(token)


class RequestIdFilter(logging.Filter):
    """Attach the current request id to every record.

    Installed on the handler rather than on a logger: a filter on a logger does
    not run for records that propagate up from its children, and the point is
    that third-party libraries -- sqlalchemy, uvicorn, twstock -- get the id
    too, without knowing this module exists.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line, for a log shipper to parse.

    Deliberately hand-rolled: the alternative is a dependency whose only job is
    this, on a service that emits a few hundred lines a day.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.datetime.fromtimestamp(
                record.created, datetime.timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", NO_REQUEST),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        # Anything a call site passed as `extra=` -- e.g. the access log's
        # status and duration -- rides along as its own field instead of being
        # baked into the message string.
        for key, value in getattr(record, "__dict__", {}).items():
            if key.startswith("ctx_"):
                payload[key[4:]] = value

        return json.dumps(payload, ensure_ascii=False, default=str)


TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s [%(request_id)s]: %(message)s"


def configure(*, level: str = "INFO", fmt: str = "text") -> None:
    """Install the root handler. Safe to call more than once.

    Replaces whatever uvicorn set up, which is why it runs at import of
    app.main: uvicorn configures logging before it imports the application, so
    the last word belongs to us either way.
    """
    handler = logging.StreamHandler()
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(
        JsonFormatter() if fmt.lower() == "json" else logging.Formatter(TEXT_FORMAT)
    )

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # uvicorn's access log says method, path and status but not which request
    # id or how long it took, and two access lines per request is one too many.
    # app/middleware.py writes the replacement. Silenced rather than removed so
    # this works the same whether the process was started with `uvicorn`,
    # `fastapi dev`, or from a test.
    logging.getLogger("uvicorn.access").handlers.clear()
    logging.getLogger("uvicorn.access").propagate = False
