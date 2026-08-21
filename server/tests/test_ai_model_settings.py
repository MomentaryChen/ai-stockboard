"""The admin-overridable Gemini model: allowlist, fallback, and persistence."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.analysis import model_settings


class _FakeDb:
    """Minimal stand-in: get/add/commit/refresh over an in-memory dict."""

    def __init__(self, rows: dict | None = None):
        self.rows = rows or {}
        self.added = []

    def get(self, model, key):
        return self.rows.get(key)

    def add(self, row):
        self.added.append(row)
        self.rows[row.key] = row

    def commit(self):
        return None

    def refresh(self, row):
        return None


def test_available_models_include_env_default(monkeypatch):
    monkeypatch.setattr(
        model_settings,
        "get_settings",
        lambda: SimpleNamespace(
            gemini_model="gemini-3.5-flash",
            gemini_model_list=[
                "gemini-3.5-flash",
                "gemini-2.5-flash",
                "gemini-2.5-pro",
            ],
        ),
    )
    assert model_settings.available_models()[0] == "gemini-3.5-flash"
    assert "gemini-2.5-pro" in model_settings.available_models()


def test_active_model_falls_back_to_env_when_no_row(monkeypatch):
    monkeypatch.setattr(
        model_settings,
        "get_settings",
        lambda: SimpleNamespace(
            gemini_model="gemini-3.5-flash",
            gemini_model_list=["gemini-3.5-flash", "gemini-2.5-flash"],
        ),
    )
    assert model_settings.active_model(_FakeDb()) == "gemini-3.5-flash"


def test_active_model_uses_stored_row_when_allowed(monkeypatch):
    monkeypatch.setattr(
        model_settings,
        "get_settings",
        lambda: SimpleNamespace(
            gemini_model="gemini-3.5-flash",
            gemini_model_list=["gemini-3.5-flash", "gemini-2.5-flash"],
        ),
    )
    row = SimpleNamespace(key="gemini_model", value="gemini-2.5-flash")
    assert model_settings.active_model(_FakeDb({"gemini_model": row})) == "gemini-2.5-flash"


def test_active_model_ignores_stale_row_outside_allowlist(monkeypatch):
    monkeypatch.setattr(
        model_settings,
        "get_settings",
        lambda: SimpleNamespace(
            gemini_model="gemini-3.5-flash",
            gemini_model_list=["gemini-3.5-flash", "gemini-2.5-flash"],
        ),
    )
    row = SimpleNamespace(key="gemini_model", value="gemini-flash-latest")
    assert model_settings.active_model(_FakeDb({"gemini_model": row})) == "gemini-3.5-flash"


def test_set_active_model_refuses_unknown(monkeypatch):
    monkeypatch.setattr(
        model_settings,
        "get_settings",
        lambda: SimpleNamespace(
            gemini_model="gemini-3.5-flash",
            gemini_model_list=["gemini-3.5-flash"],
        ),
    )
    admin = SimpleNamespace(username="admin")
    with pytest.raises(model_settings.UnknownModel):
        model_settings.set_active_model(
            _FakeDb(), model="not-a-real-model", admin=admin
        )


def test_set_active_model_persists(monkeypatch):
    monkeypatch.setattr(
        model_settings,
        "get_settings",
        lambda: SimpleNamespace(
            gemini_model="gemini-3.5-flash",
            gemini_model_list=["gemini-3.5-flash", "gemini-2.5-flash"],
        ),
    )
    db = _FakeDb()
    admin = SimpleNamespace(username="ops")
    row = model_settings.set_active_model(db, model="gemini-2.5-flash", admin=admin)
    assert row.value == "gemini-2.5-flash"
    assert row.updated_by == "ops"
    assert model_settings.active_model(db) == "gemini-2.5-flash"
