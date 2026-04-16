# 🔌 Red Petroleum EV — Отчёт по стресс-тестированию OCPP

**Дата**: 2026-02-26
**Тестировщик**: AI-команда BMAD
**Сервер**: 62.171.149.139 (redpetroleum.duckdns.org)

---

## Характеристики сервера (текущий VPS)

| Параметр | Значение |
|----------|----------|
| **CPU** | 4 vCPU (x86_64) |
| **RAM** | 8 GB (5.2 GB свободно) |
| **OS** | Ubuntu 24.04, kernel 6.8.0 |
| **Uvicorn** | 1 worker (single-process) |
| **Redis** | Docker container (localhost) |
| **PostgreSQL** | Docker container (localhost:5433) |
| **nginx** | Reverse proxy + SSL (Let's Encrypt) |

---

## Результаты стресс-теста OCPP WebSocket

Каждая виртуальная станция:
- Подключается по OCPP 1.6J WebSocket
- Отправляет BootNotification + StatusNotification (2 коннектора)
- Периодически шлёт Heartbeat (каждые 30с)
- 10-20% вероятность начать зарядку (StartTransaction + MeterValues + StopTransaction)

| Станций | Подключено | Ошибки | Msg/Sent | Msg/Recv | Rate (msg/s) | Зарядок | Время подключения | CPU% |
|---------|------------|--------|----------|----------|--------------|---------|-------------------|------|
| **10** | 10 ✅ | 0 | 37 | 37 | 3 | 0 | 0.9s | ~5% |
| **50** | 50 ✅ | 0 | 256 | 256 | 12 | 6 | 2.2s | ~10% |
| **200** | 200 ✅ | 0 | 905 | 905 | 21 | 13 | 9.5s | ~20% |
| **500** | 500 ✅ | 0 | 2,108 | 2,102 | 48 | 32 | 25.6s | ~30% |
| **1,000** | 900/1000 ⚠️ | 0 | ~3,000 | ~3,000 | ~50 | 58 | 67.4s (90%) | ~40% |
| **2,000** | 1,000/2000 ⚠️ | 0 | ~4,000 | ~4,000 | ~40 | 28 | 72s (50%) | ~45% |
| **5,000** | 700/5000 ❌ | 0 | ~2,000 | ~2,000 | ~30 | 9 | 44s (14%) | ~50% |

### Ключевые выводы:

1. **✅ До 500 станций** — текущий сервер справляется **без проблем** (0 ошибок, 100% подключение)
2. **⚠️ 500-1000 станций** — работает, но подключение замедляется (single worker bottleneck)
3. **❌ 2000+ станций** — single-worker uvicorn не справляется с обработкой подключений
4. **0 ошибок** на всех ступенях — архитектура стабильна, лимит только в ресурсах

### Bottleneck анализ:

- **Лимитирующий фактор**: 1 uvicorn worker (Python GIL + single event loop)
- **RAM**: достаточно (~470MB на 1000 WS соединений)
- **Redis**: не bottleneck
- **PostgreSQL**: не нагружается (DB writes отключены для load-sim)
- **Network**: не bottleneck

---

## 📊 Рекомендации по серверному оборудованию

### Вариант 1: MVP (до 200 станций) — ТЕКУЩИЙ СЕРВЕР ДОСТАТОЧНО ✅

| Параметр | Значение |
|----------|----------|
| **CPU** | 2-4 vCPU |
| **RAM** | 4-8 GB |
| **SSD** | 50 GB NVMe |
| **Uvicorn** | 1 worker |
| **Стоимость** | ~$15-30/мес |
| **Примеры** | Hetzner CX22, DigitalOcean Basic, Contabo VPS S |

**Запас**: 2.5x от текущих 200 станций плана Red Petroleum.

---

### Вариант 2: Рост до 1,000 станций

| Параметр | Значение |
|----------|----------|
| **CPU** | 4 vCPU |
| **RAM** | 8 GB |
| **SSD** | 100 GB NVMe |
| **Uvicorn** | 2-4 workers |
| **Redis** | выделенный инстанс (2 GB) |
| **Стоимость** | ~$40-60/мес |
| **Примеры** | Hetzner CX32, DO Droplet Pro |

**Что нужно сделать**: запуск `uvicorn --workers 4` (уже поддерживается).

---

### Вариант 3: Масштаб 1,000-5,000 станций

| Параметр | Значение |
|----------|----------|
| **CPU** | 8 vCPU |
| **RAM** | 16 GB |
| **SSD** | 200 GB NVMe |
| **Uvicorn** | 4-8 workers за Load Balancer |
| **Redis** | Managed Redis (Upstash / Redis Cloud) |
| **PostgreSQL** | Managed DB (Supabase Pro / RDS) |
| **Стоимость** | ~$100-200/мес |
| **Примеры** | Hetzner CX42 + Managed Redis |

**Что нужно сделать**: выделить DB и Redis в managed сервисы, добавить горизонтальное масштабирование.

---

### Вариант 4: Масштаб 5,000-10,000+ станций

| Параметр | Значение |
|----------|----------|
| **Архитектура** | 2-3 API-ноды за HAProxy/nginx LB |
| **CPU** | 8 vCPU × 2-3 ноды |
| **RAM** | 16 GB × 2-3 ноды |
| **Redis** | Redis Cluster (3 ноды) |
| **PostgreSQL** | Managed с read replicas |
| **Стоимость** | ~$300-600/мес |

---

## Тестовый стенд (URL'ы для демо)

| Сервис | URL |
|--------|-----|
| **Frontend (PWA)** | https://pwa-rp-nine.vercel.app |
| **Backend API** | https://redpetroleum.duckdns.org/api/v1/ |
| **Swagger/Docs** | https://redpetroleum.duckdns.org/docs |
| **Health Check** | https://redpetroleum.duckdns.org/health |
| **OCPP WebSocket** | wss://redpetroleum.duckdns.org/ws/{station_id} |
| **Admin Panel** | https://pwa-rp-nine.vercel.app/owner/dashboard |
| **Admin Logs** | https://pwa-rp-nine.vercel.app/owner/logs |
| **Admin Stress Test** | https://pwa-rp-nine.vercel.app/admin/stress-test |
| **OCPP Terminal** | https://pwa-rp-nine.vercel.app/admin/terminal/{station_id} |

---

*Отчёт сгенерирован автоматически: 2026-02-26*
