# CORS Fix — Разрешить pwa-rp.vercel.app

> **Дата**: 2026-02-14
> **Автор**: Ruslan
> **Для**: Эрмек (backend)
> **Приоритет**: БЛОКЕР — без этого фронт не работает на проде

---

## Проблема

Frontend задеплоен на **https://pwa-rp.vercel.app**

Все API запросы к `ocpp.redp.asystem.kg` блокируются браузером:

```
Access to fetch at 'https://ocpp.redp.asystem.kg/api/v1/locations?include_stations=true'
from origin 'https://pwa-rp.vercel.app' has been blocked by CORS policy:
Response to preflight request doesn't pass access control check:
No 'Access-Control-Allow-Origin' header is present on the requested resource.
```

Затронутые эндпоинты (все):
- `GET /api/v1/locations?include_stations=true` — карта станций
- `GET /api/v1/profile` — профиль пользователя
- `GET /api/v1/history/charging` — история зарядок
- `GET /api/v1/history/transactions` — история транзакций
- `POST /api/v1/auth/otp/send` — отправка OTP
- Все остальные эндпоинты

---

## Решение

### Вариант 1: Env-переменная на сервере (рекомендуется)

На сервере `ocpp.redp.asystem.kg` добавить `https://pwa-rp.vercel.app` в переменную окружения `CORS_ORIGINS`:

```bash
CORS_ORIGINS=https://pwa-rp.vercel.app,https://redp.asystem.kg,https://redp.asystem.kg,https://ocpp.redp.asystem.kg,http://localhost:3000,http://localhost:9210
```

После чего перезапустить сервер.

Код уже поддерживает это — менять ничего не нужно:
- `app/core/config.py:45` — читает `CORS_ORIGINS` из env
- `app/main.py:376` — парсит список и передаёт в `CORSMiddleware`
- `app/core/security_middleware.py:264` — fail-safe CORS в SecurityMiddleware

### Вариант 2: Изменить дефолт в config.py

Если нет доступа к env-переменным на сервере, можно добавить домен в дефолтное значение:

**Файл**: `app/core/config.py`, строка 45-48

Было:
```python
CORS_ORIGINS: str = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:3000,http://localhost:9210,https://redp.asystem.kg,https://redp.asystem.kg,https://ocpp.redp.asystem.kg"
)
```

Стало:
```python
CORS_ORIGINS: str = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:3000,http://localhost:9210,https://redp.asystem.kg,https://redp.asystem.kg,https://ocpp.redp.asystem.kg,https://pwa-rp.vercel.app"
)
```

Также обновить `CSRF_TRUSTED_ORIGINS` (строка 50-53):
```python
CSRF_TRUSTED_ORIGINS: str = os.getenv(
    "CSRF_TRUSTED_ORIGINS",
    "https://redp.asystem.kg,https://redp.asystem.kg,https://ocpp.redp.asystem.kg,http://localhost:3000,https://pwa-rp.vercel.app"
)
```

---

## Как проверить

После добавления origin и рестарта сервера:

```bash
# Проверить что CORS работает (должен вернуть Access-Control-Allow-Origin)
curl -I -X OPTIONS \
  -H "Origin: https://pwa-rp.vercel.app" \
  -H "Access-Control-Request-Method: GET" \
  https://ocpp.redp.asystem.kg/api/v1/locations

# Ожидаемый ответ (среди заголовков):
# Access-Control-Allow-Origin: https://pwa-rp.vercel.app
# Access-Control-Allow-Credentials: true
# Access-Control-Allow-Methods: GET, POST, PUT, DELETE, PATCH, OPTIONS
```

Или открыть https://pwa-rp.vercel.app в браузере — карта должна загрузить станции.

---

## Как работает CORS в нашем backend (справка)

```
Запрос от браузера
       │
       ▼
┌─────────────────────┐
│  SecurityMiddleware  │ ← preflight OPTIONS обработка (строка 262)
│  security_middleware │   проверяет origin ∈ CORS_ORIGINS
│  .py                │   возвращает 204 с CORS заголовками
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  CORSMiddleware     │ ← FastAPI стандартный CORS (строка 408)
│  (main.py)          │   allow_origins = cors_origins
│                     │   allow_credentials = True
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  SecurityMiddleware  │ ← fail-safe: добавляет CORS заголовки
│  (response phase)   │   если их нет в ответе (строка 303)
└─────────────────────┘
```

Три уровня CORS обработки — достаточно добавить origin в `CORS_ORIGINS`.

---

## Контекст

- Frontend: React PWA (`pwa-rp/`) на Vercel
- Backend: FastAPI (`ocpp-rp/backend/`) на `ocpp.redp.asystem.kg`
- API URL в фронте: `VITE_API_URL=https://ocpp.redp.asystem.kg` (hardcoded fallback)
- В dev-режиме фронт использует Vite proxy → CORS не нужен
- В проде (Vercel) — прямые запросы → нужен CORS
