"""
AI Pricing Intelligence Service

Анализирует конкурентов, спрос и выручку для генерации
рекомендаций по оптимальному ценообразованию.

Поддерживает:
- Глобальный анализ (все станции)
- Per-station анализ
- Per-location анализ (группа станций одной локации)
- Batch анализ (все станции по отдельности)
- Авто-оптимизация с автоматическим применением
"""
import uuid
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from sqlalchemy.orm import Session
from sqlalchemy import text

logger = logging.getLogger(__name__)

# Константы
ELECTRICITY_COST_KGS = 1.50  # стоимость электричества за кВтч в KGS
MIN_MARGIN_MULTIPLIER = 3.0   # минимальная маржа: цена >= стоимость * 3
NIGHT_DISCOUNT = 0.20          # ночная скидка 20%


class AIPricingService:
    def __init__(self, db: Session):
        self.db = db

    # ══════════════════════════════════════════════════════════════
    # Public API
    # ══════════════════════════════════════════════════════════════

    def analyze_and_recommend(
        self,
        station_id: Optional[str] = None,
        date_range_days: int = 30,
    ) -> Dict[str, Any]:
        """Главный метод: анализ и генерация рекомендации."""
        current = self._get_current_pricing(station_id)
        competitors = self._analyze_competitors()
        demand = self._analyze_demand_patterns(station_id, date_range_days)
        revenue = self._analyze_revenue(station_id, date_range_days)

        recommended = self._calculate_optimal_price(
            current=current,
            competitors=competitors,
            demand=demand,
            revenue=revenue,
        )

        reasoning = self._generate_reasoning(
            current=current,
            competitors=competitors,
            demand=demand,
            revenue=revenue,
            recommended=recommended,
        )

        rec_id = self._save_recommendation(
            station_id=station_id,
            recommended=recommended,
            current=current,
            competitors=competitors,
            revenue=revenue,
            reasoning=reasoning,
        )

        return {
            "recommendation_id": rec_id,
            "recommended_price_per_kwh": recommended["price"],
            "recommended_price_night": recommended["price_night"],
            "current_price_per_kwh": current.get("tariff_per_kwh"),
            "confidence_level": recommended["confidence"],
            "reasoning": reasoning,
            "competitors_summary": competitors,
            "demand_summary": demand,
            "revenue_summary": revenue,
            "factors": recommended.get("factors", {}),
        }

    def analyze_batch(
        self,
        station_ids: Optional[List[str]] = None,
        location_ids: Optional[List[str]] = None,
        date_range_days: int = 30,
    ) -> Dict[str, Any]:
        """Batch анализ: per-station рекомендации для выбранных или всех станций.

        Можно фильтровать по station_ids или location_ids.
        Возвращает рекомендации сгруппированные по локациям.
        """
        # Получаем станции
        stations = self._get_stations(station_ids, location_ids)
        if not stations:
            return {"locations": [], "summary": {"total_stations": 0}}

        competitors = self._analyze_competitors()

        # Группируем по локациям
        location_map: Dict[str, Dict[str, Any]] = {}
        for st in stations:
            loc_id = st["location_id"] or "no-location"
            if loc_id not in location_map:
                location_map[loc_id] = {
                    "location_id": st["location_id"],
                    "location_name": st["location_name"] or "Без локации",
                    "location_city": st["location_city"],
                    "stations": [],
                }
            location_map[loc_id]["stations"].append(st)

        # Анализ каждой станции
        results: List[Dict[str, Any]] = []
        for loc_id, loc_data in location_map.items():
            loc_recommendations = []
            for st in loc_data["stations"]:
                rec = self._analyze_single_station(
                    station=st,
                    competitors=competitors,
                    days=date_range_days,
                )
                loc_recommendations.append(rec)

            # Сводка по локации
            prices = [r["recommended_price_per_kwh"] for r in loc_recommendations if r["recommended_price_per_kwh"]]
            loc_avg_price = round(sum(prices) / len(prices), 1) if prices else None

            results.append({
                **{k: v for k, v in loc_data.items() if k != "stations"},
                "recommended_avg_price": loc_avg_price,
                "station_count": len(loc_recommendations),
                "stations": loc_recommendations,
            })

        # Общая сводка
        all_recs = [r for loc in results for r in loc["stations"]]
        all_prices = [r["recommended_price_per_kwh"] for r in all_recs if r["recommended_price_per_kwh"]]

        return {
            "locations": results,
            "summary": {
                "total_stations": len(all_recs),
                "avg_recommended_price": round(sum(all_prices) / len(all_prices), 1) if all_prices else None,
                "min_recommended_price": round(min(all_prices), 1) if all_prices else None,
                "max_recommended_price": round(max(all_prices), 1) if all_prices else None,
                "competitors": competitors,
            },
        }

    def auto_optimize(
        self,
        station_ids: Optional[List[str]] = None,
        location_ids: Optional[List[str]] = None,
        date_range_days: int = 30,
        admin_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Автоматическая оптимизация цен: анализ + немедленное применение.

        Для каждой станции:
        1. Анализирует текущие показатели
        2. Вычисляет оптимальную цену
        3. Применяет если отклонение > 5% от текущей
        4. Сохраняет рекомендацию с обоснованием
        """
        stations = self._get_stations(station_ids, location_ids)
        if not stations:
            return {"applied": [], "skipped": [], "errors": []}

        competitors = self._analyze_competitors()
        applied = []
        skipped = []
        errors = []

        for st in stations:
            try:
                rec = self._analyze_single_station(
                    station=st,
                    competitors=competitors,
                    days=date_range_days,
                    save=True,
                )

                current_price = st.get("tariff_per_kwh") or 0
                new_price = rec["recommended_price_per_kwh"]

                if not new_price or new_price <= 0:
                    skipped.append({
                        "station_id": st["id"],
                        "station_name": st["serial_number"],
                        "reason": "Не удалось рассчитать оптимальную цену",
                    })
                    continue

                # Применяем если отклонение > 5%
                if current_price > 0:
                    change_pct = abs(new_price - current_price) / current_price * 100
                    if change_pct < 5:
                        skipped.append({
                            "station_id": st["id"],
                            "station_name": st["serial_number"],
                            "current_price": current_price,
                            "recommended_price": new_price,
                            "change_pct": round(change_pct, 1),
                            "reason": f"Изменение {round(change_pct, 1)}% < 5% — цена оптимальна",
                        })
                        continue

                # Применяем
                self.db.execute(text("""
                    UPDATE stations SET tariff_per_kwh = :price WHERE id = :sid
                """), {"price": new_price, "sid": st["id"]})

                # Обновляем статус рекомендации
                if rec.get("recommendation_id"):
                    self.db.execute(text("""
                        UPDATE ai_pricing_recommendations
                        SET status = 'applied', applied_at = NOW(), applied_by = :admin
                        WHERE id = :id
                    """), {"id": rec["recommendation_id"], "admin": admin_id})

                applied.append({
                    "station_id": st["id"],
                    "station_name": st["serial_number"],
                    "location_name": st.get("location_name"),
                    "old_price": current_price,
                    "new_price": new_price,
                    "change_pct": round((new_price - current_price) / max(current_price, 0.01) * 100, 1),
                    "reasoning": rec.get("reasoning", ""),
                    "confidence": rec.get("confidence_level", "medium"),
                })

            except Exception as e:
                logger.error(f"Auto-optimize failed for station {st['id']}: {e}")
                errors.append({
                    "station_id": st["id"],
                    "station_name": st.get("serial_number"),
                    "error": str(e),
                })

        self.db.commit()

        return {
            "applied": applied,
            "skipped": skipped,
            "errors": errors,
            "summary": {
                "total": len(stations),
                "applied_count": len(applied),
                "skipped_count": len(skipped),
                "error_count": len(errors),
            },
        }

    def apply_selective(
        self,
        recommendation_id: str,
        station_ids: Optional[List[str]] = None,
        location_ids: Optional[List[str]] = None,
        admin_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Применить рекомендацию выборочно к станциям или локациям."""
        rec = self.db.execute(text("""
            SELECT id, recommended_price_per_kwh, recommended_price_night, status
            FROM ai_pricing_recommendations WHERE id = :id
        """), {"id": recommendation_id}).fetchone()

        if not rec:
            return {"success": False, "error": "Recommendation not found"}
        if rec[3] == "applied":
            return {"success": False, "error": "Already fully applied"}

        new_price = float(rec[1])

        # Собираем target station IDs
        target_ids: List[str] = []

        if station_ids:
            target_ids.extend(station_ids)

        if location_ids:
            loc_stations = self.db.execute(text("""
                SELECT id FROM stations
                WHERE location_id = ANY(:locs) AND status = 'active'
            """), {"locs": location_ids}).fetchall()
            target_ids.extend([str(r[0]) for r in loc_stations])

        if not target_ids:
            return {"success": False, "error": "No stations to update"}

        # Дедупликация
        target_ids = list(set(target_ids))

        # Применяем
        updated = 0
        details = []
        for sid in target_ids:
            old = self.db.execute(text(
                "SELECT tariff_per_kwh, serial_number FROM stations WHERE id = :id"
            ), {"id": sid}).fetchone()

            result = self.db.execute(text("""
                UPDATE stations SET tariff_per_kwh = :price WHERE id = :sid AND status = 'active'
            """), {"price": new_price, "sid": sid})
            if result.rowcount > 0:
                updated += 1
                details.append({
                    "station_id": sid,
                    "station_name": old[1] if old else None,
                    "old_price": float(old[0]) if old and old[0] else None,
                    "new_price": new_price,
                })

        # Обновляем статус рекомендации
        self.db.execute(text("""
            UPDATE ai_pricing_recommendations
            SET status = 'applied', applied_at = NOW(), applied_by = :admin
            WHERE id = :id
        """), {"id": recommendation_id, "admin": admin_id})

        self.db.commit()

        return {
            "success": True,
            "updated_stations": updated,
            "applied_price": new_price,
            "details": details,
        }

    # ══════════════════════════════════════════════════════════════
    # Internal: Station fetching
    # ══════════════════════════════════════════════════════════════

    def _get_stations(
        self,
        station_ids: Optional[List[str]] = None,
        location_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Получить станции с фильтрацией."""
        where_parts = ["s.status = 'active'"]
        params: Dict[str, Any] = {}

        if station_ids:
            where_parts.append("s.id = ANY(:station_ids)")
            params["station_ids"] = station_ids
        if location_ids:
            where_parts.append("s.location_id = ANY(:location_ids)")
            params["location_ids"] = location_ids

        where = " AND ".join(where_parts)

        rows = self.db.execute(text(f"""
            SELECT s.id, s.serial_number, s.model, s.price_per_kwh,
                   s.location_id, l.name as location_name, l.city as location_city,
                   s.power_capacity
            FROM stations s
            LEFT JOIN locations l ON l.id = s.location_id
            WHERE {where}
            ORDER BY l.name NULLS LAST, s.serial_number
        """), params).fetchall()

        return [
            {
                "id": str(r[0]),
                "serial_number": r[1],
                "model": r[2],
                "tariff_per_kwh": float(r[3]) if r[3] else None,
                "location_id": r[4],
                "location_name": r[5],
                "location_city": r[6],
                "power_capacity": float(r[7]) if r[7] else None,
            }
            for r in rows
        ]

    # ══════════════════════════════════════════════════════════════
    # Internal: Single station analysis
    # ══════════════════════════════════════════════════════════════

    def _analyze_single_station(
        self,
        station: Dict[str, Any],
        competitors: Dict[str, Any],
        days: int = 30,
        save: bool = True,
    ) -> Dict[str, Any]:
        """Полный анализ одной станции."""
        sid = station["id"]
        current = {
            "tariff_per_kwh": station.get("tariff_per_kwh"),
            "serial_number": station.get("serial_number"),
        }

        demand = self._analyze_demand_patterns(sid, days)
        revenue = self._analyze_revenue(sid, days)

        # Определяем тип станции (DC/AC) по мощности
        power = station.get("power_capacity") or 22
        station_type = "dc" if power > 22 else "ac"

        recommended = self._calculate_optimal_price(
            current=current,
            competitors=competitors,
            demand=demand,
            revenue=revenue,
            station_type=station_type,
        )

        reasoning = self._generate_station_reasoning(
            station=station,
            competitors=competitors,
            demand=demand,
            revenue=revenue,
            recommended=recommended,
        )

        rec_id = None
        if save:
            rec_id = self._save_recommendation(
                station_id=sid,
                recommended=recommended,
                current=current,
                competitors=competitors,
                revenue=revenue,
                reasoning=reasoning,
            )

        return {
            "station_id": sid,
            "serial_number": station.get("serial_number"),
            "model": station.get("model"),
            "location_name": station.get("location_name"),
            "location_id": station.get("location_id"),
            "station_type": station_type,
            "current_price": station.get("tariff_per_kwh"),
            "recommended_price_per_kwh": recommended["price"],
            "recommended_price_night": recommended["price_night"],
            "price_change_pct": recommended.get("estimated_revenue_change"),
            "confidence_level": recommended["confidence"],
            "reasoning": reasoning,
            "demand": demand,
            "revenue": revenue,
            "recommendation_id": rec_id,
        }

    # ══════════════════════════════════════════════════════════════
    # Internal: Current pricing
    # ══════════════════════════════════════════════════════════════

    def _get_current_pricing(self, station_id: Optional[str]) -> Dict[str, Any]:
        if not station_id:
            row = self.db.execute(text("""
                SELECT AVG(tariff_per_kwh), COUNT(*)
                FROM stations
                WHERE tariff_per_kwh > 0 AND status = 'active'
            """)).fetchone()
            return {
                "tariff_per_kwh": float(row[0]) if row and row[0] else None,
                "station_count": row[1] if row else 0,
            }

        row = self.db.execute(text("""
            SELECT tariff_per_kwh, serial_number, status
            FROM stations WHERE id = :id
        """), {"id": station_id}).fetchone()

        if not row:
            return {"tariff_per_kwh": None}

        return {
            "tariff_per_kwh": float(row[0]) if row[0] else None,
            "serial_number": row[1],
            "status": row[2],
        }

    # ══════════════════════════════════════════════════════════════
    # Internal: Competitors analysis
    # ══════════════════════════════════════════════════════════════

    def _analyze_competitors(self) -> Dict[str, Any]:
        rows = self.db.execute(text("""
            SELECT
                charging_type,
                AVG(price_per_kwh) as avg_price,
                MIN(price_per_kwh) as min_price,
                MAX(price_per_kwh) as max_price,
                COUNT(*) as cnt
            FROM competitor_prices
            WHERE is_active = true AND currency = 'KGS'
            GROUP BY charging_type
        """)).fetchall()

        result: Dict[str, Any] = {"dc": {}, "ac": {}, "overall": {}}
        all_prices: List[float] = []

        for r in rows:
            key = (r[0] or "").lower() or "other"
            avg_p = float(r[1]) if r[1] else 0
            min_p = float(r[2]) if r[2] else 0
            max_p = float(r[3]) if r[3] else 0
            result[key] = {
                "avg": round(avg_p, 2),
                "min": round(min_p, 2),
                "max": round(max_p, 2),
                "count": r[4],
            }
            all_prices.extend([avg_p] * r[4])

        if all_prices:
            result["overall"] = {
                "avg": round(sum(all_prices) / len(all_prices), 2),
                "min": round(min(all_prices), 2),
                "max": round(max(all_prices), 2),
                "total_entries": len(all_prices),
            }

        return result

    # ══════════════════════════════════════════════════════════════
    # Internal: Demand patterns
    # ══════════════════════════════════════════════════════════════

    def _analyze_demand_patterns(
        self, station_id: Optional[str], days: int
    ) -> Dict[str, Any]:
        since = datetime.utcnow() - timedelta(days=days)
        params: Dict[str, Any] = {"since": since}

        station_filter = ""
        if station_id:
            station_filter = "AND cs.station_id = :station_id"
            params["station_id"] = station_id

        row = self.db.execute(text(f"""
            SELECT
                COUNT(*) as total_sessions,
                COALESCE(AVG(cs.energy), 0) as avg_energy,
                COALESCE(SUM(cs.energy), 0) as total_energy,
                COUNT(DISTINCT DATE(cs.start_time)) as active_days
            FROM charging_sessions cs
            WHERE cs.start_time >= :since
              AND cs.status = 'stopped'
              {station_filter}
        """), params).fetchone()

        total_sessions = row[0] if row else 0
        avg_energy = float(row[1]) if row and row[1] else 0
        total_energy = float(row[2]) if row and row[2] else 0
        active_days = row[3] if row else 0

        # Часовое распределение
        hourly = self.db.execute(text(f"""
            SELECT
                EXTRACT(HOUR FROM cs.start_time)::int as hour,
                COUNT(*) as cnt
            FROM charging_sessions cs
            WHERE cs.start_time >= :since
              AND cs.status = 'stopped'
              {station_filter}
            GROUP BY hour
            ORDER BY hour
        """), params).fetchall()

        hours_dist = {int(h[0]): h[1] for h in hourly}
        peak_hour = max(hours_dist, key=hours_dist.get) if hours_dist else None
        night_sessions = sum(v for k, v in hours_dist.items() if k >= 22 or k < 6)

        utilization = "low"
        if active_days > 0:
            sessions_per_day = total_sessions / max(active_days, 1)
            if sessions_per_day > 10:
                utilization = "high"
            elif sessions_per_day > 4:
                utilization = "medium"

        return {
            "total_sessions": total_sessions,
            "avg_energy_kwh": round(avg_energy, 1),
            "total_energy_kwh": round(total_energy, 1),
            "active_days": active_days,
            "peak_hour": peak_hour,
            "night_sessions_pct": round(night_sessions / max(total_sessions, 1) * 100, 1),
            "utilization": utilization,
        }

    # ══════════════════════════════════════════════════════════════
    # Internal: Revenue analysis
    # ══════════════════════════════════════════════════════════════

    def _analyze_revenue(
        self, station_id: Optional[str], days: int
    ) -> Dict[str, Any]:
        since = datetime.utcnow() - timedelta(days=days)
        params: Dict[str, Any] = {"since": since}

        station_filter = ""
        if station_id:
            station_filter = "AND cs.station_id = :station_id"
            params["station_id"] = station_id

        row = self.db.execute(text(f"""
            SELECT
                COALESCE(SUM(cs.amount), 0) as total_revenue,
                COALESCE(AVG(cs.amount), 0) as avg_per_session,
                COUNT(*) as sessions
            FROM charging_sessions cs
            WHERE cs.start_time >= :since
              AND cs.status = 'stopped'
              {station_filter}
        """), params).fetchone()

        total_revenue = float(row[0]) if row and row[0] else 0
        avg_per_session = float(row[1]) if row and row[1] else 0
        sessions = row[2] if row else 0

        # Тренд: вторая половина vs первая
        mid = since + timedelta(days=days // 2)
        params["mid"] = mid

        rev_first = self.db.execute(text(f"""
            SELECT COALESCE(SUM(cs.amount), 0)
            FROM charging_sessions cs
            WHERE cs.start_time >= :since AND cs.start_time < :mid
              AND cs.status = 'stopped'
              {station_filter}
        """), params).scalar() or 0

        rev_second = self.db.execute(text(f"""
            SELECT COALESCE(SUM(cs.amount), 0)
            FROM charging_sessions cs
            WHERE cs.start_time >= :mid
              AND cs.status = 'stopped'
              {station_filter}
        """), params).scalar() or 0

        trend = "stable"
        if float(rev_first) > 0:
            change = (float(rev_second) - float(rev_first)) / float(rev_first) * 100
            if change > 15:
                trend = "growing"
            elif change < -15:
                trend = "declining"

        return {
            "total_revenue": round(total_revenue, 2),
            "avg_per_session": round(avg_per_session, 2),
            "sessions": sessions,
            "trend": trend,
            "monthly_estimate": round(total_revenue / max(days, 1) * 30, 2),
        }

    # ══════════════════════════════════════════════════════════════
    # Internal: Optimal price calculation
    # ══════════════════════════════════════════════════════════════

    def _calculate_optimal_price(
        self,
        current: Dict[str, Any],
        competitors: Dict[str, Any],
        demand: Dict[str, Any],
        revenue: Dict[str, Any],
        station_type: str = "dc",
    ) -> Dict[str, Any]:
        factors: Dict[str, Any] = {}

        # 1. Floor: electricity_cost * MIN_MARGIN_MULTIPLIER
        floor_price = ELECTRICITY_COST_KGS * MIN_MARGIN_MULTIPLIER
        factors["floor_price"] = floor_price

        # 2. Competitor-based price — учитываем тип станции
        comp_overall = competitors.get("overall", {})
        comp_type = competitors.get(station_type, {}).get("avg", 0)
        comp_avg = comp_overall.get("avg", 0)

        # Предпочитаем цену конкурентов для того же типа станции
        base_competitor = comp_type if comp_type > 0 else comp_avg

        if base_competitor > 0:
            competitor_price = base_competitor * 0.95
            factors["competitor_avg"] = base_competitor
            factors["competitor_target"] = round(competitor_price, 2)
            factors["station_type"] = station_type
        else:
            competitor_price = 13.0
            factors["competitor_target"] = competitor_price

        # 3. Demand adjustment
        utilization = demand.get("utilization", "low")
        demand_multiplier = 1.0
        if utilization == "high":
            demand_multiplier = 1.10
        elif utilization == "low":
            demand_multiplier = 0.92

        factors["utilization"] = utilization
        factors["demand_multiplier"] = demand_multiplier

        # 4. Revenue trend adjustment
        trend = revenue.get("trend", "stable")
        trend_multiplier = 1.0
        if trend == "declining":
            trend_multiplier = 0.95
        elif trend == "growing":
            trend_multiplier = 1.03

        factors["revenue_trend"] = trend
        factors["trend_multiplier"] = trend_multiplier

        # Итоговая цена
        optimal = competitor_price * demand_multiplier * trend_multiplier
        optimal = max(optimal, floor_price)
        optimal = round(optimal, 1)

        # Ночная цена: -20%
        night_price = round(optimal * (1 - NIGHT_DISCOUNT), 1)

        # Confidence
        confidence = "medium"
        total_data_points = (
            comp_overall.get("total_entries", 0)
            + demand.get("total_sessions", 0)
        )
        if total_data_points > 50 and base_competitor > 0:
            confidence = "high"
        elif total_data_points < 10 or base_competitor == 0:
            confidence = "low"

        factors["electricity_cost"] = ELECTRICITY_COST_KGS

        # Estimated revenue change
        current_price = current.get("tariff_per_kwh")
        est_change = None
        if current_price and current_price > 0:
            est_change = round((optimal - current_price) / current_price * 100, 1)

        return {
            "price": optimal,
            "price_night": night_price,
            "confidence": confidence,
            "estimated_revenue_change": est_change,
            "factors": factors,
        }

    # ══════════════════════════════════════════════════════════════
    # Internal: Reasoning generation
    # ══════════════════════════════════════════════════════════════

    def _generate_reasoning(
        self,
        current: Dict[str, Any],
        competitors: Dict[str, Any],
        demand: Dict[str, Any],
        revenue: Dict[str, Any],
        recommended: Dict[str, Any],
    ) -> str:
        parts = []

        comp = competitors.get("overall", {})
        if comp.get("avg"):
            parts.append(
                f"Средняя цена конкурентов: {comp['avg']} KGS/кВтч "
                f"(мин {comp.get('min', '?')}, макс {comp.get('max', '?')}). "
                f"Рекомендуемая цена установлена ниже среднерыночной для привлечения клиентов."
            )

        util = demand.get("utilization", "low")
        util_text = {"high": "высокая", "medium": "средняя", "low": "низкая"}.get(util, util)
        parts.append(f"Загруженность станций: {util_text}.")

        if util == "high":
            parts.append("Высокий спрос позволяет установить цену ближе к рыночной.")
        elif util == "low":
            parts.append("Низкая загруженность — рекомендуется снизить цену для привлечения клиентов.")

        trend = revenue.get("trend", "stable")
        if trend == "growing":
            parts.append("Выручка растёт — текущая стратегия работает.")
        elif trend == "declining":
            parts.append("Выручка снижается — корректировка цены может помочь.")

        parts.append(
            f"Ночной тариф ({recommended['price_night']} KGS) на 20% ниже — "
            f"стимулирует зарядку в часы пониженного спроса."
        )

        parts.append(
            f"Минимальная допустимая цена: {ELECTRICITY_COST_KGS * MIN_MARGIN_MULTIPLIER} KGS "
            f"(стоимость электричества {ELECTRICITY_COST_KGS} KGS × {MIN_MARGIN_MULTIPLIER})."
        )

        return " ".join(parts)

    def _generate_station_reasoning(
        self,
        station: Dict[str, Any],
        competitors: Dict[str, Any],
        demand: Dict[str, Any],
        revenue: Dict[str, Any],
        recommended: Dict[str, Any],
    ) -> str:
        """Генерация обоснования для конкретной станции."""
        parts = []
        sn = station.get("serial_number") or station.get("id", "")[:8]
        loc = station.get("location_name") or ""
        power = station.get("power_capacity") or 22
        station_type = "DC" if power > 22 else "AC"

        parts.append(f"Станция {sn}")
        if loc:
            parts.append(f"({loc})")
        parts.append(f"— {station_type} {power} кВт.")

        # Текущая цена
        current_price = station.get("tariff_per_kwh")
        if current_price:
            diff = recommended["price"] - current_price
            direction = "повышение" if diff > 0 else "снижение" if diff < 0 else "без изменений"
            parts.append(f"Текущая цена: {current_price} KGS → рекомендуемая: {recommended['price']} KGS ({direction} на {abs(round(diff, 1))} KGS).")
        else:
            parts.append(f"Рекомендуемая цена: {recommended['price']} KGS.")

        # Конкуренты для этого типа
        comp_type = competitors.get(station_type.lower(), {})
        if comp_type.get("avg"):
            parts.append(
                f"Конкуренты ({station_type}): средняя {comp_type['avg']} KGS "
                f"(мин {comp_type.get('min', '?')}, макс {comp_type.get('max', '?')})."
            )

        # Спрос
        sessions = demand.get("total_sessions", 0)
        util = demand.get("utilization", "low")
        util_ru = {"high": "высокая", "medium": "средняя", "low": "низкая"}.get(util, util)
        parts.append(f"Загруженность: {util_ru} ({sessions} сессий за период).")

        if util == "high":
            parts.append("Высокий спрос позволяет цену ближе к рыночной.")
        elif util == "low":
            parts.append("Низкая загруженность — снижение цены привлечёт клиентов.")

        # Выручка
        trend = revenue.get("trend", "stable")
        rev_total = revenue.get("total_revenue", 0)
        if rev_total > 0:
            trend_ru = {"growing": "растёт", "declining": "снижается", "stable": "стабильна"}.get(trend, trend)
            parts.append(f"Выручка: {rev_total} KGS ({trend_ru}).")

        # Ночной тариф
        parts.append(f"Ночной тариф: {recommended['price_night']} KGS (-20%).")

        return " ".join(parts)

    # ══════════════════════════════════════════════════════════════
    # Internal: Save recommendation
    # ══════════════════════════════════════════════════════════════

    def _save_recommendation(
        self,
        station_id: Optional[str],
        recommended: Dict[str, Any],
        current: Dict[str, Any],
        competitors: Dict[str, Any],
        revenue: Dict[str, Any],
        reasoning: str,
    ) -> str:
        rec_id = str(uuid.uuid4())
        comp = competitors.get("overall", {})
        monthly_est = revenue.get("monthly_estimate", 0)

        current_price = current.get("tariff_per_kwh")
        if current_price and current_price > 0 and monthly_est > 0:
            ratio = recommended["price"] / current_price
            monthly_est = round(monthly_est * ratio, 2)

        self.db.execute(text("""
            INSERT INTO ai_pricing_recommendations (
                id, station_id,
                recommended_price_per_kwh, recommended_price_night,
                current_price_per_kwh,
                avg_competitor_price, min_competitor_price, max_competitor_price,
                electricity_cost_per_kwh,
                estimated_revenue_change_percent, estimated_monthly_revenue,
                confidence_level, reasoning, factors, status
            ) VALUES (
                :id, :station_id,
                :rec_price, :rec_night,
                :current_price,
                :avg_comp, :min_comp, :max_comp,
                :elec_cost,
                :est_change, :monthly_rev,
                :confidence, :reasoning, CAST(:factors AS jsonb), 'pending'
            )
        """), {
            "id": rec_id,
            "station_id": station_id,
            "rec_price": recommended["price"],
            "rec_night": recommended["price_night"],
            "current_price": current.get("tariff_per_kwh"),
            "avg_comp": comp.get("avg"),
            "min_comp": comp.get("min"),
            "max_comp": comp.get("max"),
            "elec_cost": ELECTRICITY_COST_KGS,
            "est_change": recommended.get("estimated_revenue_change"),
            "monthly_rev": monthly_est,
            "confidence": recommended["confidence"],
            "reasoning": reasoning,
            "factors": json.dumps(recommended.get("factors", {})),
        })
        self.db.commit()

        return rec_id
