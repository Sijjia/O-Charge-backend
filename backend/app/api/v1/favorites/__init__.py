"""
Favorites API endpoints - управление избранными локациями.

PWA ожидает следующие эндпоинты:
- GET /api/v1/favorites - список избранных location_id
- POST /api/v1/favorites - добавить в избранное (body: {location_id})
- DELETE /api/v1/favorites/{location_id} - удалить из избранного
- GET /api/v1/favorites/{location_id}/check - проверить статус
- POST /api/v1/favorites/{location_id}/toggle - переключить статус
"""
import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
import uuid

from app.db.session import get_db
from app.api.v1.schemas.favorites import FavoritesListResponse, FavoriteAddResponse, FavoriteCheckResponse, FavoriteToggleResponse
from app.api.v1.schemas.common import AUTH_RESPONSES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/favorites")


class AddFavoriteRequest(BaseModel):
    location_id: str


@router.get(
    "",
    summary="List favorite locations",
    description="Returns list of location IDs marked as favorites by the authenticated client.",
    response_model=FavoritesListResponse,
    responses=AUTH_RESPONSES,
)
async def get_favorites(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Получить список избранных локаций пользователя.

    PWA ожидает формат:
    {
        "success": true,
        "favorites": ["location_id1", "location_id2", ...]
    }
    """
    client_id = getattr(request.state, "client_id", None)
    if not client_id:
        return {
            "success": False,
            "error": "unauthorized",
            "message": "Missing or invalid authentication"
        }

    try:
        query = text("""
            SELECT location_id
            FROM user_favorites
            WHERE user_id = :user_id
            ORDER BY created_at DESC
        """)
        rows = db.execute(query, {"user_id": client_id}).fetchall()
        favorites = [row.location_id for row in rows]
    except Exception as e:
        logger.warning(f"[Favorites] get error for user {client_id}: {e}")
        favorites = []

    return {
        "success": True,
        "favorites": favorites
    }


@router.post(
    "",
    summary="Add favorite location",
    description="Adds a location to the client's favorites list.",
    response_model=FavoriteAddResponse,
    responses=AUTH_RESPONSES,
)
async def add_favorite(
    request: Request,
    body: AddFavoriteRequest,
    db: Session = Depends(get_db),
):
    """
    Добавить локацию в избранное.

    PWA ожидает формат:
    {
        "success": true,
        "already_exists": boolean (optional)
    }
    """
    client_id = getattr(request.state, "client_id", None)
    if not client_id:
        return {
            "success": False,
            "error": "unauthorized",
            "message": "Missing or invalid authentication"
        }

    try:
        existing = db.execute(
            text("""
                SELECT id FROM user_favorites
                WHERE user_id = :user_id AND location_id = :location_id
            """),
            {"user_id": client_id, "location_id": body.location_id}
        ).fetchone()

        if existing:
            return {"success": True, "already_exists": True}

        new_id = str(uuid.uuid4())
        db.execute(
            text("""
                INSERT INTO user_favorites (id, user_id, location_id, created_at)
                VALUES (:id, :user_id, :location_id, NOW())
            """),
            {"id": new_id, "user_id": client_id, "location_id": body.location_id}
        )
        db.commit()
        return {"success": True, "already_exists": False}
    except Exception as e:
        logger.warning(f"[Favorites] add error for user {client_id}: {e}")
        db.rollback()
        return {"success": False, "error": "invalid_user_id", "message": "Cannot manage favorites for this user"}


@router.delete(
    "/{location_id}",
    summary="Remove favorite location",
    description="Removes a location from the client's favorites list.",
    responses=AUTH_RESPONSES,
)
async def remove_favorite(
    request: Request,
    location_id: str,
    db: Session = Depends(get_db),
):
    """
    Удалить локацию из избранного.

    PWA ожидает формат:
    {
        "success": true
    }
    """
    client_id = getattr(request.state, "client_id", None)
    if not client_id:
        return {
            "success": False,
            "error": "unauthorized",
            "message": "Missing or invalid authentication"
        }

    try:
        db.execute(
            text("""
                DELETE FROM user_favorites
                WHERE user_id = :user_id AND location_id = :location_id
            """),
            {"user_id": client_id, "location_id": location_id}
        )
        db.commit()
    except Exception as e:
        logger.warning(f"[Favorites] remove error for user {client_id}: {e}")
        db.rollback()

    return {"success": True}


@router.get(
    "/{location_id}/check",
    summary="Check if location is favorite",
    description="Returns whether a specific location is in the client's favorites.",
    response_model=FavoriteCheckResponse,
    responses=AUTH_RESPONSES,
)
async def check_favorite(
    request: Request,
    location_id: str,
    db: Session = Depends(get_db),
):
    """
    Проверить, является ли локация избранной.

    PWA ожидает формат:
    {
        "success": true,
        "is_favorite": boolean
    }
    """
    client_id = getattr(request.state, "client_id", None)
    if not client_id:
        return {
            "success": False,
            "error": "unauthorized",
            "message": "Missing or invalid authentication"
        }

    try:
        existing = db.execute(
            text("""
                SELECT id FROM user_favorites
                WHERE user_id = :user_id AND location_id = :location_id
            """),
            {"user_id": client_id, "location_id": location_id}
        ).fetchone()
        return {"success": True, "is_favorite": existing is not None}
    except Exception as e:
        logger.warning(f"[Favorites] check error for user {client_id}: {e}")
        return {"success": True, "is_favorite": False}


@router.post(
    "/{location_id}/toggle",
    summary="Toggle favorite location",
    description="Adds or removes a location from favorites. Returns the new state.",
    response_model=FavoriteToggleResponse,
    responses=AUTH_RESPONSES,
)
async def toggle_favorite(
    request: Request,
    location_id: str,
    db: Session = Depends(get_db),
):
    """
    Переключить статус избранного.

    PWA ожидает формат:
    {
        "success": true,
        "is_favorite": boolean,
        "action": "added" | "removed"
    }
    """
    client_id = getattr(request.state, "client_id", None)
    if not client_id:
        return {
            "success": False,
            "error": "unauthorized",
            "message": "Missing or invalid authentication"
        }

    try:
        existing = db.execute(
            text("""
                SELECT id FROM user_favorites
                WHERE user_id = :user_id AND location_id = :location_id
            """),
            {"user_id": client_id, "location_id": location_id}
        ).fetchone()

        if existing:
            db.execute(
                text("""
                    DELETE FROM user_favorites
                    WHERE user_id = :user_id AND location_id = :location_id
                """),
                {"user_id": client_id, "location_id": location_id}
            )
            db.commit()
            return {"success": True, "is_favorite": False, "action": "removed"}
        else:
            new_id = str(uuid.uuid4())
            db.execute(
                text("""
                    INSERT INTO user_favorites (id, user_id, location_id, created_at)
                    VALUES (:id, :user_id, :location_id, NOW())
                """),
                {"id": new_id, "user_id": client_id, "location_id": location_id}
            )
            db.commit()
            return {"success": True, "is_favorite": True, "action": "added"}
    except Exception as e:
        logger.warning(f"[Favorites] toggle error for user {client_id}: {e}")
        db.rollback()
        return {"success": False, "error": "invalid_user_id", "message": "Cannot manage favorites for this user"}
