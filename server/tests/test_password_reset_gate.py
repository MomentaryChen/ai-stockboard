"""`must_change_password` restricted mode.

README / deps.py: an account sitting on an ADMIN-issued temporary password
may call exactly two endpoints (GET /api/auth/me, POST /api/auth/me/password).
Everything else that takes `get_current_user` answers 403 with a fixed detail
string the frontend interceptor keys on.

The rule is not in a middleware -- it is whichever dependency each route
declared. A third `get_authenticated_user` (or switching /me onto
`get_current_user`) would recreate the lock-in or punch a hole, and would
compile fine. The inventory test is what notices.
"""

from types import SimpleNamespace

from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.deps import (
    PASSWORD_RESET_REQUIRED,
    get_authenticated_user,
    get_current_user,
)
from app.main import app as real_app


def _forced_user():
    return SimpleNamespace(
        id=1,
        username="alice",
        email="alice@example.com",
        phone=None,
        role="USER",
        is_active=True,
        must_change_password=True,
    )


def _gate_app() -> FastAPI:
    app = FastAPI()
    app.dependency_overrides[get_authenticated_user] = _forced_user

    @app.get("/bare")
    def bare(user=Depends(get_authenticated_user)):  # noqa: ANN001
        return {"id": user.id}

    @app.get("/restricted")
    def restricted(user=Depends(get_current_user)):  # noqa: ANN001
        return {"id": user.id}

    return app


def test_get_current_user_returns_403_with_the_contract_detail():
    client = TestClient(_gate_app())
    res = client.get("/restricted")
    assert res.status_code == 403
    assert res.json()["detail"] == PASSWORD_RESET_REQUIRED
    # 403, not 401: a refresh would not help, and the interceptor must not
    # treat this as an expired session.
    assert "WWW-Authenticate" not in res.headers


def test_get_authenticated_user_still_lets_the_account_read_itself():
    client = TestClient(_gate_app())
    res = client.get("/bare")
    assert res.status_code == 200
    assert res.json() == {"id": 1}


def test_password_reset_required_string_is_the_frontend_contract():
    # frontend/src/api/client.ts PASSWORD_RESET_REQUIRED. Rewording one side
    # turns the change-password redirect into a dead end.
    assert PASSWORD_RESET_REQUIRED == "Password reset required"


def _dep_calls(dependant) -> set:  # noqa: ANN001
    found: set = set()
    stack = [dependant]
    while stack:
        node = stack.pop()
        if node.call is not None:
            found.add(node.call)
        stack.extend(node.dependencies)
    return found


def _iter_api_routes(routes):  # noqa: ANN001
    """FastAPI 0.141 keeps included routers nested as `_IncludedRouter`."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        original = getattr(route, "original_router", None)
        if original is not None:
            yield from _iter_api_routes(original.routes)


def test_only_me_and_change_password_skip_the_restricted_mode_gate():
    """Walk the real app: bare `get_authenticated_user` on exactly two routes."""
    allowed = {
        ("GET", "/api/auth/me"),
        ("POST", "/api/auth/me/password"),
    }
    bare: set[tuple[str, str]] = set()
    for route in _iter_api_routes(real_app.routes):
        calls = _dep_calls(route.dependant)
        if get_authenticated_user in calls and get_current_user not in calls:
            for method in route.methods:
                if method == "HEAD":
                    continue
                bare.add((method, route.path))

    assert bare == allowed
