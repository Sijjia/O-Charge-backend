"""Favorites endpoint response schemas."""
from pydantic import BaseModel
from typing import List


class FavoritesListResponse(BaseModel):
    success: bool = True
    favorites: List[str] = []

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "favorites": ["loc-001", "loc-002"]}
    ]}}


class FavoriteAddResponse(BaseModel):
    success: bool = True
    already_exists: bool = False

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "already_exists": False}
    ]}}


class FavoriteCheckResponse(BaseModel):
    success: bool = True
    is_favorite: bool = False

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "is_favorite": True}
    ]}}


class FavoriteToggleResponse(BaseModel):
    success: bool = True
    is_favorite: bool = False
    action: str = ""

    model_config = {"json_schema_extra": {"examples": [
        {"success": True, "is_favorite": True, "action": "added"}
    ]}}
