"""
Corporate Clients Service — бизнес-логика корпоративного модуля.
- Управление группами, сотрудниками, лимитами
- Проверка разрешений на зарядку
- Резервирование/возврат средств
- Отчёты и аналитика
"""
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, date, timezone
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import text
import uuid
import logging

logger = logging.getLogger(__name__)


class CorporateService:
    def __init__(self, db: Session):
        self.db = db

    # ============================================
    # Groups CRUD
    # ============================================

    def create_group(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Создать корпоративную группу."""
        group_id = str(uuid.uuid4())
        self.db.execute(text("""
            INSERT INTO corporate_groups (
                id, company_name, inn, legal_address,
                contact_person, contact_phone, contact_email,
                billing_type, monthly_limit, credit_limit,
                contract_number, contract_date, contract_expires
            ) VALUES (
                :id, :company_name, :inn, :legal_address,
                :contact_person, :contact_phone, :contact_email,
                :billing_type, :monthly_limit, :credit_limit,
                :contract_number, :contract_date, :contract_expires
            )
        """), {
            "id": group_id,
            "company_name": data["company_name"],
            "inn": data.get("inn"),
            "legal_address": data.get("legal_address"),
            "contact_person": data.get("contact_person"),
            "contact_phone": data.get("contact_phone"),
            "contact_email": data.get("contact_email"),
            "billing_type": data.get("billing_type", "prepaid"),
            "monthly_limit": data.get("monthly_limit"),
            "credit_limit": data.get("credit_limit", 0),
            "contract_number": data.get("contract_number"),
            "contract_date": data.get("contract_date"),
            "contract_expires": data.get("contract_expires"),
        })
        self.db.commit()
        return {"id": group_id, "company_name": data["company_name"]}

    def get_group(self, group_id: str) -> Optional[Dict[str, Any]]:
        """Получить группу по ID."""
        row = self.db.execute(text("""
            SELECT id, company_name, inn, legal_address,
                   contact_person, contact_phone, contact_email,
                   balance, credit_limit, monthly_limit, billing_type,
                   current_month_spent, contract_number, contract_date,
                   contract_expires, is_active, blocked_reason,
                   created_at, updated_at
            FROM corporate_groups WHERE id = :id
        """), {"id": group_id}).fetchone()
        if not row:
            return None
        return self._row_to_group(row)

    def list_groups(self, active_only: bool = True) -> List[Dict[str, Any]]:
        """Список корпоративных групп."""
        where = "WHERE g.is_active = true" if active_only else ""
        rows = self.db.execute(text(f"""
            SELECT g.id, g.company_name, g.inn, g.legal_address,
                   g.contact_person, g.contact_phone, g.contact_email,
                   g.balance, g.credit_limit, g.monthly_limit, g.billing_type,
                   g.current_month_spent, g.contract_number, g.contract_date,
                   g.contract_expires, g.is_active, g.blocked_reason,
                   g.created_at, g.updated_at,
                   COUNT(ce.id) FILTER (WHERE ce.is_active = true) AS employees_count
            FROM corporate_groups g
            LEFT JOIN corporate_employees ce ON ce.corporate_group_id = g.id
            {where}
            GROUP BY g.id
            ORDER BY g.company_name
        """)).fetchall()

        result = []
        for r in rows:
            g = self._row_to_group(r)
            g["employees_count"] = r[19]
            result.append(g)
        return result

    def update_group(self, group_id: str, data: Dict[str, Any]) -> bool:
        """Обновить данные группы."""
        updates = []
        params = {"id": group_id}
        allowed = [
            "company_name", "inn", "legal_address",
            "contact_person", "contact_phone", "contact_email",
            "monthly_limit", "credit_limit", "billing_type",
            "contract_number", "contract_date", "contract_expires",
        ]
        for key in allowed:
            if key in data:
                updates.append(f"{key} = :{key}")
                params[key] = data[key]

        if not updates:
            return False

        updates.append("updated_at = NOW()")
        self.db.execute(text(f"""
            UPDATE corporate_groups SET {', '.join(updates)} WHERE id = :id
        """), params)
        self.db.commit()
        return True

    # ============================================
    # Balance Operations
    # ============================================

    def topup_balance(self, group_id: str, amount: float, description: str = "") -> Dict[str, Any]:
        """Пополнить баланс (prepaid)."""
        group = self.get_group(group_id)
        if not group:
            raise ValueError("Group not found")

        balance_before = float(group["balance"])
        balance_after = balance_before + amount

        self.db.execute(text("""
            UPDATE corporate_groups SET balance = balance + :amount, updated_at = NOW()
            WHERE id = :id
        """), {"id": group_id, "amount": amount})

        self.db.execute(text("""
            INSERT INTO corporate_transactions (id, corporate_group_id, type, amount, balance_before, balance_after, description)
            VALUES (:id, :group_id, 'topup', :amount, :before, :after, :desc)
        """), {
            "id": str(uuid.uuid4()),
            "group_id": group_id,
            "amount": amount,
            "before": balance_before,
            "after": balance_after,
            "desc": description or f"Пополнение на {amount} сом",
        })
        self.db.commit()

        return {"balance_before": balance_before, "balance_after": balance_after}

    def block_group(self, group_id: str, reason: str) -> bool:
        """Заблокировать корпоративный аккаунт."""
        self.db.execute(text("""
            UPDATE corporate_groups
            SET is_active = false, blocked_reason = :reason, updated_at = NOW()
            WHERE id = :id
        """), {"id": group_id, "reason": reason})
        self.db.commit()
        return True

    def unblock_group(self, group_id: str) -> bool:
        """Разблокировать корпоративный аккаунт."""
        self.db.execute(text("""
            UPDATE corporate_groups
            SET is_active = true, blocked_reason = NULL, updated_at = NOW()
            WHERE id = :id
        """), {"id": group_id})
        self.db.commit()
        return True

    # ============================================
    # Employees CRUD
    # ============================================

    def add_employee(self, group_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Добавить сотрудника в группу."""
        emp_id = str(uuid.uuid4())
        self.db.execute(text("""
            INSERT INTO corporate_employees (
                id, corporate_group_id, user_id, role, position,
                monthly_limit, daily_limit
            ) VALUES (
                :id, :group_id, :user_id, :role, :position,
                :monthly_limit, :daily_limit
            )
        """), {
            "id": emp_id,
            "group_id": group_id,
            "user_id": data["user_id"],
            "role": data.get("role", "employee"),
            "position": data.get("position"),
            "monthly_limit": data.get("monthly_limit"),
            "daily_limit": data.get("daily_limit"),
        })
        self.db.commit()
        return {"id": emp_id}

    def get_employees(self, group_id: str, active_only: bool = True) -> List[Dict[str, Any]]:
        """Список сотрудников группы."""
        where_active = "AND ce.is_active = true" if active_only else ""
        rows = self.db.execute(text(f"""
            SELECT ce.id, ce.user_id, ce.role, ce.position,
                   ce.monthly_limit, ce.daily_limit,
                   ce.current_month_spent, ce.current_day_spent,
                   ce.is_active, ce.created_at,
                   c.phone, c.first_name, c.last_name
            FROM corporate_employees ce
            LEFT JOIN clients c ON ce.user_id = c.id
            WHERE ce.corporate_group_id = :group_id {where_active}
            ORDER BY ce.role DESC, ce.created_at
        """), {"group_id": group_id}).fetchall()

        employees = []
        for r in rows:
            monthly_limit = float(r[4]) if r[4] else None
            monthly_spent = float(r[6] or 0)
            employees.append({
                "id": str(r[0]),
                "user_id": str(r[1]),
                "role": r[2],
                "position": r[3],
                "monthly_limit": monthly_limit,
                "daily_limit": float(r[5]) if r[5] else None,
                "current_month_spent": monthly_spent,
                "current_day_spent": float(r[7] or 0),
                "remaining": round(monthly_limit - monthly_spent, 2) if monthly_limit else None,
                "is_active": r[8],
                "phone": r[10],
                "name": f"{r[11] or ''} {r[12] or ''}".strip() or r[10],
            })
        return employees

    def update_employee(self, employee_id: str, data: Dict[str, Any]) -> bool:
        """Обновить данные сотрудника."""
        updates = []
        params = {"id": employee_id}
        for key in ["role", "position", "monthly_limit", "daily_limit", "is_active"]:
            if key in data:
                updates.append(f"{key} = :{key}")
                params[key] = data[key]

        if not updates:
            return False

        updates.append("updated_at = NOW()")
        self.db.execute(text(f"""
            UPDATE corporate_employees SET {', '.join(updates)} WHERE id = :id
        """), params)
        self.db.commit()
        return True

    def remove_employee(self, employee_id: str) -> bool:
        """Деактивировать сотрудника."""
        self.db.execute(text("""
            UPDATE corporate_employees SET is_active = false, updated_at = NOW() WHERE id = :id
        """), {"id": employee_id})
        self.db.commit()
        return True

    # ============================================
    # Charging Authorization
    # ============================================

    def can_employee_charge(self, user_id: str, requested_amount: float) -> Tuple[bool, str, Optional[Dict]]:
        """
        Проверяет, может ли корпоративный сотрудник начать зарядку.
        Returns: (can_charge, reason, employee_info)
        """
        row = self.db.execute(text("""
            SELECT ce.id, ce.corporate_group_id, ce.monthly_limit, ce.daily_limit,
                   ce.current_month_spent, ce.current_day_spent, ce.last_day_reset,
                   ce.is_active,
                   g.balance, g.credit_limit, g.monthly_limit AS group_monthly_limit,
                   g.current_month_spent AS group_month_spent, g.billing_type,
                   g.is_active AS group_active, g.company_name
            FROM corporate_employees ce
            JOIN corporate_groups g ON ce.corporate_group_id = g.id
            WHERE ce.user_id = :user_id AND ce.is_active = true
        """), {"user_id": user_id}).fetchone()

        if not row:
            return False, "not_corporate", None

        emp_id = str(row[0])
        group_id = str(row[1])
        emp_monthly_limit = float(row[2]) if row[2] else None
        emp_daily_limit = float(row[3]) if row[3] else None
        emp_month_spent = float(row[4] or 0)
        emp_day_spent = float(row[5] or 0)
        last_day_reset = row[6]
        group_balance = float(row[8])
        group_credit_limit = float(row[9])
        group_monthly_limit = float(row[10]) if row[10] else None
        group_month_spent = float(row[11] or 0)
        billing_type = row[12]
        group_active = row[13]
        company_name = row[14]

        # 1. Группа активна?
        if not group_active:
            return False, "Корпоративный аккаунт заблокирован", None

        # 2. Prepaid — баланс
        if billing_type == "prepaid" and group_balance < requested_amount:
            return False, "Недостаточно средств на балансе компании", None

        # 3. Postpaid — кредитный лимит
        if billing_type == "postpaid":
            available = group_credit_limit - group_month_spent
            if available < requested_amount:
                return False, "Превышен кредитный лимит компании", None

        # 4. Месячный лимит компании
        if group_monthly_limit:
            remaining = group_monthly_limit - group_month_spent
            if remaining < requested_amount:
                return False, f"Превышен месячный лимит компании. Осталось: {remaining:.0f} сом", None

        # 5. Месячный лимит сотрудника
        if emp_monthly_limit:
            remaining = emp_monthly_limit - emp_month_spent
            if remaining < requested_amount:
                return False, f"Превышен ваш месячный лимит. Осталось: {remaining:.0f} сом", None

        # 6. Дневной лимит сотрудника (с автосбросом)
        if emp_daily_limit:
            today = date.today()
            if last_day_reset != today:
                emp_day_spent = 0
                self.db.execute(text("""
                    UPDATE corporate_employees
                    SET current_day_spent = 0, last_day_reset = :today
                    WHERE id = :id
                """), {"id": emp_id, "today": today})

            remaining = emp_daily_limit - emp_day_spent
            if remaining < requested_amount:
                return False, f"Превышен дневной лимит. Осталось: {remaining:.0f} сом", None

        info = {
            "employee_id": emp_id,
            "group_id": group_id,
            "company_name": company_name,
            "billing_type": billing_type,
        }
        return True, "OK", info

    def reserve_funds(self, employee_id: str, session_id: str, amount: float) -> bool:
        """Зарезервировать средства при старте зарядки."""
        row = self.db.execute(text("""
            SELECT ce.corporate_group_id, g.balance, g.billing_type
            FROM corporate_employees ce
            JOIN corporate_groups g ON ce.corporate_group_id = g.id
            WHERE ce.id = :id
        """), {"id": employee_id}).fetchone()
        if not row:
            return False

        group_id = str(row[0])
        balance_before = float(row[1])
        billing_type = row[2]

        # Prepaid — списываем с баланса
        if billing_type == "prepaid":
            self.db.execute(text("""
                UPDATE corporate_groups SET balance = balance - :amount, updated_at = NOW()
                WHERE id = :id
            """), {"id": group_id, "amount": amount})

        # Обновляем счётчики
        self.db.execute(text("""
            UPDATE corporate_groups
            SET current_month_spent = current_month_spent + :amount, updated_at = NOW()
            WHERE id = :id
        """), {"id": group_id, "amount": amount})

        self.db.execute(text("""
            UPDATE corporate_employees
            SET current_month_spent = current_month_spent + :amount,
                current_day_spent = current_day_spent + :amount,
                updated_at = NOW()
            WHERE id = :id
        """), {"id": employee_id, "amount": amount})

        # Транзакция
        balance_after = balance_before - amount if billing_type == "prepaid" else balance_before
        self.db.execute(text("""
            INSERT INTO corporate_transactions
                (id, corporate_group_id, type, amount, balance_before, balance_after, charging_session_id, employee_id, description)
            VALUES (:id, :group_id, 'charge', :amount, :before, :after, :session_id, :emp_id, :desc)
        """), {
            "id": str(uuid.uuid4()),
            "group_id": group_id,
            "amount": amount,
            "before": balance_before,
            "after": balance_after,
            "session_id": session_id,
            "emp_id": employee_id,
            "desc": f"Зарядка, сессия {session_id}",
        })

        self.db.commit()
        return True

    def refund_unused(self, employee_id: str, session_id: str, reserved: float, actual: float) -> float:
        """Вернуть неиспользованные средства после завершения зарядки."""
        refund = round(reserved - actual, 2)
        if refund <= 0:
            return 0.0

        row = self.db.execute(text("""
            SELECT ce.corporate_group_id, g.balance, g.billing_type
            FROM corporate_employees ce
            JOIN corporate_groups g ON ce.corporate_group_id = g.id
            WHERE ce.id = :id
        """), {"id": employee_id}).fetchone()
        if not row:
            return 0.0

        group_id = str(row[0])
        balance_before = float(row[1])
        billing_type = row[2]

        # Prepaid — возвращаем на баланс
        if billing_type == "prepaid":
            self.db.execute(text("""
                UPDATE corporate_groups SET balance = balance + :amount, updated_at = NOW()
                WHERE id = :id
            """), {"id": group_id, "amount": refund})

        # Корректируем счётчики
        self.db.execute(text("""
            UPDATE corporate_groups
            SET current_month_spent = GREATEST(0, current_month_spent - :amount), updated_at = NOW()
            WHERE id = :id
        """), {"id": group_id, "amount": refund})

        self.db.execute(text("""
            UPDATE corporate_employees
            SET current_month_spent = GREATEST(0, current_month_spent - :amount),
                current_day_spent = GREATEST(0, current_day_spent - :amount),
                updated_at = NOW()
            WHERE id = :id
        """), {"id": employee_id, "amount": refund})

        # Транзакция возврата
        balance_after = balance_before + refund if billing_type == "prepaid" else balance_before
        self.db.execute(text("""
            INSERT INTO corporate_transactions
                (id, corporate_group_id, type, amount, balance_before, balance_after, charging_session_id, employee_id, description)
            VALUES (:id, :group_id, 'refund', :amount, :before, :after, :session_id, :emp_id, :desc)
        """), {
            "id": str(uuid.uuid4()),
            "group_id": group_id,
            "amount": refund,
            "before": balance_before,
            "after": balance_after,
            "session_id": session_id,
            "emp_id": employee_id,
            "desc": f"Возврат за сессию {session_id}",
        })

        self.db.commit()
        return refund

    # ============================================
    # Dashboard & Reports
    # ============================================

    def get_dashboard(self, group_id: str) -> Dict[str, Any]:
        """Дашборд корпоративного админа."""
        group = self.get_group(group_id)
        if not group:
            return {}

        employees = self.db.execute(text("""
            SELECT COUNT(*) FILTER (WHERE is_active = true),
                   COUNT(*) FILTER (WHERE current_day_spent > 0 AND is_active = true)
            FROM corporate_employees
            WHERE corporate_group_id = :gid
        """), {"gid": group_id}).fetchone()

        recent = self.db.execute(text("""
            SELECT ct.amount, ct.type, ct.description, ct.created_at,
                   c.first_name, c.last_name
            FROM corporate_transactions ct
            LEFT JOIN corporate_employees ce ON ct.employee_id = ce.id
            LEFT JOIN clients c ON ce.user_id = c.id
            WHERE ct.corporate_group_id = :gid
            ORDER BY ct.created_at DESC
            LIMIT 10
        """), {"gid": group_id}).fetchall()

        recent_list = []
        for r in recent:
            recent_list.append({
                "amount": float(r[0]),
                "type": r[1],
                "description": r[2],
                "created_at": r[3].isoformat() if r[3] else None,
                "employee_name": f"{r[4] or ''} {r[5] or ''}".strip() or None,
            })

        return {
            "company": {
                "name": group["company_name"],
                "billing_type": group["billing_type"],
                "balance": group["balance"],
                "credit_limit": group["credit_limit"],
            },
            "current_month": {
                "spent": group["current_month_spent"],
                "limit": group["monthly_limit"],
                "remaining": round((group["monthly_limit"] or 0) - group["current_month_spent"], 2) if group["monthly_limit"] else None,
            },
            "employees": {
                "total": employees[0] if employees else 0,
                "active_today": employees[1] if employees else 0,
            },
            "recent_transactions": recent_list,
        }

    def get_report(
        self,
        group_id: str,
        period_start: date,
        period_end: date,
        employee_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Отчёт по зарядкам компании за период."""
        group = self.get_group(group_id)
        if not group:
            return {}

        emp_filter = "AND ct.employee_id = :emp_id" if employee_id else ""
        params = {"gid": group_id, "start": period_start, "end": period_end}
        if employee_id:
            params["emp_id"] = employee_id

        # Общая статистика
        summary = self.db.execute(text(f"""
            SELECT COALESCE(SUM(ct.amount), 0),
                   COUNT(*),
                   COUNT(DISTINCT ct.employee_id)
            FROM corporate_transactions ct
            WHERE ct.corporate_group_id = :gid
              AND ct.type = 'charge'
              AND ct.created_at >= :start AND ct.created_at < :end + INTERVAL '1 day'
              {emp_filter}
        """), params).fetchone()

        # По сотрудникам
        by_employee = self.db.execute(text(f"""
            SELECT ce.id, c.first_name, c.last_name, ce.position,
                   COUNT(ct.id) AS sessions_count,
                   COALESCE(SUM(ct.amount), 0) AS total_amount
            FROM corporate_employees ce
            LEFT JOIN clients c ON ce.user_id = c.id
            LEFT JOIN corporate_transactions ct ON ct.employee_id = ce.id
                AND ct.type = 'charge'
                AND ct.created_at >= :start AND ct.created_at < :end + INTERVAL '1 day'
            WHERE ce.corporate_group_id = :gid
            GROUP BY ce.id, c.first_name, c.last_name, ce.position
            ORDER BY total_amount DESC
        """), {"gid": group_id, "start": period_start, "end": period_end}).fetchall()

        by_emp_list = []
        for r in by_employee:
            by_emp_list.append({
                "employee_id": str(r[0]),
                "name": f"{r[1] or ''} {r[2] or ''}".strip(),
                "position": r[3],
                "sessions_count": r[4],
                "amount": float(r[5]),
            })

        return {
            "company_name": group["company_name"],
            "period": {"start": period_start.isoformat(), "end": period_end.isoformat()},
            "summary": {
                "total_amount": float(summary[0]),
                "sessions_count": summary[1],
                "employees_charged": summary[2],
            },
            "by_employee": by_emp_list,
        }

    def get_employee_corporate_info(self, user_id: str) -> Dict[str, Any]:
        """Информация о корпоративном аккаунте сотрудника (для PWA)."""
        row = self.db.execute(text("""
            SELECT ce.id, ce.role, ce.position,
                   ce.monthly_limit, ce.daily_limit,
                   ce.current_month_spent, ce.current_day_spent,
                   ce.is_active,
                   g.company_name, g.is_active AS group_active,
                   g.blocked_reason
            FROM corporate_employees ce
            JOIN corporate_groups g ON ce.corporate_group_id = g.id
            WHERE ce.user_id = :user_id AND ce.is_active = true
        """), {"user_id": user_id}).fetchone()

        if not row:
            return {"is_corporate": False}

        monthly_limit = float(row[3]) if row[3] else None
        daily_limit = float(row[4]) if row[4] else None
        monthly_spent = float(row[5] or 0)
        daily_spent = float(row[6] or 0)

        can_charge = row[7] and row[9]  # employee active AND group active
        block_reason = None
        if not row[9]:
            block_reason = row[10] or "Корпоративный аккаунт заблокирован"
        elif not row[7]:
            block_reason = "Ваш доступ отключён"

        return {
            "is_corporate": True,
            "company_name": row[8],
            "role": row[1],
            "position": row[2],
            "limits": {
                "monthly_limit": monthly_limit,
                "monthly_spent": monthly_spent,
                "monthly_remaining": round(monthly_limit - monthly_spent, 2) if monthly_limit else None,
                "daily_limit": daily_limit,
                "daily_spent": daily_spent,
                "daily_remaining": round(daily_limit - daily_spent, 2) if daily_limit else None,
            },
            "can_charge": can_charge,
            "block_reason": block_reason,
        }

    # ============================================
    # Helpers
    # ============================================

    def _row_to_group(self, r) -> Dict[str, Any]:
        return {
            "id": str(r[0]),
            "company_name": r[1],
            "inn": r[2],
            "legal_address": r[3],
            "contact_person": r[4],
            "contact_phone": r[5],
            "contact_email": r[6],
            "balance": float(r[7] or 0),
            "credit_limit": float(r[8] or 0),
            "monthly_limit": float(r[9]) if r[9] else None,
            "billing_type": r[10],
            "current_month_spent": float(r[11] or 0),
            "contract_number": r[12],
            "contract_date": r[13].isoformat() if r[13] else None,
            "contract_expires": r[14].isoformat() if r[14] else None,
            "is_active": r[15],
            "blocked_reason": r[16],
            "created_at": r[17].isoformat() if r[17] else None,
            "updated_at": r[18].isoformat() if r[18] else None,
        }
