"""Resolve the active Gemini model: env default, admin override, allowlist.

`GEMINI_MODEL` / `GEMINI_MODELS` are deployment config. Which of those models is
*live* is an operator choice that must survive a restart, so it lives in
`system_setting` the same way job schedules outlive their env seeds.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AppUser, SystemSetting

GEMINI_MODEL_KEY = "gemini_model"


class UnknownModel(ValueError):
    """The requested id is not in `GEMINI_MODELS`."""


def available_models() -> list[str]:
    return list(get_settings().gemini_model_list)


def active_model(db: Session) -> str:
    """The model generation and the cache key must agree on.

    A stale row whose value was removed from the allowlist falls through to the
    env default rather than keeping generation pointed at something the
    deployment no longer intends to call.
    """
    settings = get_settings()
    allowed = set(settings.gemini_model_list)
    row = db.get(SystemSetting, GEMINI_MODEL_KEY)
    if row is not None and row.value in allowed:
        return row.value
    if settings.gemini_model in allowed:
        return settings.gemini_model
    # Empty allowlist is a misconfiguration; still return the configured
    # default so the feature fails at the provider rather than here.
    return settings.gemini_model_list[0] if settings.gemini_model_list else settings.gemini_model


def set_active_model(db: Session, *, model: str, admin: AppUser) -> SystemSetting:
    """Persist the admin's pick. Refuses anything outside the env allowlist."""
    model = model.strip()
    if model not in available_models():
        raise UnknownModel(model)

    row = db.get(SystemSetting, GEMINI_MODEL_KEY)
    if row is None:
        row = SystemSetting(key=GEMINI_MODEL_KEY, value=model)
        db.add(row)
    else:
        row.value = model
    row.updated_by = admin.username
    db.commit()
    db.refresh(row)
    return row
