# 📱 Push Notifications API - Примеры использования

**Дата:** 2025-11-18
**Версия API:** v1.3.0
**Base URL:** `https://ocpp.redp.asystem.kg/api/v1/notifications`

---

## 📋 Оглавление

1. [Получение VAPID Public Key](#1-получение-vapid-public-key)
2. [Подписка на Push Notifications](#2-подписка-на-push-notifications)
3. [Отписка от Push Notifications](#3-отписка-от-push-notifications)
4. [Тестовое уведомление](#4-тестовое-уведомление)
5. [Интеграция с PWA](#5-интеграция-с-pwa)
6. [Обработка ошибок](#6-обработка-ошибок)

---

## 1. Получение VAPID Public Key

### Endpoint
```
GET /api/v1/notifications/vapid-public-key
```

### Описание
Получить VAPID public key для подписки на push notifications. Это публичный endpoint, аутентификация **НЕ требуется**.

### Request

```bash
curl -X GET "https://ocpp.redp.asystem.kg/api/v1/notifications/vapid-public-key" \
  -H "Accept: application/json"
```

### Response (200 OK)

```json
{
  "success": true,
  "data": {
    "public_key": "BEkG...4Qw="
  }
}
```

### Поля ответа

| Поле | Тип | Описание |
|------|-----|----------|
| `success` | boolean | Статус операции |
| `data.public_key` | string | VAPID public key в base64url формате |

### Примеры использования

**JavaScript (PWA):**
```javascript
const response = await fetch('/api/v1/notifications/vapid-public-key');
const { data } = await response.json();
const vapidPublicKey = data.public_key;

// Использовать для подписки
const registration = await navigator.serviceWorker.ready;
const subscription = await registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: vapidPublicKey
});
```

**Python:**
```python
import requests

response = requests.get('https://ocpp.redp.asystem.kg/api/v1/notifications/vapid-public-key')
data = response.json()
vapid_public_key = data['data']['public_key']
print(f"VAPID Public Key: {vapid_public_key}")
```

---

## 2. Подписка на Push Notifications

### Endpoint
```
POST /api/v1/notifications/subscribe
```

### Описание
Зарегистрировать PushSubscription от браузера для получения уведомлений. **Требуется JWT аутентификация**.

### Request Headers

```
Authorization: Bearer <JWT_TOKEN>
Content-Type: application/json
```

### Request Body

```json
{
  "subscription": {
    "endpoint": "https://fcm.googleapis.com/fcm/send/c1KrmpTuRm...",
    "keys": {
      "p256dh": "BJ3l7ZH...tQxw=",
      "auth": "k8JV8yQ...Lrg=="
    }
  },
  "user_type": "client"
}
```

### Поля запроса

| Поле | Тип | Обязательное | Описание |
|------|-----|--------------|----------|
| `subscription.endpoint` | string | ✅ | Push service endpoint URL от браузера |
| `subscription.keys.p256dh` | string | ✅ | P256DH public key (base64) |
| `subscription.keys.auth` | string | ✅ | Auth secret (base64) |
| `user_type` | string | ✅ | Тип пользователя: `"client"` или `"owner"` |

### Response (200 OK) - Новая подписка

```json
{
  "success": true,
  "message": "Push subscription registered successfully",
  "subscription_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

### Response (200 OK) - Обновление существующей

```json
{
  "success": true,
  "message": "Push subscription updated successfully",
  "subscription_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

### Примеры использования

**curl:**
```bash
curl -X POST "https://ocpp.redp.asystem.kg/api/v1/notifications/subscribe" \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..." \
  -H "Content-Type: application/json" \
  -d '{
    "subscription": {
      "endpoint": "https://fcm.googleapis.com/fcm/send/c1KrmpTuRm...",
      "keys": {
        "p256dh": "BJ3l7ZH...tQxw=",
        "auth": "k8JV8yQ...Lrg=="
      }
    },
    "user_type": "client"
  }'
```

**JavaScript (PWA):**
```javascript
// 1. Получить VAPID key
const vapidResponse = await fetch('/api/v1/notifications/vapid-public-key');
const { data } = await vapidResponse.json();

// 2. Подписаться на push
const registration = await navigator.serviceWorker.ready;
const subscription = await registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: data.public_key
});

// 3. Отправить subscription на сервер
const response = await fetch('/api/v1/notifications/subscribe', {
    method: 'POST',
    headers: {
        'Authorization': `Bearer ${jwtToken}`,
        'Content-Type': 'application/json'
    },
    body: JSON.stringify({
        subscription: subscription.toJSON(),
        user_type: 'client'  // или 'owner'
    })
});

const result = await response.json();
console.log('Subscription ID:', result.subscription_id);
```

**Python:**
```python
import requests

jwt_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."

payload = {
    "subscription": {
        "endpoint": "https://fcm.googleapis.com/fcm/send/c1KrmpTuRm...",
        "keys": {
            "p256dh": "BJ3l7ZH...tQxw=",
            "auth": "k8JV8yQ...Lrg=="
        }
    },
    "user_type": "client"
}

response = requests.post(
    'https://ocpp.redp.asystem.kg/api/v1/notifications/subscribe',
    headers={
        'Authorization': f'Bearer {jwt_token}',
        'Content-Type': 'application/json'
    },
    json=payload
)

print(response.json())
```

---

## 3. Отписка от Push Notifications

### Endpoint
```
POST /api/v1/notifications/unsubscribe
```

### Описание
Удалить PushSubscription для указанного endpoint. **Требуется JWT аутентификация**.

### Request Headers

```
Authorization: Bearer <JWT_TOKEN>
Content-Type: application/json
```

### Request Body

```json
{
  "endpoint": "https://fcm.googleapis.com/fcm/send/c1KrmpTuRm..."
}
```

### Поля запроса

| Поле | Тип | Обязательное | Описание |
|------|-----|--------------|----------|
| `endpoint` | string | ✅ | Push service endpoint URL для удаления |

### Response (200 OK) - Успешно удалено

```json
{
  "success": true,
  "message": "Push subscription removed successfully"
}
```

### Response (200 OK) - Подписка не найдена

```json
{
  "success": false,
  "message": "Push subscription not found"
}
```

### Примеры использования

**curl:**
```bash
curl -X POST "https://ocpp.redp.asystem.kg/api/v1/notifications/unsubscribe" \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..." \
  -H "Content-Type: application/json" \
  -d '{
    "endpoint": "https://fcm.googleapis.com/fcm/send/c1KrmpTuRm..."
  }'
```

**JavaScript (PWA):**
```javascript
// 1. Получить текущую subscription
const registration = await navigator.serviceWorker.ready;
const subscription = await registration.pushManager.getSubscription();

if (subscription) {
    // 2. Отписаться на клиенте
    await subscription.unsubscribe();

    // 3. Удалить subscription на сервере
    const response = await fetch('/api/v1/notifications/unsubscribe', {
        method: 'POST',
        headers: {
            'Authorization': `Bearer ${jwtToken}`,
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            endpoint: subscription.endpoint
        })
    });

    const result = await response.json();
    console.log('Unsubscribed:', result.message);
}
```

**Python:**
```python
import requests

jwt_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
endpoint = "https://fcm.googleapis.com/fcm/send/c1KrmpTuRm..."

response = requests.post(
    'https://ocpp.redp.asystem.kg/api/v1/notifications/unsubscribe',
    headers={
        'Authorization': f'Bearer {jwt_token}',
        'Content-Type': 'application/json'
    },
    json={'endpoint': endpoint}
)

print(response.json())
```

---

## 4. Тестовое уведомление

### Endpoint
```
POST /api/v1/notifications/test
```

### Описание
Отправить тестовое push notification на все subscriptions текущего пользователя. **Требуется JWT аутентификация**.

### Request Headers

```
Authorization: Bearer <JWT_TOKEN>
```

### Request Body
Не требуется

### Response (200 OK) - Успешно отправлено

```json
{
  "success": true,
  "sent_to": 2,
  "message": "Test notification sent to 2 device(s)"
}
```

### Response (200 OK) - Нет активных subscriptions

```json
{
  "success": false,
  "sent_to": 0,
  "message": "No active subscriptions found"
}
```

### Поля ответа

| Поле | Тип | Описание |
|------|-----|----------|
| `success` | boolean | Статус операции |
| `sent_to` | integer | Количество устройств, которым отправлено уведомление |
| `message` | string | Описание результата |

### Примеры использования

**curl:**
```bash
curl -X POST "https://ocpp.redp.asystem.kg/api/v1/notifications/test" \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
```

**JavaScript:**
```javascript
const response = await fetch('/api/v1/notifications/test', {
    method: 'POST',
    headers: {
        'Authorization': `Bearer ${jwtToken}`
    }
});

const result = await response.json();
console.log(`Test notification sent to ${result.sent_to} device(s)`);
```

**Python:**
```python
import requests

jwt_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."

response = requests.post(
    'https://ocpp.redp.asystem.kg/api/v1/notifications/test',
    headers={'Authorization': f'Bearer {jwt_token}'}
)

result = response.json()
print(f"Sent to {result['sent_to']} device(s)")
```

---

## 5. Интеграция с PWA

### Полный пример подписки на push notifications

```javascript
// service-worker.js
self.addEventListener('push', function(event) {
    const data = event.data ? event.data.json() : {};

    const title = data.title || 'RedPetroleum Notification';
    const options = {
        body: data.body || 'You have a new notification',
        icon: data.icon || '/logo-192.png',
        badge: data.badge || '/logo-96.png',
        data: data.data || {},
        actions: data.actions || [],
        tag: data.tag || 'default',
        requireInteraction: data.requireInteraction || false
    };

    event.waitUntil(
        self.registration.showNotification(title, options)
    );
});

self.addEventListener('notificationclick', function(event) {
    event.notification.close();

    const urlToOpen = event.notification.data.url || '/';

    event.waitUntil(
        clients.openWindow(urlToOpen)
    );
});
```

```javascript
// app.js
async function subscribeUserToPush() {
    try {
        // 1. Проверить поддержку Service Worker
        if (!('serviceWorker' in navigator)) {
            throw new Error('Service Workers not supported');
        }

        // 2. Проверить поддержку Push API
        if (!('PushManager' in window)) {
            throw new Error('Push API not supported');
        }

        // 3. Зарегистрировать Service Worker
        const registration = await navigator.serviceWorker.register('/service-worker.js');
        await navigator.serviceWorker.ready;

        // 4. Запросить разрешение на уведомления
        const permission = await Notification.requestPermission();
        if (permission !== 'granted') {
            throw new Error('Permission not granted for notifications');
        }

        // 5. Получить VAPID public key
        const vapidResponse = await fetch('/api/v1/notifications/vapid-public-key');
        const { data } = await vapidResponse.json();

        // 6. Подписаться на push
        const subscription = await registration.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: data.public_key
        });

        // 7. Отправить subscription на сервер
        const jwtToken = localStorage.getItem('jwt_token'); // или из вашего хранилища
        const userType = localStorage.getItem('user_type') || 'client';

        const subscribeResponse = await fetch('/api/v1/notifications/subscribe', {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${jwtToken}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                subscription: subscription.toJSON(),
                user_type: userType
            })
        });

        const result = await subscribeResponse.json();
        console.log('✅ Subscribed to push notifications:', result.subscription_id);

        return subscription;
    } catch (error) {
        console.error('❌ Failed to subscribe to push notifications:', error);
        throw error;
    }
}

// Использование
subscribeUserToPush()
    .then(subscription => console.log('Push subscription active'))
    .catch(error => console.error('Push subscription failed:', error));
```

### Проверка статуса подписки

```javascript
async function checkPushSubscription() {
    const registration = await navigator.serviceWorker.ready;
    const subscription = await registration.pushManager.getSubscription();

    if (subscription) {
        console.log('✅ Already subscribed to push notifications');
        return subscription;
    } else {
        console.log('⚠️ Not subscribed to push notifications');
        return null;
    }
}
```

---

## 6. Обработка ошибок

### Коды ошибок HTTP

| Код | Описание | Решение |
|-----|----------|---------|
| `401 Unauthorized` | Отсутствует или невалидный JWT token | Проверить наличие и валидность токена в заголовке Authorization |
| `422 Unprocessable Entity` | Невалидные данные в request body | Проверить формат subscription и обязательные поля |
| `500 Internal Server Error` | Внутренняя ошибка сервера | Повторить запрос позже или связаться с поддержкой |

### Пример обработки ошибок

**JavaScript:**
```javascript
async function subscribeWithErrorHandling() {
    try {
        const response = await fetch('/api/v1/notifications/subscribe', {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${jwtToken}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                subscription: subscription.toJSON(),
                user_type: 'client'
            })
        });

        if (!response.ok) {
            // HTTP error
            const errorData = await response.json();
            throw new Error(`HTTP ${response.status}: ${errorData.detail || errorData.message}`);
        }

        const result = await response.json();

        if (!result.success) {
            // Application error
            throw new Error(result.message);
        }

        return result;
    } catch (error) {
        console.error('Subscription error:', error);

        // Показать пользователю friendly сообщение
        if (error.message.includes('401')) {
            alert('Требуется повторная авторизация');
        } else if (error.message.includes('422')) {
            alert('Невалидные данные подписки');
        } else {
            alert('Не удалось подписаться на уведомления. Попробуйте позже.');
        }

        throw error;
    }
}
```

### Типичные ошибки и решения

**Ошибка: "Permission not granted for notifications"**
- **Причина:** Пользователь отклонил разрешение на уведомления
- **Решение:** Попросить пользователя вручную разрешить уведомления в настройках браузера

**Ошибка: "Push API not supported"**
- **Причина:** Браузер не поддерживает Push API
- **Решение:** Показать сообщение о необходимости использовать современный браузер (Chrome, Firefox, Edge)

**Ошибка: "Service Workers not supported"**
- **Причина:** Браузер не поддерживает Service Workers
- **Решение:** Показать сообщение о необходимости обновить браузер

**Ошибка: 401 Unauthorized**
- **Причина:** JWT token истек или невалиден
- **Решение:** Обновить токен через refresh token или попросить пользователя войти заново

---

## 📝 Примечания

### Уведомления для клиентов (user_type: "client")

Backend автоматически отправляет следующие уведомления:

1. **Charging Started** - зарядка началась
   - Триггер: успешный запуск зарядки (`POST /api/v1/charging/start`)
   - Данные: `session_id`, `station_id`, `connector_id`

2. **Charging Completed** - зарядка завершена
   - Триггер: успешная остановка зарядки (`POST /api/v1/charging/stop`)
   - Данные: `session_id`, `energy_kwh`, `amount`

### Уведомления для владельцев (user_type: "owner")

Backend автоматически отправляет следующие уведомления:

1. **New Session** - новая зарядка на вашей станции
   - Триггер: успешный запуск зарядки (`POST /api/v1/charging/start`)
   - Данные: `session_id`, `station_id`, `connector_id`

2. **Session Completed** - зарядка завершена на вашей станции
   - Триггер: успешная остановка зарядки (`POST /api/v1/charging/stop`)
   - Данные: `session_id`, `station_id`, `energy_kwh`, `amount`

### Graceful Degradation

Backend реализует graceful degradation pattern:
- Push notification failures **НЕ блокируют** основной application flow
- Ошибки push логируются как warnings
- Критические операции (charging start/stop) всегда завершаются успешно

### Автоматическая очистка

Backend автоматически удаляет невалидные subscriptions:
- При получении `410 Gone` от push service
- При получении `404 Not Found` от push service
- Subscription удаляется из БД для предотвращения повторных попыток

---

## 🔗 Полезные ссылки

- [Web Push API](https://developer.mozilla.org/en-US/docs/Web/API/Push_API)
- [VAPID Specification (RFC 8292)](https://datatracker.ietf.org/doc/html/rfc8292)
- [Service Worker API](https://developer.mozilla.org/en-US/docs/Web/API/Service_Worker_API)
- [Notification API](https://developer.mozilla.org/en-US/docs/Web/API/Notifications_API)

---

**Генерация:** 🤖 Generated with [Claude Code](https://claude.com/claude-code)
