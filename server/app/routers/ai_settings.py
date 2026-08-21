"""Admin surface for the active Gemini model.

The allowlist is deployment config (`GEMINI_MODELS`); which entry is live is an
operator choice stored in `system_setting`. Only ADMIN may read or write here
-- a USER has no reason to know which model the deployment is calling, and
certainly no reason to change it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_admin
from app.models import AppUser, SystemSetting
from app.schemas import AiModelSettingsOut, AiModelSettingsUpdate
from app.services.analysis import model_settings

router = APIRouter(
    prefix="/api/admin/ai",
    tags=["admin-ai"],
    dependencies=[Depends(require_admin)],
)


def _out(db: Session) -> AiModelSettingsOut:
    row = db.get(SystemSetting, model_settings.GEMINI_MODEL_KEY)
    return AiModelSettingsOut(
        model=model_settings.active_model(db),
        available_models=model_settings.available_models(),
        updated_at=row.updated_at if row else None,
        updated_by=row.updated_by if row else None,
    )


@router.get("/settings", response_model=AiModelSettingsOut)
def get_ai_settings(db: Session = Depends(get_db)) -> AiModelSettingsOut:
    return _out(db)


@router.put("/settings", response_model=AiModelSettingsOut)
def update_ai_settings(
    body: AiModelSettingsUpdate,
    db: Session = Depends(get_db),
    admin: AppUser = Depends(require_admin),
) -> AiModelSettingsOut:
    try:
        model_settings.set_active_model(db, model=body.model, admin=admin)
    except model_settings.UnknownModel as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Model '{exc}' is not in GEMINI_MODELS",
        ) from exc
    return _out(db)
