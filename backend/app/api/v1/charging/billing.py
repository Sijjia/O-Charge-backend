"""
Биллинг: резервирование, расчёт стоимости, refund/charge, финализация сессии
"""
from typing import Optional, Dict, Any
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import text
import logging

from app.crud.ocpp_service import payment_service
from app.services.pricing_service import PricingService

logger = logging.getLogger(__name__)

# Множители резервирования
RESERVE_MULTIPLIER_ENERGY = 0.95    # Лимит по энергии — 95%
RESERVE_MULTIPLIER_AMOUNT = 0.95    # Лимит по сумме — 95%

# Безлимитная зарядка (полный бак):
# Резервируем небольшую сумму, остаток доснимаем по факту при остановке
UNLIMITED_RESERVE_AMOUNT = 200  # сом — начальный резерв для полного бака


class ChargingBilling:
    """Биллинг операций зарядки"""

    def __init__(self, db: Session):
        self.db = db
        self.pricing_service = PricingService(db)

    def calculate_reservation(
        self,
        balance: Decimal,
        pricing_result,
        energy_kwh: Optional[float],
        amount_som: Optional[float],
        promo_code: Optional[str] = None,
        client_id: Optional[str] = None,
        estimated_duration: int = 60
    ) -> Dict[str, Any]:
        """Расчет суммы резервирования и лимитов.

        Режимы:
        - energy: reserved = estimated_cost × 0.95
        - amount: reserved = amount × 0.95
        - unlimited (полный бак): reserved = min(200, баланс), остаток доснимается при стопе
        """
        session_cost = None
        if energy_kwh:
            session_cost = self.pricing_service.calculate_session_cost(
                energy_kwh=energy_kwh,
                duration_minutes=estimated_duration,
                pricing=pricing_result,
                promo_code=promo_code,
                client_id=client_id
            )
            estimated_cost = Decimal(str(session_cost.final_amount))
            base_amount = Decimal(str(session_cost.base_amount))
            discount_amount = Decimal(str(session_cost.discount_amount))
        else:
            estimated_cost = Decimal(str(pricing_result.session_fee))
            if pricing_result.rate_per_minute > 0:
                estimated_cost += Decimal(str(pricing_result.rate_per_minute)) * estimated_duration
            base_amount = estimated_cost
            discount_amount = Decimal('0')

        if energy_kwh and amount_som:
            amount_som_d = Decimal(str(amount_som))
            full_amount = min(estimated_cost, amount_som_d)
            reservation_amount = full_amount * Decimal(str(RESERVE_MULTIPLIER_ENERGY))
            limit_type = 'energy'
            limit_value = energy_kwh

        elif amount_som:
            amount_som_d = Decimal(str(amount_som))
            if amount_som_d > balance:
                return {
                    "success": False,
                    "error": "amount_exceeds_balance",
                    "message": f"Указанная сумма ({amount_som} сом) превышает баланс ({balance} сом)",
                    "current_balance": float(balance),
                    "requested_amount": amount_som
                }
            reservation_amount = amount_som_d * Decimal(str(RESERVE_MULTIPLIER_AMOUNT))
            limit_type = 'amount'
            limit_value = amount_som

        elif energy_kwh:
            full_amount = (Decimal(str(energy_kwh)) * Decimal(str(pricing_result.rate_per_kwh))) + Decimal(str(pricing_result.session_fee))
            if pricing_result.rate_per_minute > 0:
                full_amount += estimated_duration * Decimal(str(pricing_result.rate_per_minute))
            reservation_amount = full_amount * Decimal(str(RESERVE_MULTIPLIER_ENERGY))
            limit_type = 'energy'
            limit_value = energy_kwh

        else:
            # Безлимитная зарядка (полный бак)
            # Резервируем 200 сом, остаток доснимаем при остановке
            if balance <= 0:
                return {
                    "success": False,
                    "error": "zero_balance",
                    "message": "Недостаточно средств для зарядки",
                    "current_balance": float(balance)
                }

            reservation_amount = min(Decimal(str(UNLIMITED_RESERVE_AMOUNT)), balance)

            min_reservation = Decimal('10')
            if reservation_amount < min_reservation:
                return {
                    "success": False,
                    "error": "insufficient_balance",
                    "message": f"Минимальный баланс для зарядки: {min_reservation} сом",
                    "current_balance": float(balance),
                    "required_amount": float(min_reservation)
                }

            limit_type = 'none'
            limit_value = 0

        if balance < reservation_amount:
            return {
                "success": False,
                "error": "insufficient_balance",
                "message": f"Недостаточно средств. Баланс: {balance} сом, требуется: {float(reservation_amount):.2f} сом",
                "current_balance": float(balance),
                "required_amount": float(reservation_amount)
            }

        return {
            "success": True,
            "amount": float(reservation_amount),
            "limit_type": limit_type,
            "limit_value": limit_value,
            "base_amount": float(base_amount),
            "discount_amount": float(discount_amount)
        }

    def reserve_funds(self, client_id: str, amount: float, station_id: str) -> Decimal:
        """Резервирование средств на балансе"""
        return payment_service.update_client_balance(
            self.db, client_id, Decimal(str(amount)), "subtract",
            f"Резервирование средств для зарядки на станции {station_id}"
        )

    def get_actual_energy_consumed(self, session_id: str, session_energy: Optional[float]) -> float:
        """Получение фактически потребленной энергии

        Приоритет источников данных:
        1. charging_sessions.energy (если > 0)
        2. ocpp_meter_values.energy_active_import_register - meter_start (последние показания)
        3. ocpp_transactions.meter_stop - meter_start (если станция прислала StopTransaction)
        4. 0.0 (fallback)
        """
        if session_energy and float(session_energy) > 0:
            return float(session_energy)

        result = self.db.execute(text("""
            SELECT COALESCE(
                (mv.energy_active_import_register - ot.meter_start) / 1000.0,
                (ot.meter_stop - ot.meter_start) / 1000.0,
                0
            ) as energy_kwh
            FROM ocpp_transactions ot
            LEFT JOIN LATERAL (
                SELECT energy_active_import_register
                FROM ocpp_meter_values
                WHERE ocpp_transaction_id = ot.id
                ORDER BY timestamp DESC
                LIMIT 1
            ) mv ON true
            WHERE ot.charging_session_id = :session_id
            ORDER BY ot.created_at DESC
            LIMIT 1
        """), {"session_id": session_id}).fetchone()

        energy = float(result[0]) if result and result[0] else 0.0
        logger.info(f"Фактическое потребление для сессии {session_id}: {energy:.3f} кВт*ч")
        return energy

    def get_session_rate(self, session_info: Dict[str, Any]) -> float:
        """Получение тарифа для сессии"""
        if session_info['price_per_kwh']:
            return float(session_info['price_per_kwh'])

        if session_info['tariff_plan_id']:
            result = self.db.execute(text("""
                SELECT price FROM tariff_rules
                WHERE tariff_plan_id = :tariff_plan_id
                AND tariff_type = 'per_kwh'
                AND is_active = true
                ORDER BY priority DESC LIMIT 1
            """), {"tariff_plan_id": session_info['tariff_plan_id']}).fetchone()

            if result:
                return float(result[0])

        return 13.5  # Default rate

    def calculate_refund_or_charge(
        self,
        client_id: str,
        actual_cost: Decimal,
        reserved_amount: Decimal,
        session_id: str
    ) -> tuple[Decimal, Decimal]:
        """Расчет возврата или дополнительного списания"""
        additional_charge = Decimal('0')
        refund_amount = Decimal('0')

        if actual_cost > reserved_amount:
            additional_charge = actual_cost - reserved_amount
            current_balance = payment_service.get_client_balance(self.db, client_id)

            if current_balance < additional_charge:
                logger.warning(f"Недостаток средств для доплаты в сессии {session_id}")
                additional_charge = current_balance
        else:
            refund_amount = reserved_amount - actual_cost

        return refund_amount, additional_charge

    def process_session_payment(
        self,
        client_id: str,
        refund_amount: Decimal,
        additional_charge: Decimal,
        session_id: str,
        energy_consumed: float
    ) -> Decimal:
        """Обработка платежей сессии"""
        current_balance = payment_service.get_client_balance(self.db, client_id)

        if additional_charge > 0:
            new_balance = payment_service.update_client_balance(
                self.db, client_id, additional_charge, "subtract",
                f"Дополнительное списание за превышение резерва в сессии {session_id}"
            )

            payment_service.create_payment_transaction(
                self.db, client_id, "charge_payment",
                -additional_charge, current_balance, new_balance,
                f"Доплата за сессию {session_id}",
                charging_session_id=session_id
            )
        elif refund_amount > 0:
            new_balance = payment_service.update_client_balance(
                self.db, client_id, refund_amount, "add",
                f"Возврат неиспользованных средств за сессию {session_id}"
            )

            payment_service.create_payment_transaction(
                self.db, client_id, "charge_refund",
                refund_amount, current_balance, new_balance,
                f"Возврат за сессию {session_id}: потреблено {energy_consumed} кВт*ч",
                charging_session_id=session_id
            )
        else:
            new_balance = current_balance

        return new_balance

    def finalize_session(self, session_id: str, actual_energy: float, actual_cost: float):
        """Финализация сессии в БД"""
        self.db.execute(text("""
            UPDATE charging_sessions
            SET stop_time = NOW(), status = 'stopped',
                energy = :actual_energy, amount = :actual_cost,
                payment_processed = TRUE
            WHERE id = :session_id
        """), {
            "actual_energy": actual_energy,
            "actual_cost": actual_cost,
            "session_id": session_id
        })
