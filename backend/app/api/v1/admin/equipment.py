"""
Admin API: Equipment Catalog — справочник оборудования ЭЗС (производители + модели)
"""
from fastapi import APIRouter, Depends, HTTPException, Request, Query, UploadFile, File, Form
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Optional
import logging
import os
import uuid
import httpx

from app.db.session import get_db
from .operators import get_current_admin, get_current_owner
from app.api.v1.schemas.admin.equipment import (
    ManufacturerListResponse,
    ManufacturerDetailResponse,
    ModelListResponse,
    ModelDetailResponse,
    EquipmentMutationResponse,
)
from app.api.v1.schemas.common import ADMIN_RESPONSES

router = APIRouter(prefix="/equipment")
logger = logging.getLogger(__name__)


# ─── Manufacturers ────────────────────────────────────────────────────

@router.get("/manufacturers", response_model=ManufacturerListResponse, summary="List manufacturers", responses=ADMIN_RESPONSES)
async def list_manufacturers(
    request: Request,
    db: Session = Depends(get_db),
    search: Optional[str] = Query(None, max_length=100),
    is_active: Optional[bool] = Query(None),
):
    """Список производителей оборудования с количеством моделей"""
    get_current_owner(request, db)

    where_clauses = ["1=1"]
    params: dict = {}

    if search:
        where_clauses.append("(m.name ILIKE :search OR m.name_cn ILIKE :search OR m.country ILIKE :search)")
        params["search"] = f"%{search}%"
    if is_active is not None:
        where_clauses.append("m.is_active = :is_active")
        params["is_active"] = is_active

    where_sql = " AND ".join(where_clauses)

    rows = db.execute(text(f"""
        SELECT m.*,
               COUNT(em.id) FILTER (WHERE em.is_active) as model_count
        FROM equipment_manufacturers m
        LEFT JOIN equipment_models em ON em.manufacturer_id = m.id
        WHERE {where_sql}
        GROUP BY m.id
        ORDER BY m.name
    """), params).fetchall()

    data = []
    for r in rows:
        data.append({
            "id": r.id,
            "name": r.name,
            "name_cn": r.name_cn,
            "country": r.country,
            "website": r.website,
            "logo_url": r.logo_url,
            "description": r.description,
            "is_active": r.is_active,
            "model_count": r.model_count,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })

    return {"success": True, "data": data, "total": len(data)}


