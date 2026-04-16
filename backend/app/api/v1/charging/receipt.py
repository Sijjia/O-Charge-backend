"""
API endpoint для скачивания PDF-чека зарядной сессии
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import text
from io import BytesIO
import logging

from app.db.session import get_db
from app.services.receipt_service import ReceiptService
from app.api.v1.schemas.common import ErrorResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get(
    "/charging/receipt/{session_id}",
    summary="Download charging receipt (PDF)",
    description="Generates and returns a PDF receipt for a completed charging session.",
    responses={
        401: {"description": "Not authenticated", "model": ErrorResponse},
        404: {"description": "Session not found", "model": ErrorResponse},
    },
)
async def get_receipt(
    session_id: str,
    db: Session = Depends(get_db),
    http_request: Request = None
):
    """Скачать PDF-чек для завершённой сессии зарядки"""

    # 1. Аутентификация
    client_id = getattr(http_request.state, "client_id", None)
    if not client_id:
        return {
            "success": False,
            "error": "unauthorized",
            "message": "Missing or invalid authentication"
        }

    # 2. Проверка владельца сессии
    session_check = db.execute(text("""
        SELECT user_id, status FROM charging_sessions WHERE id = :session_id
    """), {"session_id": session_id}).fetchone()

    if not session_check:
        return {
            "success": False,
            "error": "session_not_found",
            "message": "Сессия не найдена"
        }

    if session_check[0] != client_id:
        return {
            "success": False,
            "error": "access_denied",
            "message": "У вас нет доступа к этой сессии"
        }

    if session_check[1] != "stopped":
        return {
            "success": False,
            "error": "session_not_completed",
            "message": "Чек доступен только для завершённых сессий"
        }

    # 3. Генерация PDF
    try:
        service = ReceiptService(db)
        pdf_bytes = service.generate_receipt(session_id)

        short_id = session_id[:8]
        return StreamingResponse(
            BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="receipt_{short_id}.pdf"'
            }
        )
    except ValueError as e:
        logger.warning(f"Receipt generation failed for session {session_id}: {e}")
        return {
            "success": False,
            "error": "generation_failed",
            "message": str(e)
        }
    except Exception as e:
        logger.error(f"Receipt generation error for session {session_id}: {e}", exc_info=True)
        return {
            "success": False,
            "error": "internal_error",
            "message": "Ошибка генерации чека"
        }
