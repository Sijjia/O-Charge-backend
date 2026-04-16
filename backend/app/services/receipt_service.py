"""
Сервис генерации PDF-чеков для зарядных сессий
Red Petroleum EV — PRD charging §4.4
"""
from io import BytesIO
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import text
import logging
import os

from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

logger = logging.getLogger(__name__)

# Ширина чека — узкий формат (80мм), как в кассовых чеках
RECEIPT_WIDTH = 80 * mm
RECEIPT_HEIGHT = 250 * mm

# Регистрация кириллического шрифта (DejaVu Sans — ставится через fonts-dejavu-core)
_FONT_REGISTERED = False

def _register_fonts():
    global _FONT_REGISTERED
    if _FONT_REGISTERED:
        return

    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]

    try:
        pdfmetrics.registerFont(TTFont("DejaVu", font_paths[0]))
        pdfmetrics.registerFont(TTFont("DejaVu-Bold", font_paths[1]))
        _FONT_REGISTERED = True
        logger.info("Шрифты DejaVu зарегистрированы для PDF-чеков")
    except Exception as e:
        logger.warning(f"DejaVu шрифты недоступны ({e}), fallback на Helvetica (без кириллицы)")


class ReceiptService:
    """Сервис генерации PDF-чеков"""

    def __init__(self, db: Session):
        self.db = db

    def generate_receipt(self, session_id: str) -> bytes:
        """Генерация PDF-чека для завершённой сессии зарядки."""
        data = self._get_session_data(session_id)
        pdf_bytes = self._build_pdf(data)

        self.db.execute(text("""
            UPDATE charging_sessions
            SET receipt_generated = true
            WHERE id = :session_id
        """), {"session_id": session_id})
        self.db.commit()

        return pdf_bytes

    def _get_session_data(self, session_id: str) -> dict:
        """Получение всех данных для чека одним запросом."""
        result = self.db.execute(text("""
            SELECT
                cs.id,
                cs.start_time,
                cs.stop_time,
                cs.energy,
                cs.amount,
                cs.tariff_rate,
                cs.night_tariff_applied,
                cs.connector_id,
                cs.status,
                l.name AS location_name,
                l.address AS location_address,
                s.id AS station_id,
                c2.phone AS client_phone,
                cn.connector_type
            FROM charging_sessions cs
            LEFT JOIN stations s ON cs.station_id = s.id
            LEFT JOIN locations l ON s.location_id = l.id
            LEFT JOIN clients c2 ON cs.user_id = c2.id
            LEFT JOIN connectors cn
                ON cs.station_id = cn.station_id
                AND cs.connector_id = cn.connector_number
            WHERE cs.id = :session_id
        """), {"session_id": session_id}).fetchone()

        if not result:
            raise ValueError(f"Сессия {session_id} не найдена")

        (
            sid, start_time, stop_time, energy, amount,
            tariff_rate, night_tariff, connector_id, status,
            location_name, location_address, station_id,
            client_phone, connector_type
        ) = result

        if status != "stopped":
            raise ValueError(f"Сессия {session_id} ещё не завершена (status={status})")

        return {
            "session_id": str(sid),
            "start_time": start_time,
            "stop_time": stop_time,
            "energy": float(energy) if energy else 0.0,
            "amount": float(amount) if amount else 0.0,
            "tariff_rate": float(tariff_rate) if tariff_rate else 0.0,
            "night_tariff_applied": bool(night_tariff),
            "connector_id": connector_id,
            "connector_type": connector_type or "Type 2",
            "location_name": location_name or "\u2014",
            "location_address": location_address or "\u2014",
            "station_id": station_id or "\u2014",
            "client_phone": client_phone or "",
        }

    def _mask_phone(self, phone: str) -> str:
        """Маскирование телефона: +996 555 *** **45"""
        if not phone:
            return "\u2014"
        digits = "".join(filter(str.isdigit, phone))
        if len(digits) < 7:
            return "***"
        return f"+{digits[:3]} {digits[3:6]} *** **{digits[-2:]}"

    def _format_datetime(self, dt) -> str:
        """Форматирование даты/времени в локальный формат (UTC+6 Бишкек)."""
        if not dt:
            return "\u2014"
        from datetime import timedelta
        local_dt = dt + timedelta(hours=6)
        return local_dt.strftime("%d.%m.%Y %H:%M")

    def _short_session_id(self, session_id: str) -> str:
        """Сокращённый ID сессии для чека."""
        return session_id[:8].upper() if session_id else "\u2014"

    def _build_pdf(self, data: dict) -> bytes:
        """Построение PDF-чека через reportlab с кириллицей."""
        _register_fonts()

        # Используем DejaVu если зарегистрирован, иначе fallback
        if _FONT_REGISTERED:
            font_name = "DejaVu"
            font_bold = "DejaVu-Bold"
        else:
            font_name = "Helvetica"
            font_bold = "Helvetica-Bold"

        buf = BytesIO()
        c = canvas.Canvas(buf, pagesize=(RECEIPT_WIDTH, RECEIPT_HEIGHT))

        x_left = 5 * mm
        x_right = RECEIPT_WIDTH - 5 * mm
        x_center = RECEIPT_WIDTH / 2
        y = RECEIPT_HEIGHT - 10 * mm

        def draw_centered(text_str, font=font_bold, size=10):
            nonlocal y
            c.setFont(font, size)
            c.drawCentredString(x_center, y, text_str)
            y -= size + 2 * mm

        def draw_line():
            nonlocal y
            c.setDash(1, 2)
            c.line(x_left, y, x_right, y)
            c.setDash()
            y -= 3 * mm

        def draw_row(label, value, font=font_name, size=9):
            nonlocal y
            c.setFont(font, size)
            c.drawString(x_left, y, label)
            c.drawRightString(x_right, y, str(value))
            y -= size + 2 * mm

        # === ЗАГОЛОВОК ===
        draw_centered("RED PETROLEUM EV", font_bold, 12)
        draw_centered("ЭЛЕКТРОННЫЙ ЧЕК", font_bold, 10)
        draw_line()

        # === ДАТА / ВРЕМЯ ===
        draw_row("Начало:", self._format_datetime(data["start_time"]))
        draw_row("Конец:", self._format_datetime(data["stop_time"]))
        draw_line()

        # === СТАНЦИЯ ===
        draw_row("Станция:", data["station_id"])
        c.setFont(font_name, 8)
        c.drawString(x_left, y, data["location_name"])
        y -= 10
        c.drawString(x_left, y, data["location_address"])
        y -= 5 * mm
        draw_line()

        # === КОННЕКТОР ===
        conn_id = data['connector_id'] or "\u2014"
        connector_str = f"#{conn_id} ({data['connector_type']})"
        draw_row("Коннектор:", connector_str)

        # === ЭНЕРГИЯ ===
        draw_row("Энергия:", f"{data['energy']:.3f} кВт\u00b7ч")

        # === ТАРИФ ===
        draw_row("Тариф:", f"{data['tariff_rate']:.2f} сом/кВт\u00b7ч")

        # === НОЧНАЯ СКИДКА ===
        if data["night_tariff_applied"]:
            draw_row("Ночная скидка:", "-20%")

        draw_line()

        # === ИТОГО ===
        draw_row("ИТОГО:", f"{data['amount']:.2f} сом", font=font_bold, size=11)

        # === СПОСОБ ОПЛАТЫ ===
        draw_row("Оплата:", "Баланс")

        draw_line()

        # === СЕССИЯ / КЛИЕНТ ===
        draw_row("Сессия:", self._short_session_id(data["session_id"]))
        draw_row("Телефон:", self._mask_phone(data["client_phone"]))

        draw_line()

        # === ПОДВАЛ ===
        draw_centered("Спасибо за зарядку!", font_name, 9)
        draw_centered("red-petroleum.kg", font_name, 8)

        c.showPage()
        c.save()
        buf.seek(0)
        return buf.read()