@router.get("/manufacturers/{manufacturer_id}", response_model=ManufacturerDetailResponse, summary="Get manufacturer details", responses=ADMIN_RESPONSES)
async def get_manufacturer(
    manufacturer_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Детали производителя + список его моделей"""
    get_current_owner(request, db)

    mfr = db.execute(text("""
        SELECT m.*,
               COUNT(em.id) FILTER (WHERE em.is_active) as model_count
        FROM equipment_manufacturers m
        LEFT JOIN equipment_models em ON em.manufacturer_id = m.id
        WHERE m.id = :id
        GROUP BY m.id
    """), {"id": manufacturer_id}).fetchone()

    if not mfr:
        raise HTTPException(status_code=404, detail="Производитель не найден")

    models = db.execute(text("""
        SELECT * FROM equipment_models
        WHERE manufacturer_id = :mfr_id AND is_active = true
        ORDER BY power_kw DESC NULLS LAST
    """), {"mfr_id": manufacturer_id}).fetchall()

    return {
        "success": True,
        "manufacturer": {
            "id": mfr.id,
            "name": mfr.name,
            "name_cn": mfr.name_cn,
            "country": mfr.country,
            "website": mfr.website,
            "logo_url": mfr.logo_url,
            "description": mfr.description,
            "is_active": mfr.is_active,
            "model_count": mfr.model_count,
            "created_at": mfr.created_at.isoformat() if mfr.created_at else None,
        },
        "models": [_model_to_dict(m) for m in models],
    }


@router.post("/manufacturers", response_model=EquipmentMutationResponse, summary="Create manufacturer", responses=ADMIN_RESPONSES)
async def create_manufacturer(
    request: Request,
    db: Session = Depends(get_db),
):
    """Создать нового производителя"""
    get_current_admin(request, db)
    body = await request.json()

    name = body.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="name обязательно")

    existing = db.execute(
        text("SELECT id FROM equipment_manufacturers WHERE name = :name"),
        {"name": name},
    ).fetchone()
    if existing:
        raise HTTPException(status_code=409, detail="Производитель с таким именем уже существует")

    row = db.execute(text("""
        INSERT INTO equipment_manufacturers (name, name_cn, country, website, logo_url, description)
        VALUES (:name, :name_cn, :country, :website, :logo_url, :description)
        RETURNING id
    """), {
        "name": name,
        "name_cn": body.get("name_cn"),
        "country": body.get("country"),
        "website": body.get("website"),
        "logo_url": body.get("logo_url"),
        "description": body.get("description"),
    }).fetchone()

    db.commit()
    logger.info(f"[Equipment] Manufacturer created: {name}")
    return {"success": True, "id": row.id, "message": "Производитель создан"}


@router.put("/manufacturers/{manufacturer_id}", response_model=EquipmentMutationResponse, summary="Update manufacturer", responses=ADMIN_RESPONSES)
async def update_manufacturer(
    manufacturer_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Обновить производителя"""
    get_current_admin(request, db)
    body = await request.json()

    existing = db.execute(
        text("SELECT id FROM equipment_manufacturers WHERE id = :id"),
        {"id": manufacturer_id},
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Производитель не найден")

    allowed_fields = ["name", "name_cn", "country", "website", "logo_url", "description", "is_active"]
    set_clauses = []
    params = {"id": manufacturer_id}

    for field in allowed_fields:
        if field in body:
            set_clauses.append(f"{field} = :{field}")
            params[field] = body[field]

    if not set_clauses:
        raise HTTPException(status_code=400, detail="Нет полей для обновления")

    set_clauses.append("updated_at = NOW()")
    db.execute(
        text(f"UPDATE equipment_manufacturers SET {', '.join(set_clauses)} WHERE id = :id"),
        params,
    )
    db.commit()
    logger.info(f"[Equipment] Manufacturer updated: {manufacturer_id}")
    return {"success": True, "id": manufacturer_id, "message": "Производитель обновлён"}


@router.delete("/manufacturers/{manufacturer_id}", response_model=EquipmentMutationResponse, summary="Deactivate manufacturer", responses=ADMIN_RESPONSES)
async def delete_manufacturer(
    manufacturer_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Деактивировать производителя (soft delete)"""
    get_current_admin(request, db)

    existing = db.execute(
        text("SELECT id FROM equipment_manufacturers WHERE id = :id"),
        {"id": manufacturer_id},
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Производитель не найден")

    db.execute(text("""
        UPDATE equipment_manufacturers SET is_active = false, updated_at = NOW() WHERE id = :id
    """), {"id": manufacturer_id})
    db.commit()
    logger.info(f"[Equipment] Manufacturer deactivated: {manufacturer_id}")
    return {"success": True, "id": manufacturer_id, "message": "Производитель деактивирован"}


# ─── Models ───────────────────────────────────────────────────────────

@router.get("/models", response_model=ModelListResponse, summary="List equipment models", responses=ADMIN_RESPONSES)
async def list_models(
    request: Request,
    db: Session = Depends(get_db),
    manufacturer_id: Optional[str] = Query(None),
    type: Optional[str] = Query(None, pattern="^(AC|DC)$"),
    search: Optional[str] = Query(None, max_length=100),
    is_active: Optional[bool] = Query(None),
):
    """Список моделей оборудования с фильтрацией"""
    get_current_owner(request, db)

    where_clauses = ["1=1"]
    params: dict = {}

    if manufacturer_id:
        where_clauses.append("em.manufacturer_id = :manufacturer_id")
        params["manufacturer_id"] = manufacturer_id
    if type:
        where_clauses.append("em.type = :type")
        params["type"] = type
    if search:
        where_clauses.append("(em.name ILIKE :search OR m.name ILIKE :search)")
        params["search"] = f"%{search}%"
    if is_active is not None:
        where_clauses.append("em.is_active = :is_active")
        params["is_active"] = is_active

    where_sql = " AND ".join(where_clauses)

    rows = db.execute(text(f"""
        SELECT em.*, m.name as manufacturer_name
        FROM equipment_models em
        JOIN equipment_manufacturers m ON m.id = em.manufacturer_id
        WHERE {where_sql}
        ORDER BY m.name, em.power_kw DESC NULLS LAST
    """), params).fetchall()

    return {"success": True, "data": [_model_to_dict(r) for r in rows], "total": len(rows)}


@router.get("/models/{model_id}", response_model=ModelDetailResponse, summary="Get model details", responses=ADMIN_RESPONSES)
async def get_model(
    model_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Детали модели оборудования"""
    get_current_owner(request, db)

    row = db.execute(text("""
        SELECT em.*, m.name as manufacturer_name
        FROM equipment_models em
        JOIN equipment_manufacturers m ON m.id = em.manufacturer_id
        WHERE em.id = :id
    """), {"id": model_id}).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Модель не найдена")

    return {"success": True, "model": _model_to_dict(row)}


@router.post("/models", response_model=EquipmentMutationResponse, summary="Create equipment model", responses=ADMIN_RESPONSES)
async def create_model(
    request: Request,
    db: Session = Depends(get_db),
):
    """Создать новую модель оборудования"""
    get_current_admin(request, db)
    body = await request.json()

    manufacturer_id = body.get("manufacturer_id")
    name = body.get("name")
    if not manufacturer_id or not name:
        raise HTTPException(status_code=400, detail="manufacturer_id и name обязательны")

    mfr = db.execute(
        text("SELECT id FROM equipment_manufacturers WHERE id = :id"),
        {"id": manufacturer_id},
    ).fetchone()
    if not mfr:
        raise HTTPException(status_code=404, detail="Производитель не найден")

    existing = db.execute(
        text("SELECT id FROM equipment_models WHERE manufacturer_id = :mfr_id AND name = :name"),
        {"mfr_id": manufacturer_id, "name": name},
    ).fetchone()
    if existing:
        raise HTTPException(status_code=409, detail="Модель с таким именем уже существует у этого производителя")

    row = db.execute(text("""
        INSERT INTO equipment_models (
            manufacturer_id, name, type, power_kw, connector_types, num_connectors,
            voltage_range, image_url, price_min_usd, price_max_usd, ocpp_versions,
            ip_rating, dimensions, weight_kg, operating_temp, efficiency_percent, display_size
        ) VALUES (
            :manufacturer_id, :name, :type, :power_kw, :connector_types, :num_connectors,
            :voltage_range, :image_url, :price_min_usd, :price_max_usd, :ocpp_versions,
            :ip_rating, :dimensions, :weight_kg, :operating_temp, :efficiency_percent, :display_size
        )
        RETURNING id
    """), {
        "manufacturer_id": manufacturer_id,
        "name": name,
        "type": body.get("type", "DC"),
        "power_kw": body.get("power_kw"),
        "connector_types": body.get("connector_types", []),
        "num_connectors": body.get("num_connectors"),
        "voltage_range": body.get("voltage_range"),
        "image_url": body.get("image_url"),
        "price_min_usd": body.get("price_min_usd"),
        "price_max_usd": body.get("price_max_usd"),
        "ocpp_versions": body.get("ocpp_versions", ["1.6"]),
        "ip_rating": body.get("ip_rating"),
        "dimensions": body.get("dimensions"),
        "weight_kg": body.get("weight_kg"),
        "operating_temp": body.get("operating_temp"),
        "efficiency_percent": body.get("efficiency_percent"),
        "display_size": body.get("display_size"),
    }).fetchone()

    db.commit()
    logger.info(f"[Equipment] Model created: {name}")
    return {"success": True, "id": row.id, "message": "Модель создана"}


@router.put("/models/{model_id}", response_model=EquipmentMutationResponse, summary="Update equipment model", responses=ADMIN_RESPONSES)
async def update_model(
    model_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Обновить модель оборудования"""
    get_current_admin(request, db)
    body = await request.json()

    existing = db.execute(
        text("SELECT id FROM equipment_models WHERE id = :id"),
        {"id": model_id},
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Модель не найдена")

    allowed_fields = [
        "name", "type", "power_kw", "connector_types", "num_connectors",
        "voltage_range", "image_url", "price_min_usd", "price_max_usd",
        "ocpp_versions", "ip_rating", "dimensions", "weight_kg",
        "operating_temp", "efficiency_percent", "display_size", "is_active",
    ]
    set_clauses = []
    params = {"id": model_id}

    for field in allowed_fields:
        if field in body:
            set_clauses.append(f"{field} = :{field}")
            params[field] = body[field]

    if not set_clauses:
        raise HTTPException(status_code=400, detail="Нет полей для обновления")

    set_clauses.append("updated_at = NOW()")
    db.execute(
        text(f"UPDATE equipment_models SET {', '.join(set_clauses)} WHERE id = :id"),
        params,
    )
    db.commit()
    logger.info(f"[Equipment] Model updated: {model_id}")
    return {"success": True, "id": model_id, "message": "Модель обновлена"}


@router.delete("/models/{model_id}", response_model=EquipmentMutationResponse, summary="Deactivate equipment model", responses=ADMIN_RESPONSES)
async def delete_model(
    model_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Деактивировать модель оборудования (soft delete)"""
    get_current_admin(request, db)

    existing = db.execute(
        text("SELECT id FROM equipment_models WHERE id = :id"),
        {"id": model_id},
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Модель не найдена")

    db.execute(text("""
        UPDATE equipment_models SET is_active = false, updated_at = NOW() WHERE id = :id
    """), {"id": model_id})
    db.commit()
    logger.info(f"[Equipment] Model deactivated: {model_id}")
    return {"success": True, "id": model_id, "message": "Модель деактивирована"}


# ─── Image Upload ─────────────────────────────────────────────────────

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://supabase.asystem.kg")
# Internal URL for Docker-to-Docker communication (via Coolify reverse proxy)
SUPABASE_INTERNAL_URL = os.getenv("SUPABASE_INTERNAL_URL", "https://coolify-proxy")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
STORAGE_BUCKET = "equipment"


@router.post("/upload-image", summary="Upload equipment image to storage", responses=ADMIN_RESPONSES)
async def upload_image(
    request: Request,
    db: Session = Depends(get_db),
    file: UploadFile = File(...),
    folder: str = Form("misc"),
):
    """
    Upload an image to Supabase Storage (equipment bucket).
    Image is expected to be pre-compressed on the client side.
    Returns the public URL.
    """
    get_current_admin(request, db)

    if not SUPABASE_SERVICE_KEY:
        raise HTTPException(status_code=500, detail="Storage not configured")

    # Validate file type
    ct = file.content_type or ""
    if not ct.startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image files are allowed")

    # Read file (max 5MB)
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 5MB)")

    # Generate unique filename
    ext_map = {"image/webp": ".webp", "image/png": ".png", "image/jpeg": ".jpg", "image/svg+xml": ".svg"}
    ext = ext_map.get(ct, ".webp")
    filename = f"{uuid.uuid4().hex[:12]}{ext}"
    path = f"{folder}/{filename}"

    # Upload to Supabase Storage (use internal proxy for Docker-to-Docker)
    storage_url = f"{SUPABASE_INTERNAL_URL}/storage/v1/object/{STORAGE_BUCKET}/{path}"
    # Extract hostname from SUPABASE_URL for Host header (needed by reverse proxy)
    supabase_host = SUPABASE_URL.replace("https://", "").replace("http://", "").split("/")[0]
    headers = {
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "apikey": SUPABASE_SERVICE_KEY,
        "Content-Type": ct,
        "x-upsert": "true",
        "Host": supabase_host,
    }

    async with httpx.AsyncClient(verify=False) as client:
        resp = await client.post(storage_url, content=content, headers=headers, timeout=30)

    if resp.status_code not in (200, 201):
        logger.error(f"[Equipment] Storage upload failed: {resp.status_code} {resp.text[:200]}")
        raise HTTPException(status_code=502, detail="Failed to upload to storage")

    public_url = f"{SUPABASE_URL}/storage/v1/object/public/{STORAGE_BUCKET}/{path}"
    logger.info(f"[Equipment] Image uploaded: {public_url} ({len(content)} bytes)")

    return {"success": True, "url": public_url, "size": len(content), "message": "Image uploaded"}


# ─── Helpers ──────────────────────────────────────────────────────────

def _pg_array_to_list(val) -> list:
    """Convert PostgreSQL array result to Python list."""
    if val is None:
        return []
    if isinstance(val, list):
        return val
    # psycopg2 returns lists natively; fallback for edge cases
    return list(val)


def _model_to_dict(r) -> dict:
    """Convert a model row to a dict."""
    return {
        "id": r.id,
        "manufacturer_id": r.manufacturer_id,
        "manufacturer_name": getattr(r, "manufacturer_name", None),
        "name": r.name,
        "type": r.type,
        "power_kw": float(r.power_kw) if r.power_kw else None,
        "connector_types": _pg_array_to_list(r.connector_types),
        "num_connectors": r.num_connectors,
        "voltage_range": r.voltage_range,
        "image_url": r.image_url,
        "price_min_usd": r.price_min_usd,
        "price_max_usd": r.price_max_usd,
        "ocpp_versions": _pg_array_to_list(r.ocpp_versions),
        "ip_rating": r.ip_rating,
        "dimensions": r.dimensions,
        "weight_kg": float(r.weight_kg) if r.weight_kg else None,
        "operating_temp": r.operating_temp,
        "efficiency_percent": float(r.efficiency_percent) if r.efficiency_percent else None,
        "display_size": r.display_size,
        "is_active": r.is_active,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }
